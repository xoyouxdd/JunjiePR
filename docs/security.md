# 安全与仓库边界

本仓库只含源码和测试，不含：

- 生产或测试数据库
- 附件、上传、缓存、日志
- 会话、密码哈希之外的运行时凭据、密钥文件

## 内部约定

本系统仅公司内部使用。员工初始密码按工号规则生成（七位工号末四位），不要改成随机激活方案。口令长度策略保持现状，不要按公网标准加严。

## 上传解析

业务材料上限是 100MB。服务端锁定 FastAPI 0.120.2 + Starlette 0.49.3：大文件滚到磁盘时不再堵住事件循环；1MB 限制只作用于非文件表单字段。Starlette 0.49.1 起修复了 FileResponse/StaticFiles 多 Range 解析的 CPU 耗尽问题（GHSA-7f5h-v6xp-fcq8 / CVE-2025-62727）。不要把 Starlette 单独升到 FastAPI 约束之外。本机解析安装还包含 annotated-doc 0.0.5、anyio 4.15.1、pydantic 2.13.5；这只说明当前锁定组合，不代表已扫过此后所有公告。

## 打包

发布包不得带上 `.env`、`.venv`、`secrets.json` 或 `data_v2`。规则见 [release.md](release.md)。
