# PR 加分系统 SQLite 备份与恢复演练

本目录提供独立运维脚本，不修改业务数据模型，也不会自动部署应用。正式模式固定校验生产目录和专用备份目录；测试模式必须显式指定隔离的 `TestRoot`。

## 文件

- `Invoke-SqliteOnlineBackup.ps1`：调用生产虚拟环境中的 Python `sqlite3.Connection.backup()`，在线生成一致性快照，执行 `PRAGMA quick_check`，写入 SHA-256 清单，并按保留天数清理本脚本管理的历史文件。
- `Test-SqliteBackupRestore.ps1`：把指定备份复制到专用恢复演练目录，核对 SHA-256、`quick_check` 和关键业务表。它不会停止服务或覆盖生产数据库。
- `Run-MonthlyRestoreRehearsal.ps1`：自动选择最新受管备份并调用恢复演练脚本；只在独立恢复目录验证，默认不保留副本，也不写入生产数据库。
- `Install-DailyBackupTask.ps1`：可选安装每日 Windows 计划任务。任务使用 `SYSTEM` 服务身份，不保存账号、密码或服务器凭据。
- `Test-SqliteBackupHealth.ps1`：统一检查计划任务是否执行、最新备份时效、清单 SHA-256/大小和真实 SQLite `quick_check`，异常时返回退出码 `2`。
- `Install-DailyBackupHealthTask.ps1`：安装独立于备份任务的每日健康检查，避免备份任务自身失败时无人发现。
- `verify_readiness.py`：只读核对明确指定的 SQLite 副本，输出脱敏的账号、角色、景点圈、工作组和月结完整性摘要，供发布预检留档。
- `purge_legacy_attendance.py`：一次性清理旧 `ATTENDANCE` 扣分类型。默认只打印影响清单，必须显式 `--apply` 才会删除。
- `audit_score_rules.py`：只读列出角色分值规则，供上线前核对，不会改库。

## 正式备份

先做只读预检。`DryRun` 会检查源数据库完整性并列出将被保留策略删除的文件，不创建或删除任何文件：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-SqliteOnlineBackup.ps1 -RetentionDays 30 -DryRun
```

执行备份：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-SqliteOnlineBackup.ps1 -RetentionDays 30
```

正式模式仅接受以下固定路径：

- 项目根目录：`C:\Server\zhaojunjie\recognition-card-system`
- 应用目录：`C:\Server\zhaojunjie\recognition-card-system\backend`
- 数据库：`C:\Server\zhaojunjie\recognition-card-system\backend\data_v2\recognition_v2.db`
- Python：`C:\Server\zhaojunjie\recognition-card-system\backend\.venv\Scripts\python.exe`
- 专用备份目录：`C:\Server\zhaojunjie\backups\recognition-card-system-sqlite`

每次成功会生成一份 `recognition_v2-时间戳.db` 和相邻的 `.manifest.json`。清单包含文件大小、SHA-256、SQLite 版本和 `quick_check` 结果。保留策略只识别这个严格命名格式；删除前会再次确认解析后的文件仍位于专用备份目录中。

## 独立恢复演练

先查看计划，不创建临时副本：

```powershell
$backup = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite\recognition_v2-20260808T221500-000.db"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-SqliteBackupRestore.ps1 -BackupFile $backup -DryRun
```

执行恢复演练：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-SqliteBackupRestore.ps1 -BackupFile $backup
```

长期测试期可使用以下命令执行一次最新备份的月度演练：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-MonthlyRestoreRehearsal.ps1
```

演练副本位于 `C:\Server\zhaojunjie\restore-tests\recognition-card-system-sqlite`。默认在验证完成或失败后安全删除本次临时目录，减少敏感数据副本；需要保留取证时使用 `-KeepCopy`。脚本检查以下关键表：景点、员工、账号、认可、扣分、病假和审计日志。清单存在时必须通过 SHA-256 核对。

该脚本不是正式回滚命令。正式回滚必须先停止应用写入、再次备份当前生产库、核实目标快照和回滚窗口，再由负责人审批后替换数据库并启动服务。本脚本刻意不提供覆盖生产的选项。

## 每日计划任务

安装前预览任务定义：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DailyBackupTask.ps1 -DailyAt "02:15" -RetentionDays 30 -DryRun
```

将 `backend/` 与 `scripts/` 一起部署到正式项目根目录后，以管理员 PowerShell 执行安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Server\zhaojunjie\recognition-card-system\scripts\Install-DailyBackupTask.ps1 -DailyAt "02:15" -RetentionDays 30
```

如果同名任务已经存在，脚本默认拒绝覆盖。人工检查现有任务后才可加 `-Force`。安装后建议立即手动运行一次，并检查任务结果和最新清单：

```powershell
Start-ScheduledTask -TaskName "RecognitionCardSystem-SQLiteBackup"
Get-ScheduledTaskInfo -TaskName "RecognitionCardSystem-SQLiteBackup"
Get-ChildItem C:\Server\zhaojunjie\backups\recognition-card-system-sqlite | Sort-Object LastWriteTime -Descending | Select-Object -First 4
```

## 隔离测试模式

`TestMode` 只能用于临时测试。所有运行、备份和演练目录都必须是 `TestRoot` 的子目录，防止测试触碰生产。例如：

```powershell
$root = "C:\Temp\recognition-backup-test"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-SqliteOnlineBackup.ps1 `
  -LiveRoot "$root\live" -BackupRoot "$root\backups" -Python python `
  -TestMode -TestRoot $root
```

计划任务安装脚本在 `TestMode` 下强制要求 `DryRun`，不会注册或覆盖 Windows 任务。

## 日常检查与告警边界

## 发布副本核对

正式发布前先用在线快照或预检快照执行一次只读核对。脚本始终以 SQLite `mode=ro` 打开数据库，输出只包含数量和问题代码，不输出员工、账号、附件或凭据：

```powershell
python .\scripts\verify_readiness.py `
  --database C:\Server\zhaojunjie\backups\recognition-v2268-preflight-时间\recognition_v2.db `
  --output C:\Server\zhaojunjie\backups\recognition-v2268-preflight-时间\readiness-report.json
```

报告中的未分组 CM/TR 是 `warning`，需要由负责人核对但不阻断候选启动；SQLite 完整性、外键、账号唯一性、当前角色、景点圈和月结引用异常为 `blocking`，应在上线前解决。

安装独立健康检查任务前先预览。默认在备份任务之后的 `03:30` 检查，允许备份最多运行 60 分钟：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DailyBackupHealthTask.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DailyBackupHealthTask.ps1
```

健康检查将最后状态原子写入备份目录下的 `backup-health-status.json`。`ok=false` 或进程退出码 `2` 可直接接入现有服务器监控；`issues[].code` 会区分 `task_never_run`、`task_last_run_failed`、`backup_timeout_no_output`、清单异常和 `backup_quick_check_failed`。

如需直接发送 Webhook，只在服务器的 SYSTEM/机器级环境变量中保存完整 URL，并把**环境变量名称**传给安装脚本。任务定义和源码不会保存 URL、令牌或密钥：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-DailyBackupHealthTask.ps1 `
  -AlertWebhookUrlEnvironmentVariable "RECOGNITION_BACKUP_ALERT_WEBHOOK"
```

相同故障默认每 24 小时重复提醒一次；故障类型变化会立即提醒。Webhook 未配置时，健康检查仍以 JSON、状态文件和计划任务非零结果提供标准监控接入点。

- 每日确认计划任务 `LastTaskResult` 为 `0`，并确认当天同时存在 `.db` 和 `.manifest.json`。
- 每周至少执行一次恢复演练；每季度由业务负责人参与完整停机回滚演习。
- 当备份失败、`quick_check` 非 `ok`、SHA-256 不匹配、备份大小异常或连续两天没有新文件时，应立即停止删除旧备份并人工排查。
- 运行隔离场景回归：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\tests\Test-BackupHealthScenarios.ps1`。测试只在系统临时目录中生成模拟文件并在结束后清理，不读取生产数据库或生产备份。
