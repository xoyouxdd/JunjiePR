# 架构

## 现用系统

`backend/` 是唯一业务系统：

- 接口：FastAPI + SQLAlchemy，路由在 `backend/app/routers/v2.py`
- 数据：SQLite（默认 `backend/data_v2/`）+ 独立附件目录；表、键、索引见 [sqlite-schema.md](sqlite-schema.md)
- 页面：`backend/app/static/` 原生 HTML/JS/CSS，由 `backend/app/main.py` 直接提供
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
