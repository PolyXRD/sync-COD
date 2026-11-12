# sync-COD —— COD / PCOD / TCOD 源数据根与数据侧工具

**两个构库仓库共用的源数据目录**，外加操作这批数据的工具（验收 + 与官方同步）。

本仓库**只放代码与文档**：`tools/` 里 13 个脚本 + 说明。
数据本体（约 120 GB / 200 万文件）**不入库**，由使用者自行获取后放到数据根
（缺省 `D:/Project/XRD/sync-COD`），用环境变量 `XRD_DATA_ROOT` 指过去。

2026-10-23 数据从 `PolyXRD-DataBase/` 迁到本目录（同卷 rename，秒级）。
迁出原因：这些数据既不是 polyDB 的产物也不是 xinDB 的产物，而是两者的**共同输入**；
放在任何一个仓库里都会让另一个仓库"隔着仓库引用别人的数据"。

```bash
git clone https://github.com/PolyXRD/sync-COD.git
export XRD_DATA_ROOT=/path/to/data-root     # 指向放 COD/PCOD/TCOD 的地方
python tools/verify_data_root.py            # 先验数据是否完整
```

## 布局

```
<数据根>/                        ← 建议就叫 sync-COD
├── tools/                       ← 入库：验收 3 个 + 同步 10 个（同步已停用）
├── README.md  .gitignore        ← 入库
├── .gitattributes  LICENSE*     ← 入库（LF 归一 + 双许可）
│
├── COD/                      535,166 个 CIF   3 级布局 1/00/00/1000003.cif
├── PCOD/                   1,061,962 个       2 级布局（GRINSP 预测，全部判无机）
├── TCOD/                     362,293 个       3 级布局（理论结构）
├── inorg_cif/                                 ← 数据的副本，不入库
│   ├── COD/                   79,198 个       判为无机的 CIF
│   ├── PCOD/               1,061,962 个
│   └── TCOD/                  46,943 个
├── inorg_manifest.sqlite     289,300,480 B    逐条清单 1,959,421 行（无机 1,188,103）
└── sync_logs/                                 同步日志与清单留档（不入库）
```

约 120 GB / 200 万文件。`inorg_cif/` 是副本，原始归档完整保留（只读引用，不删不改）——
所以它在同一份数据上又占一份空间，这是刻意的冗余，便于 xinDB 只扫无机子集。

克隆下来的 `tools/` 直接放进数据根即可，脚本按"自己上一级"定位数据根；
也可以放在任何位置，只要设好 `XRD_DATA_ROOT`。

## 谁在读它

| 消费者 | 用途 |
|---|---|
| `PolyXRD-DataBase/tools/build_polydb.py` | 读 `COD/` + `inorg_manifest.sqlite`，出 polyDB |
| `PolyXRD-DataBase/tools/scan_inorg.py` | 生成 `inorg_cif/` 与 `inorg_manifest.sqlite` |
| `XinMatch-DataBase/tools/build_cod_inorg_xindb.py` | 读 `inorg_cif/{COD,PCOD,TCOD}`，出 xinDB |
| `XinMatch-DataBase/tools/scan_inorg.py` | 同上，生成清单与副本 |
| `PolyXRD` 项目（`tools/build_polydb.py`、`scripts/audit_cod_source.py`） | 建库与源数据对账 |
| `XinMatch` 项目（`xinmatch/core/db/cif_scan.py`、`tools/_env_check.py`） | 建库与自检 |

全部通过环境变量 **`XRD_DATA_ROOT`** 定位（缺省即本目录），代码里不再写死盘符。

## 一致性基线（核对用）

| 项 | 值 |
|---|---|
| 清单总行数 | 1,959,421 |
| 清单无机行数 | 1,188,103（60.6%） |
| COD | 535,166 个，其中无机 79,198 |
| PCOD | 1,061,962 个，全部判无机 |
| TCOD | 362,293 个，其中无机 46,943 |
| `inorg_manifest.sqlite` 字节数 | 289,300,480 |

跑 `python tools/verify_data_root.py` 会逐项比对这些数字，并抽样 1,500 个文件比字节数。

## 判据（不要动）

无机 = **不含 C，或含 C 但不含 H**（碳酸盐 / 碳化物 / CO₂ 归无机）。
这个判据由 `cif_common.is_inorganic` 实现，直接决定上面 1,188,103 这个数；
改了它，新清单与已发布的 polyDB / xinDB 就对不上，无法逐条比对。

## 注意

- 数据目录（`COD/ PCOD/ TCOD/ inorg_cif/ inorg_manifest.sqlite sync_logs/`）都在
  `.gitignore` 里，**不会误提交**。加数据前先 `git status --short` 看一眼。
- 搬大数据目录时注意：voidtools **Everything** 与 Windows Search 会持有目录句柄，
  导致整个目录 `mv` 报 Permission denied（搬子文件反而能过）。对策见
  `tools/README.md` 与两个构库仓库的 README。
- 本机的 `rm -rf` 在大文件上实际是**移进回收站**，可用空间不会增加；要真回收得清空回收站，
  且 WorkBuddy 的 safe-delete 会拦截回收站内的二次删除。别指望 `rm` 腾空间。

## 相关仓库

| 仓库 | 吃什么 | 出什么 |
|---|---|---|
| [PolyXRD-DataBase](https://github.com/PolyXRD/PolyXRD-DataBase) | `COD/` + `inorg_manifest.sqlite` | `*.polyDB`（目录式，mmap） |
| [XinMatch-DataBase](https://github.com/PolyXRD/XinMatch-DataBase) | `inorg_cif/{COD,PCOD,TCOD}` | `*.xinDB`（单文件 SQLite） |

两者都不含数据，都靠本仓库这份源数据建库。
