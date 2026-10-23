#!/usr/bin/env python3
"""远端清单 vs 本地清单：逐条（名称 + 字节数）比对，给出遗漏/多余/大小不符。

两个输入都必须是「相对路径 <TAB> 字节数」且已按路径排序（sort）。
采用流式归并，不把 160 万条整体读进内存 —— 本机只有 7.9 GB 内存且常驻占用高。

用法:
  python tools/cmp_inv.py <本地清单> <远端清单> [标签]
"""
from __future__ import annotations

import sys
from itertools import zip_longest

SHOW = 30


def reader(fn: str):
    with open(fn, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            p, _, s = line.rpartition("\t")
            if not p:
                continue
            try:
                yield p, int(s)
            except ValueError:
                yield p, -1


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tol = 0
    for a in sys.argv[1:]:
        if a.startswith("--size-tol="):
            tol = int(a.split("=", 1)[1])
    local_fn, remote_fn = args[0], args[1]
    label = args[2] if len(args) > 2 else ""

    n_local = n_remote = 0
    missing: list[tuple[str, int]] = []      # 远端有、本地无
    extra: list[tuple[str, int]] = []        # 本地有、远端无
    mismatch: list[tuple[str, int, int]] = []  # 大小不符

    a = reader(local_fn)
    b = reader(remote_fn)
    la = next(a, None)
    lb = next(b, None)
    while la is not None or lb is not None:
        if la is None:
            missing.append(lb); n_remote += 1; lb = next(b, None)
        elif lb is None:
            extra.append(la); n_local += 1; la = next(a, None)
        else:
            if la[0] < lb[0]:
                extra.append(la); n_local += 1; la = next(a, None)
            elif la[0] > lb[0]:
                missing.append(lb); n_remote += 1; lb = next(b, None)
            else:
                n_local += 1; n_remote += 1
                # 远端尺寸可能来自 Apache 索引的人类可读值（3.4K），
                # 用 -1 表示未知、用 --size-tol 容忍取整误差
                if lb[1] >= 0 and la[1] != lb[1] and abs(la[1] - lb[1]) > tol:
                    mismatch.append((la[0], la[1], lb[1]))
                la = next(a, None); lb = next(b, None)

    print("=" * 74)
    if label:
        print(f"库: {label}")
    print(f"本地条目      : {n_local:,}")
    print(f"远端条目      : {n_remote:,}")
    print(f"遗漏(远端有)  : {len(missing):,}")
    print(f"多余(本地有)  : {len(extra):,}")
    print(f"大小不符      : {len(mismatch):,}")
    print("=" * 74)

    # 非 .cif 条目单列，避免与 CIF 数据遗漏混淆
    def split_non_cif(items, idx=0):
        cif = [i for i in items if str(i[idx]).lower().endswith(".cif")]
        other = [i for i in items if not str(i[idx]).lower().endswith(".cif")]
        return cif, other

    if missing:
        c, o = split_non_cif(missing)
        print(f"\n[遗漏] .cif {len(c):,} 个；非 .cif {len(o):,} 个")
        for p, s in c[:SHOW]:
            print(f"   - {p}  ({s} B)")
        if len(c) > SHOW:
            print(f"   ... 其余 {len(c)-SHOW:,} 个")
        if o:
            from collections import Counter
            print("   非 .cif 明细(按扩展名):", dict(Counter(
                (p.rsplit(".", 1)[-1] if "." in p else "(无)") for p, _ in o)))
            for p, s in o[:10]:
                print(f"   - {p}  ({s} B)")
    if extra:
        c, o = split_non_cif(extra)
        print(f"\n[多余] .cif {len(c):,} 个；非 .cif {len(o):,} 个")
        for p, s in c[:SHOW]:
            print(f"   + {p}  ({s} B)")
        if len(c) > SHOW:
            print(f"   ... 其余 {len(c)-SHOW:,} 个")
    if mismatch:
        print(f"\n[大小不符] {len(mismatch):,} 个（本地/远端）")
        for p, a_sz, b_sz in mismatch[:SHOW]:
            print(f"   ! {p}  本地={a_sz}  远端={b_sz}")

    bad = len(missing) + len(extra) + len(mismatch)
    if bad == 0:
        print("\n结果: 逐条一致，无遗漏、无多余、无大小不符。")
    else:
        print(f"\n结果: 共 {bad:,} 处差异。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
