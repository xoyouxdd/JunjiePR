# JunjiePR

认可签卡绩效登记系统。当前业务是 `server/`（FastAPI + SQLite + 原生 JS），不是 `portal/`。

正式入口：`https://124.220.229.9:28176/`

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 目录职责、现用栈、遗留门户 |
| [docs/getting-started.md](docs/getting-started.md) | 本地运行、测试、数据目录 |
| [docs/release.md](docs/release.md) | 发布打包与版本号 |
| [docs/operations.md](docs/operations.md) | 备份、恢复演练、健康检查 |
| [docs/security.md](docs/security.md) | 仓库边界与内部约定 |
| [docs/changelog.md](docs/changelog.md) | 应用内「更新记录」怎么维护 |
| [docs/maintain.md](docs/maintain.md) | 改代码时文档改哪 |
| [docs/legacy-portal.md](docs/legacy-portal.md) | 旧门户说明（非正式入口） |

脚本旁的详细手册：[`server/ops/README.md`](server/ops/README.md)。

## 仓库

- `server/`：现用业务系统
- `docs/`：说明文档
- `portal/`：遗留门户源码，不要当现用前端
