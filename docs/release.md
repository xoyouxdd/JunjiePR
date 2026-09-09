# 发布打包

在 `server/` 下：

```bash
python ops/build_release.py
```

包名从 `server/app/version.py` 的 `APP_VERSION` 生成，写成 `recognition-v年.月.日.第几版.zip`，放到仓库上一级目录。

打包排除：

- `.env`、`.env.*`、`secrets.json`
- `.venv` / `venv`
- `data_v2`、数据库、日志、密钥后缀（`.pem` / `.key`）
- `__pycache__`、`.pytest_cache`

改静态资源时把 `APP_VERSION` 进一版（`STATIC_CACHE_VERSION` 跟它走），否则浏览器可能继续用旧 CSS/JS。用户可见的功能变更还要写入 `server/app/changelog.py`，规则见 [changelog.md](changelog.md)。
