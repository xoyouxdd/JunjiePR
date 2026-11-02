# 关于系统弹窗名单

本地修改，尚未上线。

- 保留现有“关于系统”入口与原 LOGO。移动端标题单行，右上角小型关闭按钮，维护信息使用紧凑的两列布局；弹窗超高时内部滚动。
- 删除“共同参与”。“建设与维护”默认显示“赵君杰、张森”，标签和姓名使用600字重；新增普通字重的“建议与反馈”，未填写时显示“—”。
- 仅真实登录账号 **精确等于 ADMIN** 的已认证用户能在原弹窗点击“编辑名单”，同一表单维护两项姓名，保存或取消，不新增页面或菜单。HR01及其他SYSTEM_ADMIN、HR与员工只能查看。不创建、启用或变更账号。
- 每项最多200字，支持分行输入；建设与维护必填，建议与反馈可清空。姓名按纯文本展示。
- `GET /api/about-system`返回maintainers、feedback、revision、can_edit。无配置时读取默认值，不写数据库。
- `PUT /api/about-system`只接收maintainers、feedback、revision，拒绝旧contributors及额外字段；服务端每次独立检查账号。过期修订返回409；两项名单和日志在同一事务保存。
- 现有about_system_settings表新增maintainers、feedback字段，已登记幂等启动迁移；旧contributors仅归档保留，不展示，不自动归为反馈人员。重启不会覆盖新名单。不修改其他角色权限、计分或业务配置。
- 验证：`backend/tests/test_about_system.py`覆盖权限、两项编辑、校验、旧接口拒绝、持久化、迁移及回滚；`backend/tests/about_system_ui.cjs`覆盖真实弹窗布局、原LOGO、编辑、取消、错误恢复及纯文本显示；`backend/tests/test_database_module_contracts.py`验证迁移登记和启动契约。
