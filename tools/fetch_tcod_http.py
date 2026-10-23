#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCOD 增量下载器（HTTP，keep-alive + 自适应压速 + 断点续传 + 熔断）。

为什么不用 wget / svn：
  * wget -r -P4 的递归并发会触发站点限流：先 403，随后直接丢弃 HTTP 响应
    （TCP 握手成功但零字节返回，封禁实测持续 10 分钟以上）；
  * svn 的 svn:// 走 3690 端口，本机网络到该端口不通，站点也没有 HTTP-DAV 端点。
所以自己控制节奏：少量长连接 + 每请求固定间隔 + 见到 403/429 立刻全局退避。

特性：
  * 每线程一条持久连接（keep-alive），避免 1 次请求 1 次 TCP 握手；
  * 自适应：连续成功则逐步减小间隔，出现 403/429/超时则指数退避并全局暂停；
  * 断点续传：已存在且尺寸接近远端估算的文件直接跳过（--resume，默认开）；
  * 熔断：连续 N 次彻底失败即退出，把状态留给下一轮；
  * 原子落盘：先写 .part 再 rename，不会留下半截 CIF。

用法:
  python tools/fetch_tcod_http.py --list sync_logs/sync20261019/tcod_missing.txt \
      --remote-inv sync_logs/sync20261019/remote_inv_tcod.tsv \
      --root D:/Project/XRD/PolyXRD-DataBase/TCOD --workers 4 --delay 0.02
"""
from __future__ import annotations

import argparse
import http.client
import os
import sys
import threading
import time

HOST = "www.crystallography.net"
HOSTIP = ""               # 启动时解析一次，之后按 IP 连接（规避 DNS 抖动）
PREFIX = "/tcod/cif/"
UA = "Mozilla/5.0 (compatible; tcod-mirror-sync/2.0)"


def resolve_host(host: str) -> str:
    import socket
    for i in range(6):
        try:
            infos = socket.getaddrinfo(host, 80, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                return infos[0][4][0]
        except OSError:
            pass
        time.sleep(3)
    return ""


class Throttle:
    """全局节流器：所有线程共享一个"当前最小间隔"和一个"暂停到某时刻"。"""

    def __init__(self, delay: float, max_delay: float, pause: float = 0.3):
        self._lock = threading.Lock()
        self.delay = delay
        self.base = delay
        self.max_delay = max_delay
        self.pause = pause
        self.pause_until = 0.0
        self.n_403 = 0
        self.n_err = 0

    def wait(self) -> None:
        with self._lock:
            d = self.delay
            pu = self.pause_until
        now = time.time()
        if pu > now:
            time.sleep(min(pu - now, 60.0))
        if d > 0:
            time.sleep(d)

    def ok(self) -> None:
        with self._lock:
            if self.delay > self.base:
                self.delay = max(self.base, self.delay * 0.97)

    def limited(self) -> None:
        with self._lock:
            self.n_403 += 1
            self.delay = min(max(self.delay * 2, 0.25), self.max_delay)
            # 真限流才值得全局暂停；连接抖动只用很短停顿，否则 32 线程会被一次抖动全冻住
            self.pause_until = max(self.pause_until, time.time() + max(self.pause * 6, 2.0))

    def down(self) -> None:
        with self._lock:
            self.n_err += 1
            self.delay = min(max(self.delay * 1.5, 0.2), self.max_delay)
            self.pause_until = max(self.pause_until, time.time() + self.pause)


class Aborted(RuntimeError):
    pass


class Fetcher:
    def __init__(self, th: Throttle, timeout: float, abort_after: int):
        self.th = th
        self.timeout = timeout
        self.abort_after = abort_after
        self.conn: http.client.HTTPConnection | None = None
        self.streak = 0
        self.lock = threading.Lock()
        self.dead = False

    def _connect(self) -> http.client.HTTPConnection:
        if self.conn is None:
            # 优先用启动时解析好的 IP，避免每建一次连接都走一次 DNS
            # （本机 DNS 对国外域名会间歇性解析失败，实测超时 10 s 以上）
            self.conn = http.client.HTTPConnection(HOSTIP or HOST, 80,
                                                   timeout=self.timeout)
        return self.conn

    def _close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def get(self, path: str, tries: int = 4) -> bytes | None:
        last = None
        for i in range(tries):
            if self.dead:
                raise Aborted("其它线程已判定服务端不可用")
            self.th.wait()
            try:
                c = self._connect()
                c.request("GET", path, headers={
                    "Host": HOST,             # 用 IP 连接时仍需正确的 Host 头
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Connection": "keep-alive",
                })
                r = c.getresponse()
                if r.status == 200:
                    data = r.read()
                    if r.will_close:          # 服务端要关连接：主动丢弃，下次重连
                        self._close()
                    self.th.ok()
                    with self.lock:
                        self.streak = 0
                    return data
                if r.status in (403, 429):
                    r.read()
                    self.th.limited()
                    self._close()
                    last = "HTTP %d" % r.status
                    time.sleep(min(2 ** i, 20))
                    continue
                if r.status in (500, 502, 503, 504):
                    r.read()
                    self.th.down()
                    self._close()
                    last = "HTTP %d" % r.status
                    time.sleep(min(3 * (i + 1), 20))
                    continue
                # 404 等：明确不存在，不重试
                r.read()
                return None
            except (http.client.HTTPException, OSError, TimeoutError) as ex:
                # 连接被复用坏了 / 被对端关掉：重连即可，不该按"服务端故障"长退避
                self._close()
                last = type(ex).__name__
                if i == 0:
                    time.sleep(0.05)
                else:
                    self.th.down()
                    time.sleep(min(1.5 * i, 8))
        with self.lock:
            self.streak += 1
            if self.streak >= self.abort_after:
                self.dead = True
                raise Aborted("连续 %d 个文件彻底失败（最后错误 %s）"
                              % (self.streak, last))
        return None


def main() -> int:
    global HOSTIP
    # 默认路径锚定数据根，不依赖当前工作目录（本脚本住在 <数据根>/tools/ 下）。
    data_root = os.environ.get("XRD_DATA_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=os.path.join(
        data_root, "sync_logs", "sync20261019", "tcod_missing.txt"))
    ap.add_argument("--remote-inv", default=os.path.join(
        data_root, "sync_logs", "sync20261019", "remote_inv_tcod.tsv"))
    ap.add_argument("--root", default=os.path.join(data_root, "TCOD"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.02)
    ap.add_argument("--max-delay", type=float, default=3.0)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--abort-after", type=int, default=30)
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--pause", type=float, default=0.3,
                    help="单次连接抖动导致的全局停顿秒数（默认 0.3；真 403 会自动放大到 2 s+）")
    ap.add_argument("--ip", default="", help="直接指定服务端 IP（跳过 DNS）")
    args = ap.parse_args()

    HOSTIP = args.ip or resolve_host(HOST)
    print("目标 %s (%s)" % (HOST, HOSTIP or "DNS 未解析，直接按域名连接"), flush=True)

    approx: dict[str, int] = {}
    with open(args.remote_inv, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p, _, s = line.rstrip("\n").rpartition("\t")
            if p:
                try:
                    approx[p] = int(s)
                except ValueError:
                    approx[p] = -1

    todo = [l.strip() for l in open(args.list, encoding="utf-8") if l.strip()]
    print("待补 %d 个文件；%d 线程；基础间隔 %.3fs"
          % (len(todo), args.workers, args.delay), flush=True)

    th = Throttle(args.delay, args.max_delay, args.pause)
    q: list[tuple[int, str]] = list(enumerate(todo))
    qi = 0
    qlock = threading.Lock()

    n_ok = 0
    n_skip = 0
    n_404 = 0
    n_fail = 0
    failed: list[str] = []
    stats_lock = threading.Lock()
    aborted = threading.Event()
    t0 = time.time()

    def worker() -> None:
        nonlocal qi, n_ok, n_skip, n_404, n_fail
        f = Fetcher(th, args.timeout, args.abort_after)
        while not aborted.is_set():
            with qlock:
                if qi >= len(q):
                    return
                idx, rel = q[qi]
                qi += 1
            dst = os.path.join(args.root, rel.replace("/", os.sep))
            exp = approx.get(rel, -1)
            if not args.no_resume and os.path.exists(dst):
                sz = os.path.getsize(dst)
                if sz > 0 and (exp <= 0 or abs(sz - exp) / exp <= args.tol):
                    with stats_lock:
                        n_skip += 1
                    continue
            try:
                data = f.get(PREFIX + rel)
            except Aborted as ex:
                aborted.set()
                print("\n[熔断] %s" % ex, flush=True)
                return
            if data is None:
                with stats_lock:
                    n_404 += 1
                    failed.append(rel)
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                tmp = dst + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, dst)
                with stats_lock:
                    n_ok += 1
                    done = n_ok + n_skip + n_404
                    if done // 500 != (done - 1) // 500:
                        el = time.time() - t0
                        rate = (n_ok + n_skip) / max(el, 1e-9)
                        eta = (len(todo) - done - n_fail) / max(rate, 1e-9) / 60
                        print("  %d/%d  新下 %d 跳过 %d 失败 %d | 均速 %.1f 文件/s | "
                              "剩余约 %.0f 分钟 | 间隔 %.2fs 抖动 %d 次 限流 %d 次"
                              % (done, len(todo), n_ok, n_skip, n_fail + n_404,
                                 rate, eta, th.delay, th.n_err, th.n_403), flush=True)
            except OSError as ex:
                with stats_lock:
                    n_fail += 1
                    failed.append(rel)
                print("  写入失败 %s: %s" % (rel, ex), flush=True)
        f._close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    el = time.time() - t0
    print("-" * 60)
    print("新下载 %d，已存在跳过 %d，404/无响应 %d，写入失败 %d，用时 %.1f 分钟"
          % (n_ok, n_skip, n_404, n_fail, el / 60))
    if th.n_403 or th.n_err:
        print("期间 抖动 %d 次、限流(403/429) %d 次，最终间隔 %.2fs、停顿 %.2fs"
              % (th.n_err, th.n_403, th.delay, th.pause))
    if aborted.is_set():
        print("状态: 已熔断（服务端限流），剩余条目请重跑本脚本继续")
    if failed:
        part = args.list + ".retry"
        with open(part, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(failed) + "\n")
        print("未完成清单 -> %s (%d 条)" % (part, len(failed)))
    else:
        print("全部完成")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
