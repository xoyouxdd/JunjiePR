# 生产自动部署

推送到 `main` 时，GitHub Actions 工作流 [ci.yml](../.github/workflows/ci.yml) 只执行完整测试、版本号与最新更新记录一致性检查、文档本地链接检查和生产包构建；它不会自动改动生产服务器。构建成功后上传名为 `recognition-production-package-<完整提交号>` 的产物，保留 14 天。

不在服务器安装 GitHub Runner，也不新增 Windows 服务，也不使用远程桌面。只有用户对 Codex 明确发送 `上线部署 GitHub 最新代码` 或指定完整提交号时，Codex 才能通过既有 SSH 通道取得该提交的**成功 CI 产物**并执行部署。部署前必须核实 CI 通过、产物名和 `release-manifest.json` 的 `git_commit` 都等于批准提交。

部署脚本依次执行：在线 SQLite 备份、版本/提交号/文件 SHA-256 清单核验、仅替换 `app/` 与 `requirements.txt`、依赖同步、短暂停止并重启既有 `RecognitionCardSystem` 任务、内部 `http://127.0.0.1:18082/health` 和外网 `https://124.220.229.9:28176/health` 版本核验。任一步失败，自动还原上一版 `app/` 与 `requirements.txt` 并重启服务。

正式包只含 `backend/app/`、`backend/requirements.txt`、部署脚本、`release-manifest.json` 和自动生成的 `RELEASE-NOTES.md`；不含测试代码、测试脚本、开发文档、数据、上传文件或凭据。

部署只使用 `C:\Server\zhaojunjie\recognition-card-system` 及其子目录，保留 `data_v2/`、上传文件、`.venv/`、Caddy 和备份目录；不会安装 Runner、创建新服务或修改其他项目。为完成应用自身的更新，脚本会短暂停止并重启既有的 `RecognitionCardSystem` 计划任务。

日常发版时对 Codex 说：`上线部署 GitHub 最新代码`；如需指定版本，则说：`上线部署 GitHub 提交 <完整提交号>`。
