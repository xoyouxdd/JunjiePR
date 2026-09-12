# 生产自动部署

推送到 `main` 时，GitHub Actions 只执行测试、更新记录/文档收口检查和生产包构建；它不会自动改动生产服务器。

只有手动触发 `Deploy approved production release` 工作流时才部署。该工作流运行在生产服务器本机的 self-hosted Runner 上，依次执行：完整测试、生产包构建、在线 SQLite 备份、清单及 SHA-256 核验、替换 `app/`、依赖同步、计划任务重启，以及 `http://127.0.0.1:18082/health` 版本核验。失败时自动还原上一版 `app/` 和 `requirements.txt`。

正式包只含 `backend/app/`、`backend/requirements.txt`、部署脚本、`release-manifest.json` 和自动生成的 `RELEASE-NOTES.md`；不含测试代码、测试脚本、开发文档、数据、上传文件或凭据。

部署脚本固定使用服务器现有的 `RecognitionCardSystem` 与 `RecognitionCardSystemWatchdog` 计划任务，并保留 `data_v2/`、`.venv/`、Caddy 和备份目录。

日常发版时对 Codex 说：`上线部署 GitHub 最新代码`；如需指定版本，则说：`上线部署 GitHub 提交 <完整提交号>`。
