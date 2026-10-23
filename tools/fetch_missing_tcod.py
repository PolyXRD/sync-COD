#!/usr/bin/env python3
"""按清单逐个补下缺失文件（针对 wget 不重试 403 的问题）。

背景：wget 的 --tries 只对连接错误和 5xx 重试，**403 直接失败**，
所以并发抓取时被限流的文件会静静地留在缺口里。
本脚本改成"顺序 + 指数退避 + 落盘校验 + 原子改名"，不并发，基本不会触发限流。

用法:
  python tools/fetch_missing_tcod.py --list /tmp/tcod_missing2.txt \
      --remote-inv sync_logs/remote_inv_tcod.tsv --root D:/Project/XRD/PolyXRD-DataBase \
      --delay 0.05 --abort-after 15 --resume

注意（2026-10-19 教训）：服务端对密集请求会先 403、再直接丢弃响应（TCP 能连上但
零字节返回，封禁持续 10 分钟以上）。因此默认串行 + 固定间隔 + 连续失败熔断。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://www.crystallography.net/tcod/cif/"
UA = "Mozilla/5.0 (compatible; tcod-mirror-fix/1.0)"


def fetch(url: str, tries: int = 10) -> bytes | None:
    for i in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=90) as r:
                return r.read()
        except HTTPError as e:
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(min(2 ** min(i, 5), 30))
                continue
            print(f"    HTTP {e.code}")
            return None
        except (URLError, TimeoutError, OSError) as ex:
            print(f"    {type(ex).__name__}")
            time.sleep(min(2 ** min(i, 5), 30))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True)
    ap.add_argument("--remote-inv", required=True)
    ap.add_argument("--root",
                    default=os.environ.get("XRD_DATA_ROOT") or r"D:/Project/XRD/sync-COD")
    ap.add_argument("--delay", type=float, default=0.05,
                    help="每个文件之间的固定间隔秒数（默认 0.05，别把服务端打爆）")
    ap.add_argument("--abort-after", type=int, default=15,
                    help="连续失败达到该次数即熔断退出（判定被限流/封禁）")
    ap.add_argument("--resume", action="store_true",
                    help="已存在且尺寸接近远端估算的文件直接跳过")
    args = ap.parse_args()

    approx: dict[str, int] = {}
    for line in open(args.remote_inv, encoding="utf-8"):
        p, _, s = line.rstrip("\n").rpartition("\t")
        if p:
            try:
                approx[p] = int(s)
            except ValueError:
                approx[p] = -1

    todo = [l.strip() for l in open(args.list, encoding="utf-8") if l.strip()]
    print(f"待补 {len(todo)} 个文件")

    ok = fail = short = skip = 0
    streak = 0
    failed = []
    for i, rel in enumerate(todo, 1):
        dst = os.path.join(args.root, "TCOD", rel.replace("/", os.sep))
        exp = approx.get(rel, -1)
        if args.resume and os.path.exists(dst):
            sz = os.path.getsize(dst)
            if exp <= 0 or abs(sz - exp) / exp <= 0.05:
                skip += 1
                continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        data = fetch(BASE + rel)
        if data is None:
            fail += 1
            streak += 1
            failed.append(rel)
            print(f"  [{i}/{len(todo)}] 失败 {rel}", flush=True)
            if streak >= args.abort_after:
                print(f"\n[熔断] 连续 {streak} 次失败，判定被限流；已补 {ok} 个，"
                      f"剩余 {len(todo) - i} 个留待下轮", flush=True)
                failed.extend(todo[i:])
                break
            continue
        streak = 0
        if exp > 0 and abs(len(data) - exp) / exp > 0.05:
            short += 1
            print(f"  [{i}/{len(todo)}] 尺寸异常 {rel} 收到 {len(data)} 期望≈{exp}（仍写入待复核）")
        tmp = dst + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
        ok += 1
        if i % 50 == 0:
            print(f"  已补 {i}/{len(todo)}  成功 {ok} 跳过 {skip}", flush=True)
        if args.delay > 0:
            time.sleep(args.delay)

    print(f"完成: 成功 {ok}, 失败 {fail}, 尺寸异常 {short}, 跳过 {skip}")
    if failed:
        with open(args.list + ".retry", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(failed) + "\n")
        print(f"失败清单写入 {args.list}.retry（共 {len(failed)} 条）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
