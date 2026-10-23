#!/usr/bin/env python3
"""独立核验 TCOD：直接爬 Apache 目录索引，拿到远端权威清单（文件名 + 尺寸）。

为什么不继续用 wget 日志：
  * wget 日志把"先 403、后重试成功"的请求也记成 ERROR 403，无法区分"真缺"与"已恢复"；
  * 日志覆盖率本身又依赖爬取没被 403 截断 —— 已实测 40/12 首轮爬取被截断，
    只提到 400 个文件却照样打印 FINISHED。
  所以最可靠的做法是自己把目录索引爬一遍。

索引实际格式（Apache mod_autoindex，注意与常见文档不同）：
  <tr><td ...><img ...></td><td><a href="40127500.cif">40127500.cif</a></td>
      <td align="right">2026-10-13 10:16  </td><td align="right">3.4K</td><td>&nbsp;</td></tr>
  日期是 YYYY-MM-DD hh:mm，尺寸是**人类可读**的 3.4K / 5.0M —— 只能做粗粒度校验。

用法:
  python tools/crawl_tcod_http.py --out sync_logs/remote_inv_tcod.tsv --par 5

注意（2026-10-19 教训）: 服务端对密集请求会先 403、再直接丢弃 HTTP 响应
（TCP 握手成功但零字节返回，实测封禁持续 10 分钟以上）。所以默认并发降到 2，
并加熔断：连续 ABORT_AFTER 次彻底失败即中止，不把封禁时间拖长。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://www.crystallography.net/tcod/cif/"
UA = "Mozilla/5.0 (compatible; tcod-mirror-verify/1.0)"
RE_HREF = re.compile(r'href="([^"]+)"')
RE_CIFLINK = re.compile(r'<a href="([^"]+\.cif)">')
RE_CELL = re.compile(r'<td align="right">\s*([^<]*?)\s*</td>')

# ---- 运行参数（由 main() 覆盖） ----
TIMEOUT = 60.0
TRIES = 5
ABORT_AFTER = 12          # 连续这么多次彻底失败就熔断退出（服务端已不可达/已封 IP）
DELAY = 0.0               # 每次请求前的固定间隔（秒），用于压速防封


class RemoteDown(RuntimeError):
    """连续失败过多，判定服务端不可用。"""


_streak = 0
_lock = None
_n_ok = 0
_n_fail = 0


def _bump(ok: bool) -> None:
    global _streak, _n_ok, _n_fail
    with _lock:
        _n_ok += ok
        _n_fail += (not ok)
        _streak = 0 if ok else _streak + 1
        if _streak >= ABORT_AFTER:
            raise RemoteDown("连续 %d 次请求失败，中止（服务端不可达或 IP 被封）"
                             % _streak)


def get(url: str, tries: int | None = None) -> str | None:
    tries = TRIES if tries is None else tries
    if DELAY > 0:
        time.sleep(DELAY)
    for i in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=TIMEOUT) as r:
                txt = r.read().decode("utf-8", "replace")
            _bump(True)
            return txt
        except HTTPError as e:
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))          # 退避，别把限流放大
                continue
            _bump(False)
            return None
        except (URLError, TimeoutError, OSError):
            time.sleep(1.0 * (i + 1))
    _bump(False)
    return None


def human_to_bytes(txt: str) -> int:
    """'3.4K' -> 3481 (近似；K=1024 为 Apache 惯例)。返回 -1 表示未知。"""
    txt = txt.strip()
    if not txt or txt == "-":
        return -1
    m = re.match(r"([\d.]+)\s*([KMG]?)", txt)
    if not m:
        return -1
    v = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]
    return int(round(v * mult))


def parse_index(html: str, rel: str):
    """返回 (子目录名列表, [(relpath, 近似字节数)] 列表)。"""
    dirs, files = [], []
    if not html:
        return dirs, files
    for line in html.splitlines():
        mc = RE_CIFLINK.search(line)
        if mc:
            name = mc.group(1)
            cells = RE_CELL.findall(line)
            size = human_to_bytes(cells[1]) if len(cells) > 1 else -1
            files.append((f"{rel}/{name}" if rel else name, size))
            continue
        m = RE_HREF.search(line)
        if m:
            h = m.group(1)
            if h.startswith("?") or h.startswith("/") or h.startswith(".."):
                continue
            if h.endswith("/"):
                dirs.append(h.rstrip("/"))
            elif not rel:
                files.append((h, -1))
    return dirs, files


def main() -> int:
    global TIMEOUT, TRIES, ABORT_AFTER, DELAY, _lock
    # 默认输出锚定数据根，不依赖当前工作目录（本脚本住在 <数据根>/tools/ 下）。
    data_root = os.environ.get("XRD_DATA_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(data_root, "sync_logs", "remote_inv_tcod.tsv"))
    ap.add_argument("--dirs-out",
                    default=os.path.join(data_root, "sync_logs", "remote_dirs_tcod.txt"))
    ap.add_argument("--par", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--abort-after", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.08,
                    help="每次请求前的固定间隔秒数（压速防封，默认 0.08）")
    args = ap.parse_args()

    TIMEOUT = args.timeout
    TRIES = args.tries
    ABORT_AFTER = args.abort_after
    DELAY = args.delay
    _lock = threading.Lock()

    try:
        return _crawl(args)
    except RemoteDown as ex:
        print("\n[熔断] %s" % ex, flush=True)
        print("[熔断] 成功 %d 次 / 失败 %d 次" % (_n_ok, _n_fail), flush=True)
        print("ABORTED", flush=True)
        return 3


def _crawl(args) -> int:
    print("第 1 层 ...", flush=True)
    top = get(BASE)
    if top is None:
        print("无法访问根目录")
        return 2
    lvl1, root_files = parse_index(top, "")
    print(f"  顶层: {lvl1}", flush=True)

    # 第 2 层
    all_dirs: list[str] = []
    files: list[tuple[str, int]] = list(root_files)
    with ThreadPoolExecutor(max_workers=args.par) as ex:
        res1 = list(ex.map(lambda d: (d, parse_index(get(BASE + d + "/"), d)), lvl1))
    work2 = []
    for d1, (d2s, fs) in res1:
        files.extend(fs)
        all_dirs.append(d1)
        for d2 in d2s:
            work2.append(f"{d1}/{d2}")
    print(f"第 2 层目录 {len(work2)} 个 ...", flush=True)

    # 第 3 层：拿到叶子目录
    with ThreadPoolExecutor(max_workers=args.par) as ex:
        res2 = list(ex.map(lambda r: (r, parse_index(get(BASE + r + "/"), r)), work2))
    leaves, parents_with_files = [], []
    for r, (d3s, fs) in res2:
        all_dirs.append(r)
        files.extend(fs)
        if fs and not d3s:
            leaves.append(r)
            continue
        for d3 in d3s:
            all_dirs.append(f"{r}/{d3}")
            leaves.append(f"{r}/{d3}")
    print(f"叶子目录 {len(leaves)} 个 ...", flush=True)

    leaves = sorted(set(leaves))
    done = 0
    with ThreadPoolExecutor(max_workers=args.par) as ex:
        for rel, (d4s, fs) in zip(leaves, ex.map(lambda r: parse_index(get(BASE + r + "/"), r), leaves)):
            files.extend(fs)
            for d4 in d4s:                      # 防御：叶子下还有目录
                all_dirs.append(f"{rel}/{d4}")
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(leaves)} ... {len(files):,} 条", flush=True)

    # 去重：同一路径可能被两处发现
    uniq: dict[str, int] = {}
    for p, s in files:
        if p.lower().endswith(".cif"):
            if p not in uniq or (uniq[p] < 0 <= s):
                uniq[p] = s
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for p in sorted(uniq):
            fh.write(f"{p}\t{uniq[p]}\n")
    with open(args.dirs_out, "w", encoding="utf-8", newline="\n") as fh:
        for d in sorted(set(all_dirs)):
            fh.write(d + "\n")
    print(f"写出 {len(uniq):,} 条 .cif -> {args.out}", flush=True)
    print(f"写出 {len(set(all_dirs)):,} 个目录 -> {args.dirs_out}", flush=True)
    print("CRAWL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
