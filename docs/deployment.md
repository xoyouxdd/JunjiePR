# 生产部署

本项目**不使用 CI**。发布包在本地构建，只有在人工明确下达上线指令后才部署到生产服务器。不在服务器安装 GitHub Runner，也不新增 Windows 服务，也不使用远程桌面。日常使用仓库内的 `scripts/Publish-RecognitionRelease.ps1`：它执行下述测试和打包步骤、上传发布包、调用服务器端 `scripts/server_release.py`，再独立读回清单及公网健康接口。

发版前必须在本地依次完成两步，顺序不能颠倒：

1. 跑全量测试：`.\backend\.venv\Scripts\python.exe backend\tests\run_all.py`，确认全部模块通过。
2. 从**干净的 Git 工作区**构建：`.\backend\.venv\Scripts\python.exe scripts\build_release.py`。未提交的改动会直接阻止打包。

打包脚本在确认工作区干净后会**自动执行一次全量测试**，任一模块失败就拒绝出包；随后校验 `APP_VERSION` 与最新更新记录版本一致、检查文档本地链接，并把版本、完整 Git commit 和每个文件的 SHA-256 写进 `release-manifest.json`。第 1 步单独先跑一遍，是为了在打包前就看到失败详情，不是因为打包不查。

本机须预先配置专用 SSH 密钥 `%USERPROFILE%\.ssh\junjiepr_deploy`，其公钥登记在服务器 `C:\ProgramData\ssh\administrators_authorized_keys`。私钥和密码都不进入仓库。一次性配置完成后，在仓库根目录执行 `pwsh -File .\scripts\Publish-RecognitionRelease.ps1`。脚本从干净工作区取得完整提交号，并核实包名、清单版本及提交号；通过 SSH 将包和服务器部署脚本上传到 `.deploy-incoming`，先预检再部署。服务器地址及登录用户记录在该脚本中。

部署脚本依次执行：版本/提交号/文件 SHA-256 清单核验、在线 SQLite 备份、仅替换 `app/` 与 `requirements.txt`、依赖同步、短暂停止并重启既有 `RecognitionCardSystem` 任务、内部 `http://127.0.0.1:18082/health` 和外网 `https://124.220.229.9:28176/health` 版本核验。失败时自动尝试还原上一版 `app/` 与 `requirements.txt` 并重启服务。上传端随后单独读回服务器清单和公网健康接口。

本地发布入口要求 PowerShell 7。当前生产应用根目录是 `C:\Server\zhaojunjie\recognition-card-system`；服务器用现有虚拟环境的 Python 运行 `server_release.py`。外网健康检查按 IP 访问，证书主机名与 IP 不匹配，因此该步跳过证书校验。

正式包只含 `backend/app/`、`backend/requirements.txt`、部署脚本、`release-manifest.json` 和自动生成的 `RELEASE-NOTES.md`；不含测试代码、测试脚本、测试依赖清单 `requirements-dev.txt`、开发文档、数据、上传文件或凭据。部署脚本在服务器上执行的是 `pip install -r requirements.txt`，装的只有生产依赖。

部署只使用 `C:\Server\zhaojunjie\recognition-card-system` 及其子目录，保留 `data_v2/`、上传文件、`.venv/`、Caddy 和备份目录；不会安装 Runner、创建新服务或修改其他项目。为完成应用自身的更新，脚本会短暂停止并重启既有的 `RecognitionCardSystem` 计划任务。

日常发版时，明确上线授权后运行仓库入口即可；没有经过全量测试的包不得上线。正式包内保留 `Deploy-RecognitionRelease.ps1` 供合同与留档核验，当前生产服务器执行的是与现有目录布局匹配的 `server_release.py`。
