# 运维

备份、恢复演练和健康检查脚本在 `scripts/`。命令、正式路径和计划任务以脚本旁手册为准：

**[scripts/README.md](../scripts/README.md)**

最高管理员可在「待办」展开备份健康异常，查看最近巡检问题，并选择删除本次提醒或手动重新备份。删除只隐藏当前巡检报告对应的待办，最多 24 小时；新报告异常会再次出现，不删除备份，也不停用巡检。手动备份在后台生成一份 SQLite 在线快照和 SHA-256 清单，限制并发及短时间重复操作，可在同一待办查看结果；它不会修复缺失的每日备份计划任务。网页服务账号须对配置的备份目录有写入权限，权限应只授予该目录。对应接口仅 `SYSTEM_ADMIN` 可访问：`POST /api/admin/backup-health/dismiss`、`POST /api/admin/backup-health/retry`、`GET /api/admin/backup-health/manual-status`。若任务失败，检查服务器日志、备份目录权限及磁盘空间。

## 脚本一览

| 脚本 | 用途 |
|---|---|
| `Invoke-SqliteOnlineBackup.ps1` | 在线 SQLite 快照 + SHA-256 清单 |
| `Test-SqliteBackupRestore.ps1` | 独立目录恢复演练，不覆盖生产 |
| `Run-MonthlyRestoreRehearsal.ps1` | 选最新备份做月度演练 |
| `Test-SqliteBackupHealth.ps1` | 备份时效与完整性巡检 |
| `Install-DailyBackupTask.ps1` | 安装每日备份任务 |
| `Install-DailyBackupHealthTask.ps1` | 安装每日健康检查任务 |
| `verify_readiness.py` | 只读预检脱敏摘要 |
| `purge_legacy_attendance.py` | 旧 ATTENDANCE 类型一次性清理，默认 dry-run |
| `audit_score_rules.py` | 只读列出角色分值规则，不改数据 |
| `Reset-NeverLoggedInInitialPasswords.py` | 恢复从未登录账号的初始密码，默认 dry-run |
| `build_release.py` | 源码发布包，见 [release.md](release.md) |
| `Deploy-RecognitionRelease.ps1` | 人工授权后上线已核验的发布包，见 [deployment.md](deployment.md) |

## 范围说明

现有演练证明数据库快照能打开、关键表在。表、主键、外键和索引清单见 [sqlite-schema.md](sqlite-schema.md)。附件目录、应用版本、运行环境变量和服务/计划任务配置不在 SQLite 页内，完整整机恢复必须把这些内容一并纳入并在独立目录验收。脚本不会自动覆盖生产库。

完整恢复验收至少包括：使用对应版本源码启动、管理员登录、抽样核对月度分数、打开一份附件、执行只读预检，以及确认备份与健康检查任务路径有效。数据库快照恢复通过不等于整机恢复完成。

日常启动不会删除历史 `ATTENDANCE` 扣分类型及其记录、审计或附件。如业务批准一次性清理，先在隔离副本运行 `.\backend\.venv\Scripts\python.exe scripts\purge_legacy_attendance.py` 查看影响清单，确认备份后再加 `--apply`。

上线前用 `.\backend\.venv\Scripts\python.exe scripts\audit_score_rules.py --data-dir <已存在的隔离快照目录>` 只读核对分值规则，不要按默认分值自动删除。必须指向已有库文件，脚本用 SQLite `mode=ro` 打开，不会新建库。生产继续单进程运行；材料任务虽有领取令牌，仍不要默认开多个 worker。
