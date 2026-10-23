#!/usr/bin/env python3
"""抓取 COD / PCOD 的**远端完整文件清单**（相对路径 <TAB> 字节数）。

为什么要这个而不是直接 rsync --dry-run：
  服务端 stat 72 万 / 106 万个文件极慢，实测全模块试运行 15 分钟没有任何输出；
  按二级目录拆分后每个连接只要几秒，可并行、可重试。

为什么用 Python 而不是 shell 并行 append：
  Windows/MSYS 下多个进程 `>>` 同一个文件会发生**行交错**（实测 22 行损坏，
  形如把两条记录从中间粘在一起），所以每个子任务必须各自成文件，最后串行合并。

用法:
  python tools/fetch_remote_inv.py --module cif        --parents-file A --out B
  python tools/fetch_remote_inv.py --module pcod-cif   --parents-file A --out B
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

RSYNC = r"C:\msys64\usr\bin\rsync.exe"
URL = "rsync://www.crystallography.net/{module}/{parent}/"


def parse_listing(text: str, parent: str):
    """把 rsync --list-only -r 的输出转成 (relpath, size) 列表。"""
    out = []
    bad = 0
    for line in text.splitlines():
        if not line.startswith("-"):
            continue
        # 权限 大小 日期 时间 名称
        parts = line.split(None, 4)
        if len(parts) < 5:
            bad += 1
            continue
        name = parts[4]
        try:
            size = int(parts[1].replace(",", ""))
        except ValueError:
            bad += 1
            continue
        if name.endswith("/"):
            continue
        out.append((f"{parent}/{name}", size))
    return out, bad


def fetch(module: str, parent: str):
    p = subprocess.run(
        [RSYNC, "--list-only", "-r", "--no-motd", URL.format(module=module, parent=parent)],
        capture_output=True, text=True, timeout=1800,
    )
    if p.returncode != 0:
        return parent, None, p.returncode, p.stderr.strip()[:200]
    rows, bad = parse_listing(p.stdout, parent)
    return parent, rows, 0, f"badlines={bad}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True)
    ap.add_argument("--parents-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--par", type=int, default=6)
    args = ap.parse_args()

    parents = [l.strip() for l in open(args.parents_file, encoding="utf-8") if l.strip()]
    print(f"二级目录 {len(parents)} 个，并行 {args.par}", flush=True)

    results: dict[str, list] = {}
    failed = []
    with ThreadPoolExecutor(max_workers=args.par) as ex:
        for i, (parent, rows, rc, info) in enumerate(
                ex.map(lambda p: fetch(args.module, p), parents), 1):
            if rows is None:
                failed.append((parent, rc, info))
                print(f"  [{i}/{len(parents)}] {parent} 失败 rc={rc} {info}", flush=True)
            else:
                results[parent] = rows
                print(f"  [{i}/{len(parents)}] {parent}: {len(rows)} 条 ({info})", flush=True)

    # 串行写盘 —— 单进程，不存在交错
    total = 0
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for parent in parents:
            for path, size in results.get(parent, []):
                fh.write(f"{path}\t{size}\n")
                total += 1
    print(f"写出 {total:,} 条 -> {args.out}", flush=True)
    if failed:
        print(f"!! {len(failed)} 个目录抓取失败: {[f[0] for f in failed]}", flush=True)
        return 1
    print("INV_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
