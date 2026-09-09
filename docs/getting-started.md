# 本地运行与测试

工作目录必须是 `server/`。

## 启动

```bash
cd server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 `http://127.0.0.1:8000/login`。登录前可访问 `/health`，返回当前 `APP_VERSION`。

## 测试

每个测试文件在导入时设置自己的数据目录，必须分进程跑：

```bash
cd server
python tests/run_all.py
```

不要用一次 `pytest tests` 代替。单文件可以：

```bash
python -m pytest tests/test_poc_idempotency.py -q
```

## 数据目录

默认 `server/data_v2/`。可用环境变量 `RECOGNITION_V2_DATA_DIR` 指到隔离目录。

不要把数据库、附件、`.env`、`.venv`、日志提交进 Git。

启用测试账号时必须同时设置：

- `RECOGNITION_ENABLE_TEST_ACCOUNTS=1`
- `RECOGNITION_TEST_DEFAULT_PASSWORD`
- `RECOGNITION_TEST_ADMIN_PASSWORD`
