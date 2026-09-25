# 更新记录

用户在系统导航里打开「更新记录」。接口 `GET /api/changelog` 只返回**当前登录角色**能看到的条目。

登录后应用调用 `GET /api/changelog/announcement` 获取当前版本与当前角色可见的公告，以及该账号是否已读。未读时弹出公告，前端阅读倒计时 5 秒后才允许点击关闭；点击时调用 `POST /api/changelog/announcement/read` 保存已读。已读按账号、版本、角色分别记录，同一组合不会重复弹出；新版本或角色变更会重新提示。后端校验提交的版本必须仍是当前版本，页面倒计时属于前端交互，不是服务端阅读时长校验。

**数据源只有一处：`backend/app/changelog.py`。** 不要在文档里另写一份变更列表，避免和代码分叉。

发版时：

1. 改 `backend/app/version.py` 的 `APP_VERSION`（`STATIC_CACHE_VERSION` 与它相同，不用另写）
2. 在 `changelog.py` 的 `RELEASES` **最前面**加一个与 `APP_VERSION` 相同版本号的块
3. 每条写 `audiences`（角色代码或 `all`），必要时加 `permissions`。两者是**且**的关系：只有角色命中 `audiences`（或 `audiences` 含 `all`）**并且**同时具备 `permissions` 里全部权限的用户才看得到该条目；`permissions` 留空时只按角色判断。配置前确认目标用户确实有这些权限，否则条目对他们不可见。
4. `tests/test_changelog.py` 会检查最新版本号必须等于 `APP_VERSION`

版本号格式是 **年.月.日.当天第几版**，例如 `2026.09.10.3`。同一天再发就加最后一位；换日从 `.1` 起。历史版本 `2.23.73` 仍保留在更新记录里。

当前程序版本见 `backend/app/version.py`。

发布前还要按 [release.md](release.md) 的「每次上线的固定检查」核对更新记录、权限范围和相关文档是否全部收口。
