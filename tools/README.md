# sync-COD/tools —— 数据侧工具（验收 + 同步）

本目录随仓库 [PolyXRD/sync-COD](https://github.com/PolyXRD/sync-COD) 一起入库，
**代码受版本控制，数据不受**（`COD/ PCOD/ TCOD/ inorg_cif/ inorg_manifest.sqlite sync_logs/`
都在 `.gitignore` 里）。2026-10-23 从 `PolyXRD-DataBase/tools/` 迁出，理由是这些脚本
只操作数据（清单、副本、归档），和"构建 polyDB"那套代码职责不同。

```
../                   ← 数据根 D:/Project/XRD/sync-COD
├── tools/                   ← 本目录（入库）
├── COD/  PCOD/  TCOD/      原始归档（层级 3 / 2 / 3），不入库
├── inorg_cif/{COD,PCOD,TCOD}/   判为无机的 CIF 副本，不入库
└── inorg_manifest.sqlite        逐条清单（1,959,421 行 / 289 MB），不入库
```

所有脚本用环境变量 **`XRD_DATA_ROOT`** 定位数据根，缺省即本目录的上一级。
**可以从任意工作目录运行**，不再依赖 CWD。

## 一、数据验收（3 个，纯标准库，随时可跑）

| 脚本 | 判据 | 典型耗时 |
|---|---|---|
| `verify_data_root.py` | **17 项**：清单字节数 / `integrity_check` / 总行数与无机行数（对齐基线 1,959,421 与 1,188,103）/ 分库行数 / 三库各抽样 250 个归档文件 + 各 250 个 `inorg_cif` 副本，逐一比对字节数 | 约 10 s |
| `verify_inorg_copy.py` | 副本 vs 清单逐条一致：**缺 / 多 / 尺寸不符** 三项须全 0 | 约 2 min |
| `verify_coverage.py` | 源库 CIF / 无机副本 / 清单 **三方**对照（回答"有没有漏条目"） | 约 5 min（4 进程） |

```bash
python tools/verify_data_root.py
python tools/verify_inorg_copy.py
python tools/verify_coverage.py --workers 4
```

> `verify_coverage.py` 需要构库仓库的 `cif_common.py`（只用其中的 `list_dirs`）。
> 它按 `POLYXRD_TOOLS` → 缺省 `D:/Project/XRD/PolyXRD-DataBase/tools` 找，**不在数据根复制一份**。
> 换机器时设 `POLYXRD_TOOLS=<仓库>/tools`。

**产物验收不在这里**：polyDB 的结构/校验和门禁是 `PolyXRD-DataBase/tools/verify_polydb.py`
（32 项，验的是构建产物而不是数据），xinDB 的是 `XinMatch-DataBase/tools/validate_xindb.py`。

## 二、与 COD 官方同步（10 个脚本，**当前停用**）

> **状态：停用。** 用户明确"暂时不用同步了"。本组不在任何构建流程里，
> 也不被其他脚本导入。重新启用前先看下面的「环境前提」，两项现在都不满足。

| 脚本 | 作用 |
|---|---|
| `fetch_remote_inv.py` | 用 rsync `--list-only -r` 抓远端目录清单（COD/PCOD） |
| `crawl_tcod_http.py` | TCOD 没有 rsync，只能爬 Apache 索引拿清单 |
| `make_local_inv.py` | 生成本地逐条清单（`路径<TAB>字节`） |
| `cmp_inv.py` | 远端清单 vs 本地清单，输出缺失/多余 |
| `fetch_tcod_http.py` / `fetch_missing_tcod.py` | 按缺口清单补下载（顺序 + 退避 + 落盘尺寸校验） |
| `audit_tcod_inv.py` / `diff_tcod_inv.py` | TCOD 目录数与清单对账 |
| `verify_sync_tcod.py` | 解析 wget 日志，核对"日志声称下载"与"磁盘实际存在" |
| `probe_remote.sh` | 探查站点可达性与并发上限 |

### 环境前提（重新启用前必须满足）

1. **本机已无 rsync/wget**（2026-10-19 实测只剩 curl）。COD/PCOD 的 rsync 同步需先装便携
   rsync；TCOD 只能走 HTTP 索引爬取。
2. **DNS 对国外域名整段失效**：固定 10.84 s 超时、`curl exit 6`。
   长任务必须"解析一次 → 按 IP 连接 + 显式 `Host:` 头"。
   `www.crystallography.net` 可用 DoH 解析后直连（158.129.170.82）。

### 血泪教训（照做，别重新踩）

- wget 的 `FINISHED` / `Downloaded: N files` **不是完整性证据**：子目录索引 403 时会
  静默剪枝整棵子树。必须用独立逐条清单比对（`cmp_inv.py`）。
- `--tries` **不重试 403**（只重试连接错误与 5xx），补齐要"顺序 + 指数退避 + 落盘尺寸校验"。
- 服务端并发上限约 4（`-P 12` 必 403，`-P 4` 实测 0 个 403）。
- TCOD 文件 mtime 是服务器时间戳，判断"本轮是否新下载"要看 **Birth time**。
- **`TaskStop` 只杀直接子进程**：`xargs -P4 → sh → wget` 的孙进程会残留并持续下载/污染目录。
  清理必须按 `Win32_Process.CommandLine` 关键字找全树。
- 并发不是越小越稳：到该站 4/16 并发只有 ~2.5 文件/s，32 并发 12.3 文件/s 且 0 个 403。
  但**目录索引爬取要压速**（2 并发 + 0.15 s 间隔，约 1 req/s），否则先 403 后直接丢包。
- `--cut-dirs` 必须对齐 URL 层级：基础 URL 是 `/tcod/cif/`，落到 `TCOD/` 下要 `--cut-dirs=2`。
- 空占位目录很多，**按目录数对账会误判缺失**（COD 远端尤其明显）。

### rsync / HTTP 源

| 库 | 源 |
|---|---|
| COD | `rsync://www.crystallography.net/cif/` |
| PCOD | `rsync://www.crystallography.net/pcod-cif/` |
| TCOD | `http://www.crystallography.net/tcod/cif/`（**无 rsync；svn 3690 也不通**） |

## 三、约定

- `tools/` 只放**操作数据**的脚本。构建产物的脚本不在这里：polyDB 在
  [PolyXRD-DataBase](https://github.com/PolyXRD/PolyXRD-DataBase)、xinDB 在
  [XinMatch-DataBase](https://github.com/PolyXRD/XinMatch-DataBase)。
- 所有脚本的路径默认值都锚在**数据根**（`XRD_DATA_ROOT`，缺省为本目录上一级），
  不依赖当前工作目录，也不写死盘符。
- 依赖只有标准库，唯一例外是 `verify_coverage.py` 借用构库仓库的 `cif_common.py`
  （见第一节的说明）。**不要**把 `cif_common.py` 复制过来——单一真源留在构库仓库。
- 新增脚本后跑一次 `<python> tools/<脚本>.py --help`：迁目录时曾出现"加了
  `os.path` 用法却忘了 `import os`"，`--help` 一秒就能挡掉。
