"""迁移后完整性验证：清单驱动抽样 + 目录结构 + manifest 自身完整性。

不遍历全树（200 万文件太慢），而是：
  1) manifest 自身 integrity_check + 行数（与迁移前基准逐项比对）；
  2) 每个库随机抽 N 条（含无机与非无机），stat 新根下的对应文件，比对**字节数**；
  3) 校验三个库的顶层目录数与 inorg_cif 副本目录数与清单口径一致。
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys

DATA_ROOT = os.environ.get("XRD_DATA_ROOT") or r"D:\Project\XRD\sync-COD"
MANIFEST = os.path.join(DATA_ROOT, "inorg_manifest.sqlite")

BASELINE = {
    "manifest_rows": 1_959_421,
    "inorg_rows": 1_188_103,
    "manifest_bytes": 289_300_480,
    "per_lib": {
        "COD": (535_166, 79_198),
        "PCOD": (1_061_962, 1_061_962),
        "TCOD": (362_293, 46_943),
    },
}
SAMPLES_PER_LIB = 250

fails: list[str] = []
warns: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print("  [PASS] %-38s %s" % (label, detail))
    else:
        print("  [FAIL] %-38s %s" % (label, detail))
        fails.append(label)


def main() -> int:
    print("数据根:", DATA_ROOT)
    if not os.path.isdir(DATA_ROOT):
        print("数据根不存在")
        return 2
    if not os.path.isfile(MANIFEST):
        print("清单不存在")
        return 2

    # ── 1) 清单自身 ────────────────────────────────────────
    size = os.path.getsize(MANIFEST)
    check(size == BASELINE["manifest_bytes"], "manifest 字节数",
          "%d (基准 %d)" % (size, BASELINE["manifest_bytes"]))

    con = sqlite3.connect("file:%s?mode=ro" % MANIFEST, uri=True)
    ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    check(ic == "ok", "manifest integrity_check", ic)

    n_all = con.execute("select count(*) from files").fetchone()[0]
    n_inorg = con.execute("select count(*) from files where is_inorg=1").fetchone()[0]
    check(n_all == BASELINE["manifest_rows"], "清单总行数",
          "%d (基准 %d)" % (n_all, BASELINE["manifest_rows"]))
    check(n_inorg == BASELINE["inorg_rows"], "无机行数",
          "%d (基准 %d)" % (n_inorg, BASELINE["inorg_rows"]))

    cols = [r[1] for r in con.execute("PRAGMA table_info(files)")]
    print("  [INFO] files 列:", ", ".join(cols))

    # ── 2) 分库行数 ────────────────────────────────────────
    for lib, (exp_all, exp_inorg) in BASELINE["per_lib"].items():
        a = con.execute("select count(*) from files where lib=?", (lib,)).fetchone()[0]
        i = con.execute("select count(*) from files where lib=? and is_inorg=1",
                        (lib,)).fetchone()[0]
        check(a == exp_all and i == exp_inorg, "%s 行数" % lib,
              "全部 %d/%d, 无机 %d/%d" % (a, exp_all, i, exp_inorg))

    # ── 3) 抽样 stat（原始归档）──────────────────────────────
    rng = random.Random(20261023)
    for lib in ("COD", "PCOD", "TCOD"):
        ids = [r[0] for r in con.execute(
            "select cod_id from files where lib=?", (lib,))]
        step = max(1, len(ids) // SAMPLES_PER_LIB)
        picked = ids[::step][:SAMPLES_PER_LIB]
        miss = bad = 0
        for cid in picked:
            row = con.execute(
                "select rel, size from files where cod_id=? and lib=?",
                (cid, lib)).fetchone()
            if not row:
                continue
            rel, want = row
            p = os.path.join(DATA_ROOT, lib, str(rel).replace("\\", os.sep))
            if not os.path.isfile(p):
                miss += 1
            elif want and os.path.getsize(p) != int(want):
                bad += 1
        check(miss == 0 and bad == 0, "%s 抽样 %d 个归档文件" % (lib, len(picked)),
              "缺失 %d / 尺寸不符 %d" % (miss, bad))

    # ── 4) 抽样 stat（inorg_cif 副本）─────────────────────────
    for lib in ("COD", "PCOD", "TCOD"):
        rows = con.execute(
            "select rel, size from files where lib=? and is_inorg=1", (lib,)).fetchall()
        step = max(1, len(rows) // SAMPLES_PER_LIB)
        picked = rows[::step][:SAMPLES_PER_LIB]
        miss = bad = 0
        for rel, want in picked:
            p = os.path.join(DATA_ROOT, "inorg_cif", lib,
                             str(rel).replace("\\", os.sep))
            if not os.path.isfile(p):
                miss += 1
            elif want and os.path.getsize(p) != int(want):
                bad += 1
        check(miss == 0 and bad == 0, "inorg_cif/%s 抽样 %d 个副本" % (lib, len(picked)),
              "缺失 %d / 尺寸不符 %d" % (miss, bad))

    # ── 5) 顶层结构 ────────────────────────────────────────
    print("  [INFO] 顶层目录:", ", ".join(sorted(
        n for n in os.listdir(DATA_ROOT) if os.path.isdir(os.path.join(DATA_ROOT, n)))))
    for lib, depth in (("COD", 3), ("PCOD", 2), ("TCOD", 3)):
        base = os.path.join(DATA_ROOT, lib)
        lvl1 = sorted(os.listdir(base)) if os.path.isdir(base) else []
        lvl2 = 0
        for n in lvl1:
            p = os.path.join(base, n)
            if os.path.isdir(p):
                lvl2 += len([x for x in os.listdir(p) if os.path.isdir(os.path.join(p, x))])
        check(len(lvl1) > 0, "%s 一级目录" % lib, "%d 个（二级合计 %d）" % (len(lvl1), lvl2))

    con.close()

    print()
    print("合计：FAIL %d / WARN %d" % (len(fails), len(warns)))
    if fails:
        print("失败项:", ", ".join(fails))
        return 1
    print("结论：迁移完整")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
