# 发布打包

在项目根目录下：

```powershell
.\backend\.venv\Scripts\python.exe scripts\build_release.py
```

打包脚本在工作区干净检查之后会**自动跑一次全量测试**，任一模块失败即拒绝出包——本项目不使用 CI，这里是最后一道自动关卡。也可以先单独跑一遍看失败详情：`.\backend\.venv\Scripts\python.exe backend\tests\run_all.py`。

完整验证包含 Python、纯 Node.js 回归及 Playwright/Edge 本地 DOM 夹具，运行时要求见 [getting-started.md](getting-started.md)。发布入口只调用打包器，由打包器统一执行完整验证，避免连续重复运行同一套测试；任一模块失败或运行时缺失均不能出包。

包名从 `backend/app/version.py` 的 `APP_VERSION` 生成，写成 `recognition-v年.月.日.第几版.zip`，放在仓库根目录。打包只允许从干净的 Git 工作区执行；未提交改动会直接阻止打包。

包内使用明确白名单：`backend/app/`、`backend/requirements.txt` 和服务器部署脚本。白名单按 `backend/` 下第一层名字精确匹配，所以只有生产依赖 `requirements.txt` 入包，测试依赖 `backend/requirements-dev.txt` 不进正式包，生产服务器不会装上 `pytest`、`httpx`。`backend/tests/`、`scripts/tests/`、开发文档、演示账号脚本、运行数据、上传文件、凭据和本地配置也都不进入正式包。`release-manifest.json` 记录版本、完整 Git commit，以及每个文件的大小和 SHA-256，供发布前后读回核对；`RELEASE-NOTES.md` 会从 `backend/app/changelog.py` 的最新版本条目自动生成，作为本次发布留档。

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

登录页和首页的资源版本合同同时检查精确引用清单与总数，包括 manifest、桌面图标、品牌 Logo、样式和入口脚本；桌面图标与品牌 Logo 虽使用同一文件，仍按两次引用校验。首页模块映射另行逐项校验，缺失、过期、无版本或额外引用均不得通过。2026.10.16.3 同步新增 Logo 后的合同，不降低资源版本或 CSP 校验要求。

2026.10.16.5 的请求取消合同覆盖页面信号传递、独立读取控制器、30秒读取超时及监听器/定时器清理。行为回归同时检查默认页面取消、显式调用方信号优先、已取消信号、GET大小写，以及写入不受页面切换和读取超时影响，不以旧实现字符串代替行为约束。发布故障自动修复的授权与边界见 [deployment.md](deployment.md)。

解包核验时至少确认 `backend/app/main.py`、`release-manifest.json` 和 `RELEASE-NOTES.md` 存在，并逐项复算清单中的 SHA-256。清单 commit 必须等于本次批准发布的提交。

2026.10.17.3 同步任职历史追加迁移后的注册合同：既有病假索引修复和新任职历史步骤须按顺序各注册一次，不删除或重排稳定迁移键。完整迁移注册清单、旧库行保留、索引完整性及重复执行幂等性继续由专项和全量回归共同核验；新增无关迁移不应把旧步骤永久限定为整个注册表最后一项。

公告后台补收线程的启动和停止必须使用同一互斥锁，并且只公开已成功启动的线程。生命周期回归使用确定性暂停启动，验证停止不会等待未启动线程或丢失停止信号，同时覆盖重复启动/停止、重新启动及启动失败；原批量认可并发复核、分值封顶与统计断言保持不变。

部署器和离线环境回滚 helper 与 PowerShell 合同脚本一并进入明确白名单及哈希清单；缺任一部署依赖即拒绝出包。只遍历白名单源码根目录，并在递归前拒绝符号链接、junction 和 reparse point，避免将包外文件收入发布包。
