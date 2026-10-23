#!/usr/bin/env python3
"""生成某库的本地文件清单（相对路径 <TAB> 字节数），供与远端清单比对。

用法: python tools/make_local_inv.py <本地根目录> <输出tsv> [扩展名]
例:   python tools/make_local_inv.py E:/rsync-COD/COD sync_logs/local_inv_cod.tsv .cif

用 os.scandir 迭代而非 os.walk，在 Windows 上走 FindFirstFile，比 find 快得多。
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    root = sys.argv[1].rstrip("/\\")
    out = sys.argv[2]
    suffix = sys.argv[3].lower() if len(sys.argv) > 3 else ".cif"
    n = 0
    errs = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        stack: list[tuple[str, str]] = [("", root)]
        while stack:
            rel, d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(((rel + "/" + e.name) if rel else e.name, e.path))
                            elif suffix == "*" or e.name.lower().endswith(suffix):
                                fh.write(f"{(rel + '/' + e.name) if rel else e.name}\t{e.stat().st_size}\n")
                                n += 1
                        except OSError:
                            errs += 1
            except OSError as ex:
                print(f"ERR {d}: {ex}", file=sys.stderr)
                errs += 1
    print(f"写出 {n:,} 条 -> {out} (错误 {errs})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
