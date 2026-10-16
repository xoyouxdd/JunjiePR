# 运维

本地/测试路径隔离与恢复回归见[本地增量优化](local-hardening-20261016.md)。独立数据目录默认使用本目录下的备份及健康报告，禁止指向外部；正式默认备份路径保持不变。正式若使用自定义数据目录，需明确配置`RECOGNITION_ENVIRONMENT=production`。手动快照不改计划任务的健康结果。

备份、恢复演练和健康检查脚本在 `scripts/`。命令、正式路径和计划任务以脚本旁手册为准：

**[scripts/README.md](../scripts/README.md)**

最高管理员可在「待办」展开备份健康异常，查看最近巡检问题，并选择删除本次提醒或手动重新备份。删除只隐藏当前巡检报告对应的待办，最多 24 小时；新报告异常会再次出现，不删除备份，也不停用巡检。手动备份在后台生成一份 SQLite 在线快照和 SHA-256 清单，限制并发及短时间重复操作，可在同一待办查看结果；它不会修复缺失的每日备份计划任务。网页服务账号须对配置的备份目录有写入权限，权限应只授予该目录。对应接口仅 `SYSTEM_ADMIN` 可访问：`POST /api/admin/backup-health/dismiss`、`POST /api/admin/backup-health/retry`、`GET /api/admin/backup-health/manual-status`。若任务失败，检查服务器日志、备份目录权限及磁盘空间。

## 每日备份异常的定位与处理

先读「备份健康异常」中的问题代码、巡检时间和任务结果，再核对服务器任务及文件；应用 `/health` 正常只证明应用可访问，不能证明备份正常。`health_report_missing` 表示没有巡检报告，不能直接判断数据库备份失败；`task_last_run_failed` 表示每日任务退出码非零；`backup_timeout_no_output` / `backup_stale` 表示未产生及时的可识别快照。手动备份不会重写巡检报告，故手动成功后旧报告仍可能显示异常；须重新运行巡检确认。

2026-10-13 本地发现并复现了路径合同不一致：当前发布入口 `server_release.py` 使用根目录下的 `app`、`data_v2` 与 `.venv`，原日常备份、巡检及恢复脚本却使用 `backend` 子目录。脚本默认值和正式路径校验现已同时对齐根目录；原脚本即使传正确根目录也会抛出 `Unexpected live root path`。此为已确认代码问题，实际线上是否由该问题触发，仍需以服务器文件和任务结果验证。

处理此类路径故障时：

1. 核对线上根目录的 `data_v2\recognition_v2.db`、`.venv\Scripts\python.exe`、备份目录写入权限，以及每日备份和巡检两个任务。
2. 单独同步并校验修正后的 `Invoke-SqliteOnlineBackup.ps1`、`Test-SqliteBackupHealth.ps1`、`Test-SqliteBackupRestore.ps1`；应用发布不会自动安装这两个每日任务，也不自动同步全部运维脚本。
3. 先运行备份 `-DryRun`，核对来源与保留策略，再执行每日任务。只有任务缺失或参数确需修改时才安装/更新，保留现有触发时间和告警环境变量设置。
4. 确认备份任务结果为 `0`、新快照与清单均存在、SHA-256 和 `quick_check` 正常，再运行巡检；确认新 `backup-health-status.json` 为 `ok=true` 后刷新页面，并复查下一次定时执行结果。部署时生成的根目录 `backups` 快照与日常专用备份目录分开，不互相代替。

本地回归 `test_backup_runtime_paths.py` 将正式路径与发布程序合同对照，并在独立目录执行真实 SQLite 备份、巡检和恢复，验证源库不变。另有 `scripts/tests/Test-BackupHealthScenarios.ps1` 覆盖任务失败、无输出与完整性失败；本地通过不等于线上任务已修复。

2026-10-16 正式核查确认：每日 SQLite 备份任务存在且执行成功，运维脚本实际位于应用根目录 `ops/`；CIM `Get-ScheduledTask` 在该环境未正确列出任务，导致巡检误报 `task_missing`。巡检现使用 Task Scheduler COM 接口读取真实任务，查询失败不伪装成任务缺失。修复保留每日02:15备份、03:30巡检及原SYSTEM身份，不替换应用任务。旧 `RecognitionCardSystemBackup` 指向旧 `data/`，不能当作当前 `data_v2/files` 的附件备份。

每日 SQLite 备份通过 `-IncludeRecoveryBundle` 补充对应数据库、有效附件、部署源码/版本清单、依赖清单和任务/启动配置；巡检通过 `-RequireRecoveryBundle` 核对完整恢复集的时效及每个对象的SHA-256。内容相同的附件只存一份，详见脚本手册的完整恢复集部分。机器级秘密、证书及系统镜像不在此恢复集中；不宣称完成整机灾难恢复。

本轮正式生成的首个恢复集已在全新临时目录演练：恢复1867个条目、1682份有效附件，以对应2026.10.16.3源码启动环回服务，完成隔离管理员登录、原件哈希核对、带水印附件下载与可读性验证、10名员工封存分数核对，以及管理员备份异常待办的出现/消除。演练禁用外部连接，只修改恢复副本，结束后删除副本；备份对象保持原样。此证据不是整机恢复，也不代替下一次定时触发结果。复跑中遇到间歇性文件访问拒绝，已增加仅针对该OS错误的最多3次重试，持续失败仍报错。

## 脚本一览

| 脚本 | 用途 |
|---|---|
| `Invoke-SqliteOnlineBackup.ps1` | 在线 SQLite 快照 + SHA-256 清单 |
| `Test-SqliteBackupRestore.ps1` | 独立目录恢复演练，不覆盖生产 |
| `Run-MonthlyRestoreRehearsal.ps1` | 选最新备份做月度演练 |
| `Test-SqliteBackupHealth.ps1` | 备份时效与完整性巡检 |
| `recovery_bundle.py` | 按哈希共享对象的数据库、附件及源码恢复集；仅恢复到全新隔离目录 |
| `Install-DailyBackupTask.ps1` | 安装每日备份任务 |
| `Install-DailyBackupHealthTask.ps1` | 安装每日健康检查任务 |
| `verify_readiness.py` | 只读预检脱敏摘要 |
| `purge_legacy_attendance.py` | 旧 ATTENDANCE 类型一次性清理，默认 dry-run |
| `audit_score_rules.py` | 只读列出角色分值规则，不改数据 |
| `Reset-NeverLoggedInInitialPasswords.py` | 恢复从未登录账号的初始密码，默认 dry-run |
| `build_release.py` | 源码发布包，见 [release.md](release.md) |
| `Publish-RecognitionRelease.ps1` | 人工授权后在本机完成测试、打包、上传、部署和读回，见 [deployment.md](deployment.md) |
| `server_release.py` | 当前生产服务器上的部署事务与失败回滚，由本机发布入口调用 |
| `Deploy-RecognitionRelease.ps1` | 正式包保留的 PowerShell 部署合同 |

## 范围说明

现有演练证明数据库快照能打开、关键表在。表、主键、外键和索引清单见 [sqlite-schema.md](sqlite-schema.md)。附件目录、应用版本、运行环境变量和服务/计划任务配置不在 SQLite 页内，完整整机恢复必须把这些内容一并纳入并在独立目录验收。脚本不会自动覆盖生产库。

完整恢复验收至少包括：使用对应版本源码启动、管理员登录、抽样核对月度分数、打开一份附件、执行只读预检，以及确认备份与健康检查任务路径有效。数据库快照恢复通过不等于整机恢复完成。

日常启动不会删除历史 `ATTENDANCE` 扣分类型及其记录、审计或附件。如业务批准一次性清理，先在隔离副本运行 `.\backend\.venv\Scripts\python.exe scripts\purge_legacy_attendance.py` 查看影响清单，确认备份后再加 `--apply`。

上线前用 `.\backend\.venv\Scripts\python.exe scripts\audit_score_rules.py --data-dir <已存在的隔离快照目录>` 只读核对分值规则，不要按默认分值自动删除。必须指向已有库文件，脚本用 SQLite `mode=ro` 打开，不会新建库。生产继续单进程运行；材料任务虽有领取令牌，仍不要默认开多个 worker。
