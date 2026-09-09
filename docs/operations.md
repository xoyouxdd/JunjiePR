# 运维

备份、恢复演练和健康检查脚本在 `server/ops/`。命令、正式路径和计划任务以脚本旁手册为准：

**[server/ops/README.md](../server/ops/README.md)**

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
| `build_release.py` | 源码发布包，见 [release.md](release.md) |

## 范围说明

现有演练证明数据库快照能打开、关键表在。附件目录和推送密钥不在 SQLite 页内，完整整机恢复需另行纳入备份方案。脚本不会自动覆盖生产库。
