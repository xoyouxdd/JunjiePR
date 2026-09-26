# 本地运行与测试

本地命令从仓库根目录执行，Python 环境统一使用 `backend/.venv`，与生产服务器和 `scripts/` 下的运维脚本一致。业务代码工作目录仍是 `backend/`，启动命令会明确传入该应用目录。

## 首次安装

Windows PowerShell 在项目根目录执行：

```powershell
$python = "./backend/.venv/Scripts/python.exe"
if (-not (Test-Path $python)) { python -m venv backend/.venv }
& $python -m pip install -r backend/requirements-dev.txt
$env:RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD = "由负责人现场设置的初始密码"
& $python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

依赖分两份：`backend/requirements.txt` 只有生产运行依赖，正式包里装的就是它；`backend/requirements-dev.txt` 用 `-r requirements.txt` 继承这份生产依赖，再锁定 `pytest` 和 `httpx` 两个测试依赖。本地开发和测试装 `requirements-dev.txt`，生产服务器只装 `requirements.txt`。两份文件里的版本都是锁定值，安装时不要再额外追加 `pytest` 或 `httpx`，否则会装上最新版并覆盖锁定版本。

首次启动会初始化 SQLite 结构并建立最高管理员 `HR01`。确认管理员可以登录后，在启动服务的环境中移除临时的 `RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD`。正式运行的数据目录、服务账号和监听地址由部署环境明确设置；不要把本地测试目录当作正式目录。

## 启动

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 `http://127.0.0.1:8000/login`。登录前可访问 `/health`，返回当前 `APP_VERSION`。

## 测试

每个测试文件在导入时设置自己的数据目录，必须分进程跑：

```powershell
.\backend\.venv\Scripts\python.exe backend\tests\run_all.py
```

`run_all.py` 用当前解释器逐个文件起子进程，工作目录固定为 `backend/`，所以在仓库根目录直接执行即可。

中文 Windows 默认按 GBK 读取子进程输出，`test_audit_score_rules.py` 会因此报 `UnicodeDecodeError`。跑测试前先在同一个 PowerShell 里执行 `$env:PYTHONUTF8 = "1"`。

不要用一次 `pytest tests` 代替。单文件可以：

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m pytest tests/test_poc_idempotency.py -q
Pop-Location
```

## 数据目录

默认 `backend/data_v2/`。可用环境变量 `RECOGNITION_V2_DATA_DIR` 指到隔离目录。表、主键、外键和索引见 [sqlite-schema.md](sqlite-schema.md)。

不要把数据库、附件、`.env`、`.venv`、日志提交进 Git。

启用测试账号时必须同时设置：

- `RECOGNITION_ENABLE_TEST_ACCOUNTS=1`
- `RECOGNITION_TEST_DEFAULT_PASSWORD`
- `RECOGNITION_TEST_ADMIN_PASSWORD`

## 从旧目录升级

旧版本使用 `server/` 时，先停止旧服务并做在线 SQLite 备份；将原 `server/data_v2/` 整体复制到新项目的 `backend/data_v2/`，保留数据库与 `files/` 附件目录的相对关系。随后更新服务工作目录、Python 虚拟环境路径和 Windows 计划任务脚本路径，再启动一次让迁移步骤执行。核对 `/health` 版本、管理员登录、月度分数、附件预览和计划任务后，才移除旧目录。回退程序时不得用旧数据库覆盖升级后的新写入。

## 月度趋势统计范围

月度趋势只统计已设置景点圈的数据，未设置景点圈不参与整体人数、认可、出勤、扣分和总分汇总，也不显示曲线或图例。历史月份仍沿用月结封存归属；此调整不删除员工及登记记录，不改变月度统计页原有口径。

## 组员记录排序

有 `MEMBER_RECORDS` 权限的 TA主管、主管在「组员记录」查看直属 CM/TR 月度汇总时，列表默认按综合分从高到低排列；同分按工号升序排列。切换月份或按姓名、工号搜索后仍保持此顺序，点击各项分数查看明细的方式和计分规则不变。

## 绩效登记反馈

管理角色完成加分、声明或升级登记后，反馈弹窗按服务器返回状态显示已生效、待审核、待补材料或处理中；已确认加分显示实际计入分值，升级分值显示为审核后确定。重复提交仅提示记录已保存。反馈不改变登记权限、LOA 或计分规则；普通 CM/TR 的原有认可鼓励提示保持不变。
