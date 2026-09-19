# 生产部署

本项目**不使用 CI**。发布包在本地构建，只有在人工明确下达上线指令后才部署到生产服务器。不在服务器安装 GitHub Runner，也不新增 Windows 服务，也不使用远程桌面。

发版前必须在本地依次完成两步，顺序不能颠倒：

1. 跑全量测试：`.\backend\.venv\Scripts\python.exe backend\tests\run_all.py`，确认全部模块通过。
2. 从**干净的 Git 工作区**构建：`.\backend\.venv\Scripts\python.exe scripts\build_release.py`。未提交的改动会直接阻止打包。

打包脚本在确认工作区干净后会**自动执行一次全量测试**，任一模块失败就拒绝出包；随后校验 `APP_VERSION` 与最新更新记录版本一致、检查文档本地链接，并把版本、完整 Git commit 和每个文件的 SHA-256 写进 `release-manifest.json`。第 1 步单独先跑一遍，是为了在打包前就看到失败详情，不是因为打包不查。

把发布包传到服务器后，由负责人执行部署脚本并传入经批准的完整提交号。部署前必须核实包名和 `release-manifest.json` 的 `git_commit` 都等于批准提交。

部署脚本依次执行：在线 SQLite 备份、版本/提交号/文件 SHA-256 清单核验、仅替换 `app/` 与 `requirements.txt`、依赖同步、短暂停止并重启既有 `RecognitionCardSystem` 任务、内部 `http://127.0.0.1:18082/health` 和外网 `https://124.220.229.9:28176/health` 版本核验。任一步失败，自动还原上一版 `app/` 与 `requirements.txt` 并重启服务。

部署脚本要求服务器上有 PowerShell 7（脚本头部已声明 `#Requires -Version 7.0`），其 `LiveRoot` 是应用目录 `C:\Server\zhaojunjie\recognition-card-system\backend`，与每日备份脚本校验的根目录一致。外网健康检查按 IP 访问，证书主机名与 IP 不匹配，因此该步跳过证书校验。

正式包只含 `backend/app/`、`backend/requirements.txt`、部署脚本、`release-manifest.json` 和自动生成的 `RELEASE-NOTES.md`；不含测试代码、测试脚本、测试依赖清单 `requirements-dev.txt`、开发文档、数据、上传文件或凭据。部署脚本在服务器上执行的是 `pip install -r requirements.txt`，装的只有生产依赖。

部署只使用 `C:\Server\zhaojunjie\recognition-card-system` 及其子目录，保留 `data_v2/`、上传文件、`.venv/`、Caddy 和备份目录；不会安装 Runner、创建新服务或修改其他项目。为完成应用自身的更新，脚本会短暂停止并重启既有的 `RecognitionCardSystem` 计划任务。

日常发版时，先在本地完成上面两步，再明确指出要上线的发布包文件名和对应的完整提交号；没有经过全量测试的包不得上线。
