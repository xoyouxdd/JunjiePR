# 发布打包

在项目根目录下：

```bash
python scripts/build_release.py
```

包名从 `backend/app/version.py` 的 `APP_VERSION` 生成，写成 `recognition-v年.月.日.第几版.zip`，放在仓库根目录。打包只允许从干净的 Git 工作区执行；未提交改动会直接阻止打包。

包内使用明确白名单：`backend/app/`、`backend/requirements.txt` 和服务器部署脚本。`backend/tests/`、`scripts/tests/`、开发文档、演示账号脚本、运行数据和本地配置都不进入正式包。`release-manifest.json` 记录版本、完整 Git commit，以及每个文件的大小和 SHA-256，供发布前后读回核对；`RELEASE-NOTES.md` 会从 `backend/app/changelog.py` 的最新版本条目自动生成，作为本次发布留档。

## 每次上线的固定检查

每次上线都要完成以下三项：

1. 在应用内「更新记录」补齐本次变更；按角色和权限配置可见范围，确保用户只看到与自己权限对应的内容。
2. 核对版本号与更新记录的最新版本一致。
3. 检查相关文档是否已收口：新增或改变的功能、接口、运维方式和发布规则，均同步到对应文档；新增文档同时加入根 `README.md` 与 `docs/README.md` 索引。

未完成上述检查，不应上线。

打包排除：

- `.env`、`.env.*`、`secrets.json`
- `.venv` / `venv`
- `data_v2`、数据库、日志、密钥后缀（`.pem` / `.key`）
- `__pycache__`、`.pytest_cache`
- 符号链接和白名单外脚本

改静态资源时把 `APP_VERSION` 进一版（`STATIC_CACHE_VERSION` 跟它走），否则浏览器可能继续用旧 CSS/JS。用户可见的功能变更还要写入 `backend/app/changelog.py`，规则见 [changelog.md](changelog.md)。

解包核验时至少确认 `backend/app/main.py`、`release-manifest.json` 和 `RELEASE-NOTES.md` 存在，并逐项复算清单中的 SHA-256。清单 commit 必须等于本次批准发布的提交。
