# 认可签卡绩效登记系统入口

Codex Sites 项目 `recognition-card-portal`，为公网认可签卡系统提供 HTTPS 入口、服务状态提示和同源代理。

## 系统结构

- Codex Sites：门户页面、HTTPS 访问和 `/system/*` 同源代理。
- 公网业务服务：`https://124.220.229.9/recognition`，负责员工号/PIN 登录、业务规则、SQLite 数据、照片和 Excel 导出。
- `/system/login`：通过 Sites 进入原业务系统，登录凭据不会写入或展示在门户源码中。

## 本地运行

```bash
npm install
npm run dev
```

## 验证

```bash
npm test
```

测试会构建 Sites Worker，并确认门户入口、分享元数据和凭据保护规则。

## 配置

生产环境可通过 `BACKEND_BASE_URL` 覆盖默认公网业务服务地址。敏感值必须由 Sites 环境变量管理，不要写入 `.openai/hosting.json`。
