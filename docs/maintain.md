# 文档怎么跟代码对齐

这个仓库是持续改业务的，文档只保留「现在代码里是什么样」，不写计划、不写过期方案。

## 结构

- 根目录 `README.md`：索引
- `docs/`：说明正文
- `scripts/README.md`：备份脚本命令，跟脚本放在一起

新增一篇文档时，同时改根 `README.md` 和 `docs/README.md` 的表格。

## 改代码时改哪份文档

| 你改了 | 同步改 |
|---|---|
| 入口、目录职责、技术栈 | `docs/architecture.md` |
| `v2_models.py` / `v2_database.py` 表、字段、键、索引 | `docs/sqlite-schema.md` |
| 怎么跑测试、数据目录、环境变量 | `docs/getting-started.md` |
| `build_release.py` 排除规则或包名 | `docs/release.md` |
| `scripts/` 脚本 | `scripts/README.md`，必要时改 `docs/operations.md` 一览表 |
| 密码/打包/仓库边界约定 | `docs/security.md` |
| 用户能看见的功能 | `backend/app/changelog.py`（见 [changelog.md](changelog.md)） |
| `APP_VERSION` | `version.py`（`年.月.日.当天第几版`）+ `changelog.py` 最新一条；`STATIC_CACHE_VERSION` 与 `APP_VERSION` 相同 |

## 禁止

- 不要把初始密码规则写成「应改为随机激活」
- 不要复制 `scripts/README.md` 的整份命令到 `docs/`，避免两处命令不一致
- 不要在文档里写尚未落地的功能
