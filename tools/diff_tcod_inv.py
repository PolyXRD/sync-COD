#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比对 TCOD 远端清单与本地文件，产出待补清单。

输入:
  sync_logs/sync20261019/remote_inv_tcod.tsv   远端清单（相对路径 <TAB> 近似字节数）
本地根: <repo>/TCOD

输出:
  sync_logs/sync20261019/tcod_missing.txt   需要下载的相对路径（每行一个）
  stdout: 缺失 / 尺寸不符 / 本地多余 的统计与样例

说明: 远端尺寸来自 Apache 索引的人类可读值（3.4K），只能按相对容差比较，
      因此"尺寸不符"只做提示，不自动重下（除 --size-tol 明确指定）。

用法:
  python tools/diff_tcod_inv.py [--remote ...] [--root TCOD] [--tol 0.05] [--extra-out ...]
"""
from __future__ import annotations

import argparse
import os
import sys

# 数据根（2026-10-23 起在仓库外）；TCOD 归档与 sync_logs 都在其下。
BASE = os.environ.get("XRD_DATA_ROOT") or r"D:/Project/XRD/sync-COD"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default=os.path.join(
        BASE, "sync_logs", "sync20261019", "remote_inv_tcod.tsv"))
    ap.add_argument("--root", default=os.path.join(BASE, "TCOD"))
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--missing-out", default=os.path.join(
        BASE, "sync_logs", "sync20261019", "tcod_missing.txt"))
    ap.add_argument("--extra-out", default=os.path.join(
        BASE, "sync_logs", "sync20261019", "tcod_extra.txt"))
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()

    remote: dict[str, int] = {}
    with open(args.remote, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            p, _, s = line.rpartition("\t")
            if not p:
                continue
            try:
                remote[p] = int(s)
            except ValueError:
                remote[p] = -1
    print("远端条目: %d" % len(remote))

    local: dict[str, int] = {}
    for dirpath, _dirs, files in os.walk(args.root):
        rel_dir = os.path.relpath(dirpath, args.root).replace(os.sep, "/")
        for nm in files:
            if not nm.lower().endswith(".cif"):
                continue
            rel = nm if rel_dir == "." else rel_dir + "/" + nm
            try:
                local[rel] = os.path.getsize(os.path.join(dirpath, nm))
            except OSError:
                pass
    print("本地条目: %d" % len(local))

    missing = sorted(p for p in remote if p not in local)
    extra = sorted(p for p in local if p not in remote)
    bad = []
    for p, sz in remote.items():
        ls = local.get(p)
        if ls is None or ls == 0:
            continue
        if sz > 0 and abs(ls - sz) / sz > args.tol:
            bad.append((p, ls, sz))

    print("-" * 60)
    print("缺失（远端有、本地无）: %d" % len(missing))
    print("多余（本地有、远端无）: %d" % len(extra))
    print("尺寸不符（相对差 > %.0f%%）: %d" % (args.tol * 100, len(bad)))

    if missing:
        print("\n缺失样例:")
        for p in missing[:args.show]:
            print("  -", p)
        # 按二级目录归类，便于看新增范围
        from collections import Counter
        c = Counter("/".join(p.split("/")[:2]) for p in missing)
        print("\n缺失按二级目录统计（前 20）:")
        for k, v in c.most_common(20):
            print("  %-8s %6d" % (k, v))
    if extra:
        print("\n多余样例:")
        for p in extra[:args.show]:
            print("  +", p)
    if bad:
        print("\n尺寸不符样例:")
        for p, ls, sz in bad[:args.show]:
            print("  ! %s 本地=%d 远端≈%d" % (p, ls, sz))

    with open(args.missing_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(missing) + ("\n" if missing else ""))
    with open(args.extra_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(extra) + ("\n" if extra else ""))
    print("\n待补清单 -> %s (%d 条)" % (args.missing_out, len(missing)))
    print("多余清单 -> %s (%d 条)" % (args.extra_out, len(extra)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
