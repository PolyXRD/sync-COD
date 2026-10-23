#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3COD 覆盖度核对：源库 CIF / 无机副本 / 清单 三方逐条对照。

回答两个问题（用户明确要求）:
  1. "验证不要漏条目" —— 磁盘上每个 .cif 是否都进了 inorg_manifest；
  2. "是否已复制到对应的无机物文件夹中" —— 清单里 is_inorg=1 的条目
     是否都在 inorg_cif/{LIB}/... 下有同尺寸的副本，且没有孤儿。

做法：只 listdir（不读文件内容），6 进程并行，产出 JSONL，
再单进程灌进 coverage.sqlite 与清单做 SQL 集合运算。

用法:
    python verify_coverage.py [--workers 6] [--skip-disk] [--skip-icif]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OMP_NUM_THREADS", "1")

# cif_common（parse_elements / is_inorganic / list_dirs / formula_mass）是**构库仓库**
# 的共用模块，本脚本住在数据根下，需要把仓库的 tools 目录挂进 sys.path。
# 单一真源留在仓库里，不在数据根复制一份（复制必然漂移）。
# 换机器用 POLYXRD_TOOLS 指过去。
_POLYXRD_TOOLS = (os.environ.get("POLYXRD_TOOLS")
                  or r"D:\Project\XRD\PolyXRD-DataBase\tools")
if os.path.isdir(_POLYXRD_TOOLS) and _POLYXRD_TOOLS not in sys.path:
    sys.path.insert(0, _POLYXRD_TOOLS)

import cif_common as B  # noqa: E402

# 数据根（仓库外）：2026-10-23 起统一在 D:/Project/XRD/sync-COD，可用 XRD_DATA_ROOT 覆盖。
BASE = os.environ.get("XRD_DATA_ROOT") or r"D:\Project\XRD\sync-COD"
PARTS = os.path.join(BASE, "_scan_parts")
MANIFEST = os.path.join(BASE, "inorg_manifest.sqlite")
COVER = os.path.join(BASE, "coverage.sqlite")

# (名称, 根, 目录深度, cod_id 偏移)
SRC = [
    ("COD", os.path.join(BASE, "COD"), 3, 0),
    ("PCOD", os.path.join(BASE, "PCOD"), 2, 20_000_000),
    ("TCOD", os.path.join(BASE, "TCOD"), 3, 100_000_000),
]
DST = [
    ("COD", os.path.join(BASE, "inorg_cif", "COD"), 3, 0),
    ("PCOD", os.path.join(BASE, "inorg_cif", "PCOD"), 2, 20_000_000),
    ("TCOD", os.path.join(BASE, "inorg_cif", "TCOD"), 3, 100_000_000),
]

_RE_DIGIT = re.compile(r"(\d+)")


def jobs_of(libs):
    out = []
    for name, root, depth, off in libs:
        if not os.path.isdir(root):
            continue
        for rel, abs_dir in B.list_dirs(root, depth):
            out.append((name, rel, abs_dir, off))
    return out


def walker(args):
    """只 listdir + stat，不读文件内容。"""
    wid, jobs, tag = args
    fout = os.path.join(PARTS, "%s_%02d.jsonl" % (tag, wid))
    n = 0
    nod = 0
    buf = []
    t0 = time.time()
    with open(fout, "w", encoding="utf-8") as out:
        for lib, rel, abs_dir, off in jobs:
            try:
                names = sorted(x for x in os.listdir(abs_dir)
                               if x.lower().endswith(".cif"))
            except OSError:
                continue
            for nm in names:
                try:
                    sz = os.path.getsize(os.path.join(abs_dir, nm))
                except OSError:
                    continue
                if sz == 0:
                    continue
                m = _RE_DIGIT.search(nm)
                if not m:
                    nod += 1
                    continue
                buf.append(json.dumps([int(m.group(1)) + off, lib,
                                       ("%s/%s" % (rel, nm)) if rel else nm,
                                       sz], ensure_ascii=False))
                if len(buf) >= 5000:
                    out.write("\n".join(buf) + "\n")
                    n += len(buf)
                    buf.clear()
        if buf:
            out.write("\n".join(buf) + "\n")
            n += len(buf)
    return (wid, n, nod, round(time.time() - t0, 1))


def run(tag, libs, workers):
    jobs = jobs_of(libs)
    n = workers
    chunks = [(i, jobs[i::n], tag) for i in range(n)]
    tot = nod = 0
    with Pool(n) as pool:
        for wid, c, nd, sec in pool.imap_unordered(walker, chunks):
            tot += c
            nod += nd
            print("  %s w%02d: %d 文件, %d 无编号, %d s" % (tag, wid, c, nd, sec),
                  flush=True)
    print("  %s 合计 %d 个 CIF" % (tag, tot), flush=True)
    return tot, nod


def load(con, tag):
    con.execute("DROP TABLE IF EXISTS %s" % tag)
    con.execute("CREATE TABLE %s (cod_id INTEGER, lib TEXT, rel TEXT, size INTEGER)"
                % tag)
    ins = "INSERT INTO %s VALUES (?,?,?,?)" % tag
    import glob
    tot = 0
    for f in sorted(glob.glob(os.path.join(PARTS, "%s_*.jsonl" % tag))):
        buf = []
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    buf.append(json.loads(line))
                except ValueError:
                    continue
                if len(buf) >= 20000:
                    con.executemany(ins, buf)
                    tot += len(buf)
                    buf.clear()
        if buf:
            con.executemany(ins, buf)
            tot += len(buf)
        con.commit()
    con.execute("CREATE INDEX IF NOT EXISTS ix_%s ON %s(cod_id)" % (tag, tag))
    con.commit()
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--skip-disk", action="store_true")
    ap.add_argument("--skip-icif", action="store_true")
    args = ap.parse_args()

    os.makedirs(PARTS, exist_ok=True)
    t0 = time.time()
    if not args.skip_disk:
        print("=== 扫描源库目录 (仅 listdir) ===", flush=True)
        run("disk", SRC, args.workers)
    if not args.skip_icif:
        print("=== 扫描无机副本目录 (仅 listdir) ===", flush=True)
        run("icif", DST, args.workers)

    if os.path.exists(COVER):
        os.remove(COVER)
    con = sqlite3.connect(COVER)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    nd = load(con, "disk")
    ni = load(con, "icif")
    con.execute("ATTACH DATABASE ? AS mf", (MANIFEST,))

    print("=" * 70)
    print("覆盖度核对（耗时 %.1f min）" % ((time.time() - t0) / 60.0))
    print()
    print("【1】源库磁盘 vs 清单 —— 是否漏条目")
    print("%-6s %11s %11s %10s %10s" % ("库", "磁盘CIF", "清单条目", "漏(盘有单无)", "多(单有盘无)"))
    tot_m = tot_x = 0
    for lib in ("COD", "PCOD", "TCOD"):
        d = con.execute("SELECT COUNT(*) FROM disk WHERE lib=?", (lib,)).fetchone()[0]
        f = con.execute("SELECT COUNT(*) FROM mf.files WHERE lib=?", (lib,)).fetchone()[0]
        miss = con.execute(
            "SELECT COUNT(*) FROM disk d WHERE d.lib=? AND NOT EXISTS "
            "(SELECT 1 FROM mf.files f WHERE f.cod_id=d.cod_id)", (lib,)).fetchone()[0]
        extra = con.execute(
            "SELECT COUNT(*) FROM mf.files f WHERE f.lib=? AND NOT EXISTS "
            "(SELECT 1 FROM disk d WHERE d.cod_id=f.cod_id)", (lib,)).fetchone()[0]
        tot_m += miss
        tot_x += extra
        print("%-6s %11d %11d %10d %10d" % (lib, d, f, miss, extra))
    D = con.execute("SELECT COUNT(*) FROM disk").fetchone()[0]
    F = con.execute("SELECT COUNT(*) FROM mf.files").fetchone()[0]
    print("%-6s %11d %11d %10d %10d" % ("合计", D, F, tot_m, tot_x))

    print()
    print("【2】清单无机条目 vs inorg_cif 副本 —— 是否都已复制")
    print("%-6s %11s %11s %10s %10s" % ("库", "清单无机", "副本文件", "缺副本", "孤儿副本"))
    for lib in ("COD", "PCOD", "TCOD"):
        fi = con.execute("SELECT COUNT(*) FROM mf.files WHERE lib=? AND is_inorg=1",
                         (lib,)).fetchone()[0]
        ic = con.execute("SELECT COUNT(*) FROM icif WHERE lib=?", (lib,)).fetchone()[0]
        lack = con.execute(
            "SELECT COUNT(*) FROM mf.files f WHERE f.lib=? AND f.is_inorg=1 "
            "AND NOT EXISTS (SELECT 1 FROM icif c WHERE c.cod_id=f.cod_id)",
            (lib,)).fetchone()[0]
        orph = con.execute(
            "SELECT COUNT(*) FROM icif c WHERE c.lib=? AND NOT EXISTS "
            "(SELECT 1 FROM mf.files f WHERE f.cod_id=c.cod_id AND f.is_inorg=1)",
            (lib,)).fetchone()[0]
        print("%-6s %11d %11d %10d %10d" % (lib, fi, ic, lack, orph))
    FI = con.execute("SELECT COUNT(*) FROM mf.files WHERE is_inorg=1").fetchone()[0]
    IC = con.execute("SELECT COUNT(*) FROM icif").fetchone()[0]
    LACK = con.execute(
        "SELECT COUNT(*) FROM mf.files f WHERE f.is_inorg=1 AND NOT EXISTS "
        "(SELECT 1 FROM icif c WHERE c.cod_id=f.cod_id)").fetchone()[0]
    ORPH = con.execute(
        "SELECT COUNT(*) FROM icif c WHERE NOT EXISTS "
        "(SELECT 1 FROM mf.files f WHERE f.cod_id=c.cod_id AND f.is_inorg=1)"
    ).fetchone()[0]
    print("%-6s %11d %11d %10d %10d" % ("合计", FI, IC, LACK, ORPH))

    # 尺寸不一致的副本（复制不完整）
    dif = con.execute(
        "SELECT COUNT(*) FROM icif c JOIN mf.files f ON f.cod_id=c.cod_id "
        "WHERE f.is_inorg=1 AND c.size != f.size").fetchone()[0]
    print()
    print("副本尺寸与源不一致: %d" % dif)
    print()
    if tot_m == 0 and LACK == 0 and dif == 0:
        print(">>> 结论: 无漏条目，无机副本齐全。")
    else:
        print(">>> 结论: 存在缺口，需补扫/补复制。")
    con.close()


if __name__ == "__main__":
    main()
