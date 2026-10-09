# 生产部署

本项目**不使用 CI**。发布包在本地构建，只有在人工明确下达上线指令后才部署到生产服务器。不在服务器安装 GitHub Runner，也不新增 Windows 服务，也不使用远程桌面。日常使用仓库内的 `scripts/Publish-RecognitionRelease.ps1`：它执行下述测试和打包步骤、上传发布包、调用服务器端 `scripts/server_release.py`，再独立读回清单及公网健康接口。

发版前必须在本地依次完成两步，顺序不能颠倒：

1. 跑全量测试：`.\backend\.venv\Scripts\python.exe backend\tests\run_all.py`，确认全部模块通过。
2. 从**干净的 Git 工作区**构建：`.\backend\.venv\Scripts\python.exe scripts\build_release.py`。未提交的改动会直接阻止打包。

打包脚本在确认工作区干净后会**自动执行一次全量测试**，任一模块失败就拒绝出包；随后校验 `APP_VERSION` 与最新更新记录版本一致、检查文档本地链接，并把版本、完整 Git commit 和每个文件的 SHA-256 写进 `release-manifest.json`。第 1 步单独先跑一遍，是为了在打包前就看到失败详情，不是因为打包不查。

本机须预先配置专用 SSH 密钥 `%USERPROFILE%\.ssh\junjiepr_deploy`，其公钥登记在服务器 `C:\ProgramData\ssh\administrators_authorized_keys`。私钥和密码都不进入仓库。一次性配置完成后，在仓库根目录执行 `pwsh -File .\scripts\Publish-RecognitionRelease.ps1`。脚本从干净工作区取得完整提交号，并核实包名、清单版本及提交号。发布包和两个 Python 部署脚本均列入 SHA-256 清单；上传前核对本地脚本，上传后独立读回脚本哈希，再执行预检。脚本使用 `.deploy-incoming/<commit>-<package-sha>/` 的独立目录，ZIP 使用含完整包哈希的文件名，避免不同发布覆盖运行中的源码。服务器地址及登录用户记录在该脚本中。

部署脚本先取得非阻塞部署锁，核验包、路径及实际执行源码与清单的哈希配对，再暂存源码、在线备份 SQLite 和保存上一版元数据。依赖清单变化时，先完成 `.venv` 的离线快照；随后核验既有应用与看门狗任务，禁用并结束看门狗当前实例，确认其不处于运行或排队状态，停止应用并等待 18082 端口释放。停止后再核验环境快照，才替换应用及清单并同步依赖。清单未变化时跳过 pip 与环境快照。

新应用必须通过内部 `http://127.0.0.1:18082/health` 和外网 `https://124.220.229.9:28176/health` 版本核验，才完成清单及更新说明发布；上传端随后独立读回清单与公网健康接口。预检只验证包、布局和执行源码，不连接数据库、不修改应用或依赖、不操作计划任务，也不创建部署锁或暂存目录。部署器直接读取并编译同目录 helper 源码，绕过旧字节码缓存。

任何依赖安装失败（包括 pip 已写入部分文件）、应用启动或健康检查失败，均进入回滚：先确认应用停止，离线还原并验证旧依赖文件集合，恢复旧应用、`requirements.txt`、发布清单和更新说明；所有恢复步骤及旧版健康检查通过后，才恢复原来启用的看门狗。原本禁用的看门狗保持禁用。任务状态无法核验会终止部署；停止、恢复或旧版健康检查失败时不会宣称回滚完成或强行重启。恢复失败仍会尽力还原应用文件供人工处理，原故障及回滚故障分别记录在 `.deploy-rollbacks/<stamp>/transaction.json`，快照与失败源码保留。

本地发布入口要求 PowerShell 7。当前生产应用根目录是 `C:\Server\zhaojunjie\recognition-card-system`；服务器用现有虚拟环境的 Python 运行 `server_release.py`。外网健康检查按 IP 访问，证书主机名与 IP 不匹配，因此该步跳过证书校验。

正式包只含 `backend/app/`、`backend/requirements.txt`、三个明确白名单部署脚本（`Deploy-RecognitionRelease.ps1`、`server_release.py`、`deployment_runtime.py`）、`release-manifest.json` 和自动生成的 `RELEASE-NOTES.md`；不含测试、测试依赖、开发文档、数据、上传文件或凭据。依赖变化时执行 `pip install -r requirements.txt`，只同步生产依赖。打包在进入目录前检查符号链接、junction 和 reparse point，不跟随链接读取包外文件。

部署只使用 `C:\Server\zhaojunjie\recognition-card-system` 及其子目录，保留 `data_v2/`、上传文件、`.venv/`、Caddy 和备份目录；不会安装 Runner、创建新服务或修改其他项目。为完成应用自身的更新，脚本会短暂停止并重启既有的 `RecognitionCardSystem` 计划任务。

`.venv` 在原路径恢复，不移动环境目录，不覆盖内容未变化的运行中 Python。快照保留运行文件内容、权限位及修改时间；生成缓存可清理重建，无源字节码仍保留。目录 ACL 沿用现有继承，不复制所有权、ACL 或创建时间。应用与依赖回滚不自动覆盖数据库和上传文件；涉及数据恢复时仍须人工评估和明确授权。真实服务器的任务、文件锁和健康检查效果需要上线授权后的环境验证，本地离线回归不代表已完成生产验证。

日常发版时，明确上线授权后运行仓库入口即可；没有经过全量测试的包不得上线。正式包内保留 `Deploy-RecognitionRelease.ps1` 供合同与留档核验，当前生产服务器执行的是与现有目录布局匹配的 `server_release.py`。
