#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计 TCOD 远端清单：找出"叶子目录条目数异常偏少"的可疑项并回补。

背景：crawl_tcod_http.py 在某个目录请求失败时会让 parse_index 返回空，
      于是该目录被静默记成"没有文件"。本次已实测 40/06/39 就这样丢了 100 条。
      由于 TCOD 叶子目录一律是"每个 100 个 CIF"，条目数明显偏小就是失败信号。

做法：
  1. 从 remote_dirs_tcod.txt 判定叶子目录（没有以它开头的更深目录）；
  2. 统计每个叶子在清单里的条目数，低于 --min-count 的列为可疑；
  3. 逐个重抓索引（低频），把结果补进清单（去重合并）；
  4. 重写 tsv，并打印前后对比。

用法:
  python tools/audit_tcod_inv.py --min-count 90 --par 2 --delay 0.15
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawl_tcod_http as C  # noqa: E402

# 数据根（2026-10-23 起在仓库外）；sync_logs 随数据根一起放。
BASE = os.environ.get("XRD_DATA_ROOT") or r"D:/Project/XRD/sync-COD"
SD = os.path.join(BASE, "sync_logs", "sync20261019")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv", default=os.path.join(SD, "remote_inv_tcod.tsv"))
    ap.add_argument("--dirs", default=os.path.join(SD, "remote_dirs_tcod.txt"))
    ap.add_argument("--min-count", type=int, default=90)
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    import threading
    C.TIMEOUT = args.timeout
    C.TRIES = 3
    C.ABORT_AFTER = 10
    C.DELAY = 0.0
    C._lock = threading.Lock()

    inv: dict[str, int] = {}
    with open(args.inv, "r", encoding="utf-8") as fh:
        for line in fh:
            p, _, s = line.rstrip("\n").rpartition("\t")
            if p:
                try:
                    inv[p] = int(s)
                except ValueError:
                    inv[p] = -1

    dirs = [l.strip() for l in open(args.dirs, encoding="utf-8") if l.strip()]
    dirset = set(dirs)
    deeper = set()
    for d in dirs:
        parts = d.split("/")
        for i in range(1, len(parts)):
            deeper.add("/".join(parts[:i]))
    leaves = sorted(d for d in dirset if d not in deeper)
    print("目录 %d，其中叶子 %d" % (len(dirs), len(leaves)))

    cnt = Counter()
    for p in inv:
        cnt[os.path.dirname(p)] += 1
    leaf_cnt = {d: cnt.get(d, 0) for d in leaves}
    hist = Counter()
    for n in leaf_cnt.values():
        if n == 0:
            hist["0"] += 1
        elif n < 50:
            hist["1-49"] += 1
        elif n < 90:
            hist["50-89"] += 1
        elif n < 101:
            hist["90-100"] += 1
        else:
            hist[">100"] += 1
    print("叶子条目数分布:", dict(sorted(hist.items())))

    suspect = sorted(d for d, n in leaf_cnt.items() if n < args.min_count)
    print("可疑叶子（条目 < %d）: %d 个" % (args.min_count, len(suspect)))
    for d in suspect[:20]:
        print("  ? %-14s 清单内 %d" % (d, leaf_cnt[d]))

    if not suspect:
        print("无需回补")
        return 0

    added = 0
    still_bad = []
    for i, d in enumerate(suspect, 1):
        time.sleep(args.delay)
        html = C.get(C.BASE + d + "/")
        if html is None:
            still_bad.append(d)
            print("  [%d/%d] 重抓失败 %s" % (i, len(suspect), d), flush=True)
            continue
        _subs, fs = C.parse_index(html, d)
        n_new = 0
        for p, s in fs:
            if p.lower().endswith(".cif"):
                if p not in inv or (inv[p] < 0 <= s):
                    n_new += 1
                inv[p] = s
        added += n_new
        print("  [%d/%d] %s 取回 %d 条（新增 %d）"
              % (i, len(suspect), d, len(fs), n_new), flush=True)

    with open(args.inv, "w", encoding="utf-8", newline="\n") as fh:
        for p in sorted(inv):
            fh.write("%s\t%d\n" % (p, inv[p]))

    print("-" * 60)
    print("清单条目: %d（回补新增 %d）" % (len(inv), added))
    if still_bad:
        print("仍失败 %d 个目录: %s" % (len(still_bad), still_bad[:10]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
