# 遗留门户

`portal/` 是原 Codex Sites 门户和 `/system/*` 同源代理，**不是当前业务前端**。

当前正式入口：`https://124.220.229.9:28176/`（`server/` 直连）。不要按旧说明把流量指回 `/recognition` 或旧 PIN 登录。

## 何时才动它

1. 先确认是否还有真实流量。
2. 已退役：保持 legacy，不要当现用前端重做。
3. 仍在用：单独验证代理 Origin/Host、Cookie、安全头和跳转，不要和 `server/` 业务改动混在同一批。

本地命令（仅维护门户时）：`npm install`、`npm run dev`、`npm test`。后端地址可用 `BACKEND_BASE_URL` 覆盖。
