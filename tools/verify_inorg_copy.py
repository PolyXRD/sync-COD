#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核验 inorg_cif 副本与 inorg_manifest.sqlite 是否逐条一致。

检查三项（任一不为 0 都算不合格）：
  1. 清单里 is_inorg=1 的条目，副本文件不存在 —— 缺
  2. 清单里 is_inorg=1 的条目，副本文件尺寸与清单记录不符 —— 坏
  3. 副本里存在、但清单里没有（或 is_inorg=0）的文件 —— 多

另附统计：按库计数、ID 区间合法性、重复 cod_id。

用法: python tools/verify_inorg_copy.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

# 数据根（仓库外）：2026-10-23 起统一在 D:/Project/XRD/sync-COD，可用 XRD_DATA_ROOT 覆盖。
BASE = os.environ.get("XRD_DATA_ROOT") or r"D:\Project\XRD\sync-COD"
MANIFEST = os.path.join(BASE, "inorg_manifest.sqlite")
INORG = os.path.join(BASE, "inorg_cif")

SHOW = 10


def main() -> int:
    if not os.path.exists(MANIFEST):
        print("找不到清单 %s" % MANIFEST)
        return 2

    con = sqlite3.connect("file:%s?mode=ro" % MANIFEST.replace("\\", "/"), uri=True)
    cur = con.cursor()

    tot = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    ino = cur.execute("SELECT COUNT(*) FROM files WHERE is_inorg=1").fetchone()[0]
    dup = cur.execute(
        "SELECT COUNT(*) FROM (SELECT cod_id FROM files GROUP BY cod_id "
        "HAVING COUNT(*)>1)").fetchone()[0]
    print("清单条目 %d，无机 %d (%.1f%%)，重复 cod_id %d"
          % (tot, ino, 100.0 * ino / max(tot, 1), dup))

    # 清单：{(lib, rel): (cod_id, size)}
    expect = {}
    miss = bad = 0
    miss_s, bad_s = [], []
    rows = cur.execute(
        "SELECT cod_id, lib, rel, size FROM files WHERE is_inorg=1").fetchall()
    for cod_id, lib, rel, size in rows:
        # 清单里的 rel 是 Windows 路径（反斜杠），统一成正斜杠再比对
        rel_u = rel.replace("\\", "/")
        dst = os.path.join(INORG, lib, rel_u.replace("/", os.sep))
        expect[(lib, rel_u)] = cod_id
        if not os.path.exists(dst):
            miss += 1
            if len(miss_s) < SHOW:
                miss_s.append("%s/%s" % (lib, rel))
        elif size and os.path.getsize(dst) != size:
            bad += 1
            if len(bad_s) < SHOW:
                bad_s.append("%s/%s 副本=%d 清单=%d"
                             % (lib, rel, os.path.getsize(dst), size))

    # 副本侧：找出多余
    extra = 0
    extra_s = []
    n_seen = 0
    for dirpath, _dirs, files in os.walk(INORG):
        rel_dir = os.path.relpath(dirpath, INORG).replace(os.sep, "/")
        if rel_dir == ".":
            continue
        lib, _, sub = rel_dir.partition("/")
        for nm in files:
            if not nm.lower().endswith(".cif"):
                continue
            n_seen += 1
            key = (lib, (sub + "/" + nm) if sub else nm)
            if key not in expect:
                extra += 1
                if len(extra_s) < SHOW:
                    extra_s.append("%s/%s" % (lib, key[1]))

    print("副本实有 .cif: %d" % n_seen)
    print("-" * 60)
    print("① 清单有、副本缺 : %d" % miss)
    print("② 副本尺寸不符   : %d" % bad)
    print("③ 副本多余       : %d" % extra)
    for label, lst in (("缺", miss_s), ("坏", bad_s), ("多", extra_s)):
        for x in lst:
            print("   [%s] %s" % (label, x))

    # 分库统计 + ID 区间
    print("-" * 60)
    for lib in ("COD", "PCOD", "TCOD"):
        n = cur.execute("SELECT COUNT(*), MIN(cod_id), MAX(cod_id) "
                        "FROM files WHERE lib=? AND is_inorg=1", (lib,)).fetchone()
        print("%-5s 无机 %7d  cod_id 区间 %s .. %s" % (lib, n[0], n[1], n[2]))

    con.close()
    ok = (miss == 0 and bad == 0 and extra == 0 and dup == 0)
    print("-" * 60)
    print("结论: %s" % ("合格（副本与清单逐条一致）" if ok else "不合格，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
