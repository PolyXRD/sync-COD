# sync-COD — COD / PCOD / TCOD source data root and data-side tools

**The source data directory shared by the two database-building repositories**, plus tools for operating on this batch of data (validation + official sync).

This repository **contains only code and documentation**: 13 scripts + notes in `tools/`.
The data itself (about 120 GB / 2 million files) is **not committed**; users obtain it themselves and
place it at the data root (default `D:/Project/XRD/sync-COD`), pointing to it with `XRD_DATA_ROOT`.

On 2026-10-23 the data was moved from `PolyXRD-DataBase/` to this directory (same-volume rename, took seconds).
Reason: these data are neither a product of polyDB nor xinDB, but the **common input** of both; putting
them in either repository would make the other "reference someone else's data across repositories".

```bash
git clone https://github.com/PolyXRD/sync-COD.git
export XRD_DATA_ROOT=/path/to/data-root     # points to where COD/PCOD/TCOD are placed
python tools/verify_data_root.py            # first check whether the data is complete
```

## Layout

```
<data root>/                     ← recommended name: sync-COD
├── tools/                       ← committed: 3 validation + 10 sync (sync disabled)
├── README.md  .gitignore        ← committed
├── .gitattributes  LICENSE*     ← committed (LF normalization + dual license)
│
├── COD/                      535,166 CIFs   3-level layout 1/00/00/1000003.cif
├── PCOD/                   1,061,962       2-level layout (GRINSP predictions, all judged inorganic)
├── TCOD/                     362,293       3-level layout (theoretical structures)
├── inorg_cif/                                 ← copies of the data, not committed
│   ├── COD/                   79,198       CIFs judged inorganic
│   ├── PCOD/               1,061,962
│   └── TCOD/                  46,943
├── inorg_manifest.sqlite     289,300,480 B    per-file manifest, 1,959,421 rows (inorganic 1,188,103)
└── sync_logs/                                 sync logs and manifest archives (not committed)
```

About 120 GB / 2 million files. `inorg_cif/` is a copy; the original archives are kept intact (read-only reference, no deletion or modification) —
so it takes up another copy of space on the same data; this is deliberate redundancy, making it easy for xinDB to scan only the inorganic subset.

The cloned `tools/` can be placed directly into the data root; the scripts locate the data root by
"their own parent directory"; they can also be placed anywhere, as long as `XRD_DATA_ROOT` is set.

## Who reads it

| Consumer | Purpose |
|---|---|
| `PolyXRD-DataBase/tools/build_polydb.py` | reads `COD/` + `inorg_manifest.sqlite`, produces polyDB |
| `PolyXRD-DataBase/tools/scan_inorg.py` | generates `inorg_cif/` and `inorg_manifest.sqlite` |
| `XinMatch-DataBase/tools/build_cod_inorg_xindb.py` | reads `inorg_cif/{COD,PCOD,TCOD}`, produces xinDB |
| `XinMatch-DataBase/tools/scan_inorg.py` | same as above, generates the manifest and copies |
| `PolyXRD` project (`tools/build_polydb.py`, `scripts/audit_cod_source.py`) | database building and source-data reconciliation |
| `XinMatch` project (`xinmatch/core/db/cif_scan.py`, `tools/_env_check.py`) | database building and self-check |

All are located via the **`XRD_DATA_ROOT`** environment variable (default is this directory); the code no longer hard-codes drive letters.

## Consistency baseline (for cross-checking)

| Item | Value |
|---|---|
| Total manifest rows | 1,959,421 |
| Inorganic manifest rows | 1,188,103 (60.6%) |
| COD | 535,166, of which 79,198 inorganic |
| PCOD | 1,061,962, all judged inorganic |
| TCOD | 362,293, of which 46,943 inorganic |
| `inorg_manifest.sqlite` byte count | 289,300,480 |

Running `python tools/verify_data_root.py` compares these numbers item by item, and samples 1,500 files to compare byte counts.

## Criteria (do not change)

Inorganic = **no C, or containing C but no H** (carbonates / carbides / CO₂ count as inorganic).
This criterion is implemented by `cif_common.is_inorganic` and directly determines the 1,188,103
figure above; change it and the new manifest will no longer match the published polyDB / xinDB, making entry-by-entry comparison impossible.

## Notes

- The data directories (`COD/ PCOD/ TCOD/ inorg_cif/ inorg_manifest.sqlite sync_logs/`) are all in `.gitignore`,
  so they **will not be committed by accident**; run `git status --short` before adding data.
- When moving large data directories, note that voidtools **Everything** and Windows Search hold directory
  handles, so `mv` of the whole directory reports Permission denied (moving child files works instead).
  For the workaround see `tools/README.md` and the READMEs of the two database-building repositories.
- On this machine, `rm -rf` on large files actually **moves them to the Recycle Bin**, so free space does not increase;
  to really reclaim it you must empty the Recycle Bin, and WorkBuddy's safe-delete blocks a second deletion inside the Recycle Bin. Do not count on `rm` to free space.

## Related repositories

| Repository | Takes in | Produces |
|---|---|---|
| [PolyXRD-DataBase](https://github.com/PolyXRD/PolyXRD-DataBase) | `COD/` + `inorg_manifest.sqlite` | `*.polyDB` (directory-style, mmap) |
| [XinMatch-DataBase](https://github.com/PolyXRD/XinMatch-DataBase) | `inorg_cif/{COD,PCOD,TCOD}` | `*.xinDB` (single-file SQLite) |

Neither contains data; both build their databases from this repository's source data.
