#!/usr/bin/env python3
"""TCOD 同步完整性核验（逐条，不是数个数）。

原理
----
TCOD 没有 rsync 模块，只能走 Apache 目录索引 + wget 递归。但 wget 的日志本身
就是一份**完整的远端清单**：

  * 远端有、本地也有 → `File '<本地路径>' already there; not retrieving.`
  * 远端有、本地刚下载 → `... - '<本地路径>' saved [N/M]`
  * 远端取不到（限流 403 等）→ `ERROR 403` 且没有 saved/already 行  ← 真正的缺口

把 26 份分片日志里出现过的 .cif 路径并起来，就是"wget 这次爬到的远端全集"。
再与实际磁盘上的 .cif 做集合差，即可给出**逐条**的缺失/多余清单。

同时做两项交叉校验：
  * `saved [N/M]` 中 N==M（N<M 说明下载被打断）
  * 统计落在 `.cif` 上的 403 —— 这是最容易漏掉的一类缺口，
    因为 wget 即使大面积 403 也照样打印 FINISHED。

用法
----
  python tools/verify_sync_tcod.py --root E:/rsync-COD --logs "sync_logs/tcod_fix_*.log"
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict

RE_SAVED = re.compile(r"- '([^']+)' saved \[(\d+)/(\d+)\]")
RE_ALREADY = re.compile(r"File '([^']+)' already there; not retrieving\.")
RE_URL = re.compile(r"^--\d{4}-\d{2}-\d{2}[^\-]*--\s+(\S+)")


def rel_of(p: str, root_prefix: str) -> str | None:
    p = p.replace("\\", "/")
    if not p.lower().endswith(".cif"):
        return None
    if root_prefix in p:
        return p.split(root_prefix, 1)[1].lstrip("/")
    return None


def parse_logs(paths, root_prefix: str):
    remote: set[str] = set()
    truncated: list[tuple[str, int, int]] = []
    err403: set[str] = set()
    per_log: dict[str, dict] = {}
    for path in paths:
        fn = os.path.basename(path)
        n_skip = n_save = 0
        finished = False
        cur_url = None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("FINISHED"):
                    finished = True
                    continue
                m = RE_URL.match(line)
                if m:
                    cur_url = m.group(1)
                    continue
                if "ERROR 403" in line and cur_url and cur_url.endswith(".cif"):
                    err403.add(cur_url)
                    continue
                m = RE_SAVED.search(line)
                if m:
                    rel = rel_of(m.group(1), root_prefix)
                    if rel:
                        remote.add(rel)
                        n_save += 1
                        if int(m.group(2)) != int(m.group(3)):
                            truncated.append((rel, int(m.group(2)), int(m.group(3))))
                    continue
                m = RE_ALREADY.search(line)
                if m:
                    rel = rel_of(m.group(1), root_prefix)
                    if rel:
                        remote.add(rel)
                        n_skip += 1
                    continue
        per_log[fn] = dict(skip=n_skip, save=n_save, finished=finished)
    return remote, truncated, err403, per_log


def walk_local(root: str) -> set[str]:
    out = set()
    base = os.path.join(root, "TCOD")
    for dirpath, _dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, base).replace("\\", "/")
        for fn in filenames:
            if fn.lower().endswith(".cif"):
                out.add(fn if rel == "." else f"{rel}/{fn}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root",
                    default=os.environ.get("XRD_DATA_ROOT") or r"D:/Project/XRD/sync-COD")
    ap.add_argument("--logs", default="sync_logs/tcod_par_*.log")
    ap.add_argument("--show", type=int, default=30)
    args = ap.parse_args()

    logdir = os.path.join(args.root, args.logs)
    paths = sorted(glob.glob(logdir))
    if not paths:
        print(f"没有匹配到日志: {logdir}")
        return 1
    # 日志里的 URL 路径形如 /e/rsync-COD/TCOD/...，由 --root 反推盘符与目录名，
    # 不再硬编码 E:（换盘就失效）。
    drive, tail = os.path.splitdrive(os.path.abspath(args.root))
    drive = drive.rstrip(":") or "e"
    root_prefix = "/%s/%s/TCOD/" % (drive.lower(), os.path.basename(tail.rstrip("\\/")) or "")

    print(f"解析 {len(paths)} 份日志 ...")
    remote, truncated, err403, per_log = parse_logs(paths, root_prefix)
    print("扫描本地磁盘 ...")
    local = walk_local(args.root)

    missing = sorted(remote - local)
    extra = sorted(local - remote)

    print()
    print("=" * 76)
    print(f"日志覆盖的远端 .cif : {len(remote):,}")
    print(f"本地磁盘 .cif       : {len(local):,}")
    print(f"缺失（远端有本地无）: {len(missing):,}")
    print(f"多余（本地有日志无）: {len(extra):,}")
    print(f"截断 saved[N!=M]    : {len(truncated):,}")
    print(f"取不到(.cif 403)    : {len(err403):,}")
    unfinished = [f for f, st in per_log.items() if not st["finished"]]
    print(f"未正常结束的分片    : {len(unfinished):,}")
    print("=" * 76)

    if truncated:
        print("\n[截断文件]")
        for rel, a, b in truncated[:args.show]:
            print(f"  {rel}  {a}/{b}")

    if err403:
        print(f"\n[.cif 403 取不到（前 {args.show}）]")
        for u in sorted(err403)[:args.show]:
            print("  " + u)

    if missing:
        print(f"\n[缺失文件（前 {args.show}）]")
        for m in missing[:args.show]:
            print("  " + m)

    if extra:
        print(f"\n[多余文件（前 {args.show}）]")
        for m in extra[:args.show]:
            print("  " + m)

    print("\n[每份分片日志]")
    for fn, st in sorted(per_log.items()):
        flag = "" if st["finished"] else "   <-- 未正常结束"
        print(f"  {fn:<26} finished={str(st['finished']):<5} 跳过={st['skip']:<7} 新下载={st['save']}{flag}")

    # 路径深度分布，便于发现结构异常
    hist: dict[int, int] = defaultdict(int)
    for rel in local:
        hist[rel.count("/")] += 1
    print("\n[本地路径深度分布]")
    for k in sorted(hist):
        print(f"  深度{k}: {hist[k]:,}")

    bad = len(missing) + len(truncated) + len(err403) + len(unfinished)
    print()
    if bad == 0:
        print("结果: 逐条一致 —— 无遗漏、无截断、无取不到、所有分片正常结束。")
    else:
        print(f"结果: 存在 {bad:,} 处问题（缺失 {len(missing)} / 截断 {len(truncated)} "
              f"/ 403 {len(err403)} / 未完成 {len(unfinished)}）。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
