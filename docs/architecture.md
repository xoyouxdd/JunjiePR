# 架构

## 现用系统

`server/` 是唯一业务系统：

- 接口：FastAPI + SQLAlchemy，路由在 `server/app/routers/v2.py`
- 数据：SQLite（默认 `server/data_v2/`）+ 独立附件目录
- 页面：`server/app/static/` 原生 HTML/JS/CSS，由 `server/app/main.py` 直接提供
- Excel 写表：`server/app/excel_export.py`（路由只负责取数、水印、审计、返回）

版本来源：`server/app/version.py`（`年.月.日.当天第几版`，以文件为准）。用户在导航「更新记录」看到的内容来自 `server/app/changelog.py`，按角色过滤。

## 不要混淆

| 路径 | 角色 |
|---|---|
| `server/` | 现用签卡、缺勤、月结、导出 |
| `portal/` | 旧 Sites 门户/代理，非正式入口 |
| `docs/` | 说明文档 |

改业务只动 `server/`。不要把 `portal/` 的 React/Vite 栈接到当前页面上。
