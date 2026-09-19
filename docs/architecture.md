# 架构

## 现用系统

`backend/` 是唯一业务系统：

- 接口：FastAPI + SQLAlchemy。路由按业务域分在 `backend/app/routers/` 下（`auth`、`hr`、`deductions`、`recognitions`、`sick_leaves`、`statistics`、`governance`、`files`），跨域共用的辅助函数和常量在 `_shared.py`；`v2.py` 只把各域挂到 `/api` 前缀下，不含业务代码
- 数据：SQLite（默认 `backend/data_v2/`）+ 独立附件目录；表、键、索引见 [sqlite-schema.md](sqlite-schema.md)
- 页面：`backend/app/static/` 原生 HTML/JS/CSS，由 `backend/app/main.py` 直接提供。电脑端左侧分组导航（待办置顶，更新记录/密码在底部），窄桌面收成图标栏；手机端仍用底部栏加「更多」
- Excel 写表：`backend/app/excel_export.py`（路由只负责取数、水印、审计、返回）
- 月度分数读查询：`backend/app/score_queries.py`（先限定月份和员工范围，再聚合；旧视图保留兼容）

版本来源：`backend/app/version.py`（`年.月.日.当天第几版`，以文件为准）。用户在导航「更新记录」看到的内容来自 `backend/app/changelog.py`，按角色过滤。

## 目录职责

| 路径 | 角色 |
|---|---|
| `backend/` | 现用签卡、缺勤、月结、导出 |
| `scripts/` | 发布、备份、恢复和健康检查 |
| `docs/` | 说明文档 |

改业务只动 `backend/`；改发布或运维流程时改 `scripts/`。
