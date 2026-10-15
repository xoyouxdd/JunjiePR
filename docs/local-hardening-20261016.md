# 本地隔离与提交校验增量优化（2026-10-16，未上线）

本轮落实运行环境/备份路径隔离、认可图片及复核原因校验、关键测试身份，并增加测试环境标识。PR积分、正式绩效、月结月份规则、勋章算法及比较人群、账号口令政策均不调整。不执行正式部署或重启已有服务。

## 运行环境与路径

`app/runtime_environment.py`集中识别环境：

| 配置 | 行为 |
|---|---|
| 未声明环境，使用默认`backend/data_v2` | 保持正式兼容行为，默认服务器备份健康报告路径不变 |
| 未声明环境，显式使用其他数据目录 | 视为本地环境，备份报告缺省为`<DATA_DIR>/backups/backup-health-status.json` |
| `RECOGNITION_ENVIRONMENT=local/test` | 要求独立目录；存储和健康报告不能经链接指向外部 |
| `RECOGNITION_ENVIRONMENT=production` | 支持正式自定义目录，保留正式备份默认；禁止同时启用测试账号 |

正式若使用非默认数据目录，后续部署前必须明确声明`production`。测试配置指向默认数据目录、已知服务器目录或外部备份报告时，在数据库初始化前拒绝；手动备份执行前和报告读取前也检查。没有修改现有服务器配置。

主界面、登录及轮岗HTML由后端注入测试标识，正式模式不注入；`/health`增加`environment`，不返回路径或凭据。标识不依赖URL参数，也不能代替隔离检查。已有服务需下次正常重启才加载新Python代码，本轮不重启8000服务。

备份回归在新临时目录创建模拟SQLite、生成带哈希清单的快照、恢复到另一个新库并核对内容/完整性；不覆盖源库、不清理原备份。手动快照不代表每日任务已恢复，不伪造任务健康状态。

## 新提交校验

认可图片继续支持JPG、PNG、WebP及100MB字节上限。保存业务记录前在工作线程验证结构并解码预览帧，保留原始内容和哈希，不重压缩。像素阈值沿用Pillow常规警告阈值89,478,485，普通48MP照片不因人为24MP上限被拒绝。损坏、超像素或不能解码的材料返回400并清理临时文件；历史材料不删除、不重写。

每个事件循环同时最多进行两次图片解码，在取得工作线程前限流，避免多张大图同时解码占满内存和通用线程池；不修改业务提交次数或PR计分。上传取消也清理未完成临时文件。真实峰值负载仍需未来独立压测，本轮不宣称容量认证。

服务端拒绝认可时，原因去除首尾空白后必须非空；复核说明最多1000字。校验发生在状态更新前，失败不改变记录或实际计分。原权限、月结门禁、状态409和计分流程不变。

## 独立身份夹具

`scripts/seed_isolated_test_accounts.py`复用现有CM/TR、正式主管、现代代理及管理范围，补齐HR_ADMIN、HR_CIRCLE、其他圈CM、未生效及已到期代理。覆盖11个实际生效角色；`test-fixture.json`只记录账号、角色与范围，不记录口令/哈希。

必须显式声明`local/test`、独立数据目录、测试账号开关及两项口令。只允许全新空目录；重复运行已完成夹具只读清单，不重置口令。不向已有演示库补写，不在生产自动创建。初始化中断保留未完成标记，拒绝静默覆盖，需新目录重试。排他标记防止同目录并发初始化。

旧`seed_level_accounts.py`保留兼容，增加环境门禁且不再输出口令；它仍会重置旧演示身份口令，应优先使用新入口。两个脚本均不进入正式发布包。

## 创建方法

在项目根目录PowerShell执行，凭据对话框提供仅供本次测试的口令，不将实际值写入命令历史、文档或聊天：

```powershell
$env:RECOGNITION_ENVIRONMENT = "test"
$env:RECOGNITION_V2_DATA_DIR = Join-Path $env:TEMP ("fz-fixture-" + [guid]::NewGuid())
$env:RECOGNITION_ENABLE_TEST_ACCOUNTS = "1"
$env:RECOGNITION_BACKUP_HEALTH_STATUS_FILE = Join-Path $env:RECOGNITION_V2_DATA_DIR "backups\backup-health-status.json"
$fixtureUser = Get-Credential -UserName "local-fixture" -Message "设置普通测试身份口令"
$fixtureAdmin = Get-Credential -UserName "local-admin-fixture" -Message "设置管理员测试身份口令"
$env:RECOGNITION_TEST_DEFAULT_PASSWORD = $fixtureUser.GetNetworkCredential().Password
$env:RECOGNITION_TEST_ADMIN_PASSWORD = $fixtureAdmin.GetNetworkCredential().Password
& .\backend\.venv\Scripts\python.exe scripts\seed_isolated_test_accounts.py
# 只在新进程启动独立服务；端口0自动选取空闲端口，不占用已有8000。
& .\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 0
```

最高管理员`HR01`和`HRADMINTEST01`使用管理员环境口令，其余清单身份使用普通环境口令。`TATEST01`为CM＋代理TA主管，`TAGSMTEST01`为主管＋代理TA GSM。日期按初始化当天计算，过期夹具建议另建新目录。

## 验收与回退

针对性验证包括环境隔离、真实临时库备份恢复、三种图片格式与损坏文件、无孤儿文件、拒绝原因/状态不变、11角色登录、代理日期和跨圈403。完整验证沿用`backend/tests/run_all.py`，每个Python文件分进程运行。

没有破坏性迁移。回退仅恢复本轮代码和静态版本，不用旧数据库覆盖新记录；独立测试目录不影响正式库。保留必要路径隔离，配置中的环境含义应保持明确。验收及自行review结果另存本地验证记录，代码完成不代表正式上线。

## 本次自行review及验证结果

97个模块全部获得通过证据：85个Python模块最终902项用例通过，另有7个浏览器夹具、5个纯Node模块。首次全量为94模块通过、3模块失败；分别是旧版更新文案、损坏PNG测试样本、连接层新增共享路径模块的旧依赖假设。修正后各自复测通过，未把首次结果写成全绿。连接层只允许依赖新的纯标准库路径模块，仍禁止路由、业务服务与ORM反向依赖。

最终代码的6个受影响模块再次分进程验证，共81项通过。review额外补齐：报告路径不能等于数据目录/数据库、数据库及WAL/SHM不能链接到外部、测试目录不能位于正式默认目录内、取消上传清理、图片解码并发限制与跨事件循环使用。Windows目录链接使用隔离junction实测，没有因权限跳过此项。

另启动全新临时服务进行实际HTTP与浏览器验收，覆盖11种角色及现代代理、跨权限拒绝、损坏图片400、空原因400、正常确认计入0.50分、本地手动快照及恢复完整性/哈希。手机390及桌面1366宽度均检查环境标识，未产生横向溢出或遮挡主标题。

验证材料位于本地忽略目录`output/local-hardening-20261016/`：`REVIEW.md`、`validation-summary.json`、`live-execution.json`及脱敏日志/截图，不进入正式包。测试临时服务已经停止；原8000服务仍是PID2180、版本2026.10.14.3，没有重启。2026.10.16.1只是本地候选代码，未提交、未推送、未打包部署。真实峰值容量、正式环境配置/备份任务以及实际iOS/Android硬件仍未验证。
