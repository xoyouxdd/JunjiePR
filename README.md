# FZ团队工作台

FZ团队工作台（原 FZPR登记），覆盖认可签卡、PR积分、公告培训、轮岗及团队管理；业务代码位于 `backend/`（FastAPI + SQLite + 原生 JS）。仓库与内部项目标识继续使用 JunjiePR。

正式入口：`https://124.220.229.9:28176/`

完整展示名称为「FZ团队工作台」，手机顶部和手机桌面简称为「FZ工作台」，沿用现有 FZ Logo。PR 积分仍为业务名称；数据库、接口、运维目录及历史更新记录不因品牌更名而调整。已经添加的手机桌面入口可能需要移除后重新添加，名称更新不保证自动同步。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/avatar-crop-fix.md](docs/avatar-crop-fix.md) | 头像裁切与显示验证 |
| [docs/declaration-admin-corrections.md](docs/declaration-admin-corrections.md) | 声明类型更正、作废及还原 |
| [docs/declaration-layout-review.md](docs/declaration-layout-review.md) | 声明统计布局检查 |
| [docs/rotation-five-posts.md](docs/rotation-five-posts.md) | 每日班表五岗兼容 |
| [docs/workspace-alignment-fix.md](docs/workspace-alignment-fix.md) | 工作台对齐与窄屏检查 |
| [docs/scoring-rule-center.md](docs/scoring-rule-center.md) | 分类计分规则、生效版本与记录快照 |
| [docs/sick-import-identity-matching.md](docs/sick-import-identity-matching.md) | 缺勤导入身份核验与事务保护 |
| [docs/monthly-memory-card.md](docs/monthly-memory-card.md) | 本人月度回忆与本地图片导出 |
| [docs/rotation-daily-refresh.md](docs/rotation-daily-refresh.md) | 每日班表交换刷新与滚动保护 |
| [docs/about-system-credits.md](docs/about-system-credits.md) | 关于系统名单编辑与账号权限 |
| [docs/temporary-recognizers.md](docs/temporary-recognizers.md) | 按日期授权临时主管认可资格、计分与身份边界 |
| [docs/hr-pr-rankings.md](docs/hr-pr-rankings.md) | 景点圈HR的PR排名与导出范围 |
| [docs/rotation-workflow-20261028.md](docs/rotation-workflow-20261028.md) | 轮岗独立任务、超时转接、101与大屏正式联动 |
| [docs/workspace-compact-20261027.md](docs/workspace-compact-20261027.md) | 紧凑认可记录、今日工作、交互趋势及按需组件边界 |
| [docs/third-party-ui.md](docs/third-party-ui.md) | 本地按需界面组件的来源与许可证 |
| [docs/video-learning.md](docs/video-learning.md) | 视频学习、分片续传、压缩封面、共享品牌/员工号水印、联网缓存及验证边界 |
| [docs/mobile-filter-alignment-20261020.md](docs/mobile-filter-alignment-20261020.md) | 四页手机筛选对齐、导出历史完整展示与三引擎验证 |
| [docs/mobile-upload-compatibility.md](docs/mobile-upload-compatibility.md) | 手机浏览器图片上传共用链路、实收测试与真机验收边界 |
| [docs/uc-upload-review-20261020.md](docs/uc-upload-review-20261020.md) | iPhone UC选图读取兼容、批量上传诊断及验证边界 |
| [docs/ui-system-review-20261020.md](docs/ui-system-review-20261020.md) | 常用UI统一走查、手机登记修复、前后对照和优化建议 |
| [docs/announcement-ui-review-20261020.md](docs/announcement-ui-review-20261020.md) | 公告中心UI/UX改造、权限边界与实际浏览器验收 |
| [日常操作保护与任务验收](docs/usability-acceptance.md) | 填写保护、网络恢复、待办定位与真人手机验收 |
| [docs/architecture.md](docs/architecture.md) | 目录职责与现用技术栈 |
| [docs/sqlite-schema.md](docs/sqlite-schema.md) | SQLite 表、主键、外键、索引 |
| [docs/identity-and-supervisor.md](docs/identity-and-supervisor.md) | 本职与代理职务、主管绩效登记复核、日期展示及统计口径 |
| [docs/monthly-self-rank.md](docs/monthly-self-rank.md) | 个人月结游戏段位、同分中位算法、隐私和未发布开关 |
| [docs/monthly-memory-card.md](docs/monthly-memory-card.md) | 本人月度认可回忆卡、本地句式库、完整插画和带姓名PNG导出 |
| [docs/local-update-20261019.md](docs/local-update-20261019.md) | 10月19日导出优化、我的荣誉与名称落实复查 |
| [docs/leave-management.md](docs/leave-management.md) | LOA、月度病假事务导入与单人备用登记 |
| [docs/declaration-statistics.md](docs/declaration-statistics.md) | 声明登记统计、跨圈权限与导出 |
| [docs/hr-monthly-report-plan.md](docs/hr-monthly-report-plan.md) | HR 月报制作、三套模板、统计口径及PPTX导出 |
| [docs/excel-export-presentation.md](docs/excel-export-presentation.md) | Excel总览、完整明细、低频列折叠与同批次核对 |
| [docs/rotation.md](docs/rotation.md) | 轮岗看板：正式员工入口、分级权限、本人待办确认、线路详情、同步、每日班表及隔离测试机制 |
| [docs/self-recognition-batch.md](docs/self-recognition-batch.md) | 本人认可多条登记与共用照片 |
| [docs/recognition-upload-timing.md](docs/recognition-upload-timing.md) | 认可提交分段耗时诊断与后台上传讨论 |
| [docs/announcements.md](docs/announcements.md) | 业务公告发布、培训码签字、补收、授权、跟进导出和响应式布局 |
| [docs/learning-shares.md](docs/learning-shares.md) | 学习分享、共同修改、跨圈七天审批与阅读水印 |
| [docs/announcement-image-generation.md](docs/announcement-image-generation.md) | 公告模板制图、照片上传和素材访问 |
| [docs/getting-started.md](docs/getting-started.md) | 本地运行、测试、数据目录 |
| [docs/local-hardening-20261016.md](docs/local-hardening-20261016.md) | 路径隔离、提交校验及完整角色测试夹具（未上线） |
| [docs/release.md](docs/release.md) | 发布打包与版本号 |
| [docs/deployment.md](docs/deployment.md) | 生产部署：本地构建与人工授权上线 |
| [docs/operations.md](docs/operations.md) | 备份、恢复演练、健康检查 |
| [docs/workbench-experience.md](docs/workbench-experience.md) | 今日工作、连续登记与后台导出边界 |
| [docs/security.md](docs/security.md) | 仓库边界与内部约定 |
| [docs/changelog.md](docs/changelog.md) | 应用内「更新记录」怎么维护 |
| [docs/avatar-artwork.md](docs/avatar-artwork.md) | 头像装扮、素材来源、内部授权与低负担接入 |
| [docs/maintain.md](docs/maintain.md) | 改代码时文档改哪 |
| [docs/local-update-20261011.md](docs/local-update-20261011.md) | 10月11日讨论与本地落实清单 |

脚本旁的详细手册：[`scripts/README.md`](scripts/README.md)（运维、备份与发布脚本）。

## 仓库

- `backend/`：现用业务系统
- `scripts/`：发布、备份与恢复运维脚本
- `docs/`：说明文档
