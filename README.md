# FZ团队工作台

FZ团队工作台（原 FZPR登记），覆盖认可签卡、PR积分、公告培训、轮岗及团队管理；业务代码位于 `backend/`（FastAPI + SQLite + 原生 JS）。仓库与内部项目标识继续使用 JunjiePR。

正式入口：`https://124.220.229.9:28176/`

完整展示名称为「FZ团队工作台」，手机顶部和手机桌面简称为「FZ工作台」，沿用现有 FZ Logo。PR 积分仍为业务名称；数据库、接口、运维目录及历史更新记录不因品牌更名而调整。已经添加的手机桌面入口可能需要移除后重新添加，名称更新不保证自动同步。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/announcement-ui-review-20261020.md](docs/announcement-ui-review-20261020.md) | 公告中心UI/UX改造、权限边界与实际浏览器验收 |
| [日常操作保护与任务验收](docs/usability-acceptance.md) | 填写保护、网络恢复、待办定位与真人手机验收 |
| [docs/architecture.md](docs/architecture.md) | 目录职责与现用技术栈 |
| [docs/sqlite-schema.md](docs/sqlite-schema.md) | SQLite 表、主键、外键、索引 |
| [docs/identity-and-supervisor.md](docs/identity-and-supervisor.md) | 本职与代理职务、主管绩效登记复核、日期展示及统计口径 |
| [docs/monthly-self-rank.md](docs/monthly-self-rank.md) | 个人月结游戏段位、同分中位算法、隐私和未发布开关 |
| [docs/local-update-20261019.md](docs/local-update-20261019.md) | 10月19日导出优化、我的荣誉与名称落实复查 |
| [docs/leave-management.md](docs/leave-management.md) | LOA、月度病假事务导入与单人备用登记 |
| [docs/declaration-statistics.md](docs/declaration-statistics.md) | 声明登记统计、跨圈权限与导出 |
| [docs/hr-monthly-report-plan.md](docs/hr-monthly-report-plan.md) | HR 月报制作、三套模板、统计口径及PPTX导出 |
| [docs/excel-export-presentation.md](docs/excel-export-presentation.md) | Excel总览、完整明细、低频列折叠与同批次核对 |
| [docs/rotation.md](docs/rotation.md) | 轮岗（测试）：入口、模拟账号、规则、个人确认、平板大屏布局、现场调整、TR临时授权、吃饭选择、同步与测试时钟 |
| [docs/self-recognition-batch.md](docs/self-recognition-batch.md) | 本人认可多条登记与共用照片 |
| [docs/announcements.md](docs/announcements.md) | 业务公告发布、培训码签字、补收、授权、跟进导出和响应式布局 |
| [docs/announcement-image-generation.md](docs/announcement-image-generation.md) | 公告模板制图、照片上传和素材访问 |
| [docs/getting-started.md](docs/getting-started.md) | 本地运行、测试、数据目录 |
| [docs/local-hardening-20261016.md](docs/local-hardening-20261016.md) | 路径隔离、提交校验及完整角色测试夹具（未上线） |
| [docs/release.md](docs/release.md) | 发布打包与版本号 |
| [docs/deployment.md](docs/deployment.md) | 生产部署：本地构建与人工授权上线 |
| [docs/operations.md](docs/operations.md) | 备份、恢复演练、健康检查 |
| [docs/security.md](docs/security.md) | 仓库边界与内部约定 |
| [docs/changelog.md](docs/changelog.md) | 应用内「更新记录」怎么维护 |
| [docs/maintain.md](docs/maintain.md) | 改代码时文档改哪 |
| [docs/local-update-20261011.md](docs/local-update-20261011.md) | 10月11日讨论与本地落实清单 |

脚本旁的详细手册：[`scripts/README.md`](scripts/README.md)（运维、备份与发布脚本）。

## 仓库

- `backend/`：现用业务系统
- `scripts/`：发布、备份与恢复运维脚本
- `docs/`：说明文档
