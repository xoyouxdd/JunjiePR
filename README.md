# JunjiePR

认可签卡绩效登记系统，业务代码位于 `backend/`（FastAPI + SQLite + 原生 JS）。

正式入口：`https://124.220.229.9:28176/`

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 目录职责与现用技术栈 |
| [docs/sqlite-schema.md](docs/sqlite-schema.md) | SQLite 表、主键、外键、索引 |
| [docs/getting-started.md](docs/getting-started.md) | 本地运行、测试、数据目录 |
| [docs/release.md](docs/release.md) | 发布打包与版本号 |
| [docs/operations.md](docs/operations.md) | 备份、恢复演练、健康检查 |
| [docs/security.md](docs/security.md) | 仓库边界与内部约定 |
| [docs/changelog.md](docs/changelog.md) | 应用内「更新记录」怎么维护 |
| [docs/maintain.md](docs/maintain.md) | 改代码时文档改哪 |

脚本旁的详细手册：[`scripts/README.md`](scripts/README.md)。

## 仓库

- `backend/`：现用业务系统
- `scripts/`：发布、备份与恢复运维脚本
- `docs/`：说明文档
