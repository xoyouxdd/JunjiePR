# 更新记录

用户在系统导航里打开「更新记录」。接口 `GET /api/changelog` 只返回**当前登录角色**能看到的条目。

**数据源只有一处：`server/app/changelog.py`。** 不要在文档里另写一份变更列表，避免和代码分叉。

发版时：

1. 改 `server/app/version.py` 的 `APP_VERSION`（`STATIC_CACHE_VERSION` 与它相同，不用另写）
2. 在 `changelog.py` 的 `RELEASES` **最前面**加一个与 `APP_VERSION` 相同版本号的块
3. 每条写 `audiences`（角色代码或 `all`），必要时加 `permissions`
4. `tests/test_changelog.py` 会检查最新版本号必须等于 `APP_VERSION`

版本号格式是 **年.月.日.当天第几版**，例如 `2026.09.10.3`。同一天再发就加最后一位；换日从 `.1` 起。历史版本 `2.23.73` 仍保留在更新记录里。

当前程序版本见 `server/app/version.py`。
