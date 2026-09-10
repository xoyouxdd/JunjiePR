# SQLite 表、键、索引

对照当前程序 `2026.09.10.7`。权威定义在代码里，本文件只记录现在落地的结构：

- 表与字段：`backend/app/v2_models.py`
- 启动建库、一次性补丁、额外索引、月度分值视图：`backend/app/v2_database.py`

改模型或迁移后，同步改本文件。不要对着生产库手改结构。

## 库与约定

- 库文件：`backend/data_v2/recognition_v2.db`（可用 `RECOGNITION_V2_DATA_DIR` 改到隔离目录）
- 连接：`PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=30000`
- 主键：除迁移登记表外均为 `id INTEGER PRIMARY KEY`
- 日期：`YYYY-MM-DD`（`VARCHAR(10)`）；月份：`YYYY-MM`（`VARCHAR(7)`）
- 员工身份以 `employees.id` 为准。工号可变，业务行不要靠工号当主键。
- 附件字节不在库页内，只在 `stored_files` 记元数据，文件在数据目录的 `files/`。
- `password_hash` 只存哈希。本文件不记录口令规则之外的凭据。

列上写了 `index=True` 时，SQLAlchemy 会建 `ix_<表名>_<列名>`。下面「列级索引」即这类单列索引；复合索引单独列出。

---

## 总表

| 表 | 用途 |
|---|---|
| `schema_migration_steps` | 一次性结构补丁登记 |
| `roles` | 角色 |
| `permissions` | 权限点 |
| `role_permissions` | 角色-权限 |
| `attractions` | 景点圈 / 认可发生地 |
| `employees` | 员工主档 |
| `employee_number_history` | 工号变更轨迹 |
| `employee_loa_periods` | LOA |
| `employee_role_assignments` | 角色任职 |
| `user_accounts` | 登录账号 |
| `user_sessions` | 会话 |
| `submission_requests` | 提交幂等 |
| `management_scopes` | GSM/TA GSM 管理范围 |
| `work_groups` | 工作组 |
| `group_leader_assignments` | 组长任职 |
| `group_memberships` | 组员归属 |
| `group_transfers` | 组内交接 |
| `group_transfer_members` | 交接成员快照 |
| `circle_transfer_requests` | 跨圈调动 |
| `recognition_types` | 认可类型 |
| `recognition_score_rules` | 认可分值规则 |
| `recognition_records` | 签卡 |
| `recognition_attachments` | 签卡材料 |
| `recognition_monthly_quotas` | 月度配额占用 |
| `recognition_reviews` | 签卡复核流水 |
| `stored_files` | 附件元数据 |
| `attendance_rules` | 全勤规则 |
| `month_closures` | 月结开关 |
| `governance_cases` | 申诉 / 月结更正工单 |
| `employee_month_organization_snapshots` | 月结组织快照 |
| `sick_leave_records` | 缺勤 |
| `attendance_monthly_scores` | 月度全勤分 |
| `deduction_types` | 扣分类型 |
| `deduction_levels` | 扣分等级 |
| `deduction_records` | 扣分 |
| `deduction_material_jobs` | 照片转 PDF 任务 |
| `deduction_upgrade_requests` | 声明升级工单 |
| `deduction_upgrade_transfers` | 升级工单转交 |
| `deduction_follow_ups` | 重复违规跟进 |
| `audit_logs` | 审计 |
| `system_alerts` | 系统告警 |
| `system_job_runs` | 系统任务去重 |
| `v_employee_month_scores` | 月度综合分视图（不是表） |

---

## 迁移登记

### `schema_migration_steps`

启动时先建，记录已执行的一次性补丁。主键是步骤名，不是整数 `id`。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `step` | VARCHAR(100) PK | 否 | 补丁名，例如 `2026-09-login-account-archive` |
| `applied_at` | DATETIME | 否 | 默认 `CURRENT_TIMESTAMP` |

当前步骤清单见 `SCHEMA_MIGRATION_STEPS`。新库由 `create_all` 建齐后，这些步骤仍会跑一遍做存量库补列/补索引。

---

## 组织与账号

### `roles`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `code` | VARCHAR(30) | 否 | 角色代码，UNIQUE |
| `name` | VARCHAR(30) | 否 | 显示名，UNIQUE |
| `rank` | INTEGER | 否 | 层级 |
| `can_lead_group` | BOOLEAN | 否 | 默认可带组 |
| `attendance_eligible` | BOOLEAN | 否 | 是否计全勤 |
| `active` | BOOLEAN | 否 | 默认 true |

键：PK `id`；UNIQUE `code`、`name`。

列级索引：`code`、`rank`、`active`。

### `permissions`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `code` | VARCHAR(60) | 否 | UNIQUE |
| `name` | VARCHAR(100) | 否 | |

键：PK `id`；UNIQUE `code`。列级索引：`code`。

### `role_permissions`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `role_id` | INTEGER | 否 | FK → `roles.id` ON DELETE CASCADE |
| `permission_id` | INTEGER | 否 | FK → `permissions.id` ON DELETE CASCADE |

键：PK `id`；UNIQUE `uq_role_permission` (`role_id`, `permission_id`)。

列级索引：`role_id`、`permission_id`。

### `attractions`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `name` | VARCHAR(100) | 否 | UNIQUE |
| `active` | BOOLEAN | 否 | |
| `employee_circle` | BOOLEAN | 否 | 是否员工景点圈 |
| `recognition_venue` | BOOLEAN | 否 | 是否认可发生地 |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `name`。列级索引：`name`、`active`、`employee_circle`、`recognition_venue`。

### `employees`

员工主档。删除登录账号不删本行。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | 业务身份 |
| `employee_no` | VARCHAR(50) | 否 | 当前工号，UNIQUE |
| `name` | VARCHAR(100) | 否 | |
| `attraction_id` | INTEGER | 是 | FK → `attractions.id` |
| `is_active` | BOOLEAN | 否 | 在职 |
| `hired_on` | VARCHAR(10) | 是 | |
| `terminated_on` | VARCHAR(10) | 是 | |
| `account_deleted_at` | DATETIME | 是 | 登录账号已归档 |
| `account_deleted_by_name` | VARCHAR(100) | 是 | 归档操作人姓名 |
| `created_at` | DATETIME | 否 | |
| `updated_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `employee_no`；FK `attraction_id`。

列级索引：`employee_no`、`name`、`attraction_id`、`is_active`。

复合索引：`ix_employee_targets_status_attraction` (`is_active`, `attraction_id`, `name`, `employee_no`)。

### `employee_number_history`

工号变更只追加，不复制员工。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `old_employee_no` | VARCHAR(50) | 否 | |
| `new_employee_no` | VARCHAR(50) | 否 | |
| `effective_on` | VARCHAR(10) | 否 | |
| `reason` | TEXT | 否 | |
| `changed_by` | INTEGER | 否 | FK → `employees.id` |
| `changed_by_name` | VARCHAR(100) | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `employee_id`、`changed_by`。

列级索引：`employee_id`、`old_employee_no`、`new_employee_no`、`effective_on`、`changed_by`、`created_at`。

复合索引：

- `ix_employee_number_history_employee_date` (`employee_id`, `effective_on`, `id`)
- `ix_employee_number_history_old_number` (`old_employee_no`)
- `ix_employee_number_history_new_number` (`new_employee_no`)

### `employee_loa_periods`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `starts_on` | VARCHAR(10) | 否 | |
| `ends_on` | VARCHAR(10) | 是 | |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `note` | TEXT | 是 | |
| `created_by` | INTEGER | 否 | FK → `employees.id` |
| `created_by_name` | VARCHAR(100) | 否 | |
| `created_at` | DATETIME | 否 | |
| `ended_by` | INTEGER | 是 | FK → `employees.id` |
| `ended_by_name` | VARCHAR(100) | 是 | |
| `ended_at` | DATETIME | 是 | |

键：PK `id`；FK `employee_id`、`created_by`、`ended_by`。

列级索引：`employee_id`、`starts_on`、`ends_on`、`status`。

复合索引：`ix_employee_loa_lookup` (`employee_id`, `starts_on`, `ends_on`, `status`)。

### `employee_role_assignments`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `role_id` | INTEGER | 否 | FK → `roles.id` |
| `starts_on` | VARCHAR(10) | 否 | |
| `ends_on` | VARCHAR(10) | 是 | |
| `assignment_type` | VARCHAR(20) | 否 | 默认 `permanent` |
| `return_role_id` | INTEGER | 是 | FK → `roles.id` |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `reason` | TEXT | 是 | |
| `created_by` | INTEGER | 是 | FK → `employees.id` |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `employee_id`、`role_id`、`return_role_id`、`created_by`。

检查：`ck_role_assignment_dates`（`ends_on IS NULL OR ends_on >= starts_on`）。

列级索引：`employee_id`、`role_id`、`starts_on`、`ends_on`、`status`。

复合索引：`ix_role_assignments_current_lookup` (`employee_id`, `status`, `starts_on`, `ends_on`, `id`)。

### `user_accounts`

一个员工至多一个登录账号。归档只删本表和会话，不删员工与业务历史。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE，UNIQUE |
| `login_account` | VARCHAR(50) | 否 | UNIQUE |
| `password_hash` | VARCHAR(255) | 否 | 哈希，不明文 |
| `enabled` | BOOLEAN | 否 | |
| `must_change_password` | BOOLEAN | 否 | |
| `credential_initialized` | BOOLEAN | 否 | |
| `failed_attempts` | INTEGER | 否 | |
| `locked_until` | DATETIME | 是 | |
| `last_login_at` | DATETIME | 是 | |
| `password_changed_at` | DATETIME | 是 | 仅作状态展示 |
| `disabled_at` | DATETIME | 是 | 停用起算，满 7 天可归档 |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `employee_id`、`login_account`；FK `employee_id`。

列级索引：`employee_id`、`login_account`、`enabled`、`credential_initialized`。

复合索引：`ix_user_accounts_enabled_employee` (`enabled`, `employee_id`)。

### `user_sessions`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `account_id` | INTEGER | 否 | FK → `user_accounts.id` ON DELETE CASCADE |
| `token_hash` | VARCHAR(64) | 否 | UNIQUE |
| `expires_at` | DATETIME | 否 | |
| `last_seen_at` | DATETIME | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `token_hash`；FK `account_id`。

列级索引：`account_id`、`token_hash`、`expires_at`。

### `submission_requests`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `actor_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `operation` | VARCHAR(30) | 否 | |
| `request_key` | VARCHAR(100) | 否 | |
| `entity_id` | INTEGER | 否 | 已落库对象 |
| `payload_digest` | VARCHAR(64) | 是 | 内容摘要，防同键改内容 |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_submission_request` (`actor_id`, `operation`, `request_key`)；FK `actor_id`。

列级索引：`actor_id`、`operation`、`entity_id`、`created_at`。

### `management_scopes`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `attraction_id` | INTEGER | 否 | FK → `attractions.id` ON DELETE CASCADE |
| `starts_on` | VARCHAR(10) | 否 | |
| `ends_on` | VARCHAR(10) | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_management_scope` (`employee_id`, `attraction_id`, `starts_on`)；FK `employee_id`、`attraction_id`。

列级索引：`employee_id`、`attraction_id`、`starts_on`。

---

## 工作组与调动

### `work_groups`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | 组身份，不随显示名变 |
| `name` | VARCHAR(100) | 否 | |
| `attraction_id` | INTEGER | 否 | FK → `attractions.id` |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `revision` | INTEGER | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `attraction_id`。列级索引：`name`、`attraction_id`、`status`。

### `group_leader_assignments`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `group_id` | INTEGER | 否 | FK → `work_groups.id` ON DELETE CASCADE |
| `leader_employee_id` | INTEGER | 否 | FK → `employees.id` |
| `starts_on` | VARCHAR(10) | 否 | |
| `ends_on` | VARCHAR(10) | 是 | |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `transfer_id` | INTEGER | 是 | 关联交接，无 FK |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `group_id`、`leader_employee_id`。

检查：`ck_group_leader_dates`。

列级索引：`group_id`、`leader_employee_id`、`starts_on`、`status`。

复合索引：`ix_group_leaders_current_lookup` (`group_id`, `status`, `starts_on`, `ends_on`, `id`)。

### `group_memberships`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `group_id` | INTEGER | 否 | FK → `work_groups.id` ON DELETE CASCADE |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `starts_on` | VARCHAR(10) | 否 | |
| `ends_on` | VARCHAR(10) | 是 | |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `reason` | TEXT | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `group_id`、`employee_id`。检查：`ck_group_membership_dates`。

列级索引：`group_id`、`employee_id`、`starts_on`、`status`。

复合索引：`ix_group_memberships_current_lookup` (`employee_id`, `status`, `starts_on`, `ends_on`, `id`)。

### `group_transfers`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `group_id` | INTEGER | 否 | FK → `work_groups.id` |
| `old_leader_id` | INTEGER | 是 | FK → `employees.id` |
| `new_leader_id` | INTEGER | 否 | FK → `employees.id` |
| `attraction_id` | INTEGER | 否 | FK → `attractions.id` |
| `effective_date` | VARCHAR(10) | 否 | |
| `member_count` | INTEGER | 否 | |
| `pending_review_count` | INTEGER | 否 | |
| `reason` | TEXT | 否 | |
| `operator_id` | INTEGER | 否 | FK → `employees.id` |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `group_id`、`old_leader_id`、`new_leader_id`、`attraction_id`、`operator_id`。

列级索引：`group_id`、`effective_date`。

### `group_transfer_members`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `transfer_id` | INTEGER | 否 | FK → `group_transfers.id` ON DELETE CASCADE |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `employee_no` | VARCHAR(50) | 否 | 当时工号快照 |
| `employee_name` | VARCHAR(100) | 否 | |

键：PK `id`；UNIQUE `uq_transfer_member` (`transfer_id`, `employee_id`)；FK `transfer_id`、`employee_id`。

列级索引：`transfer_id`、`employee_id`。

### `circle_transfer_requests`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `employee_no` | VARCHAR(50) | 否 | |
| `employee_name` | VARCHAR(100) | 否 | |
| `source_attraction_id` | INTEGER | 否 | FK → `attractions.id` |
| `source_attraction_name` | VARCHAR(100) | 否 | |
| `target_attraction_id` | INTEGER | 否 | FK → `attractions.id` |
| `target_attraction_name` | VARCHAR(100) | 否 | |
| `source_group_id` | INTEGER | 是 | FK → `work_groups.id` |
| `source_leader_id` | INTEGER | 是 | FK → `employees.id` |
| `target_group_id` | INTEGER | 是 | FK → `work_groups.id` |
| `target_leader_id` | INTEGER | 是 | FK → `employees.id` |
| `reason` | TEXT | 否 | |
| `status` | VARCHAR(20) | 否 | 默认 `pending` |
| `requested_by` | INTEGER | 否 | FK → `employees.id` |
| `requested_by_name` | VARCHAR(100) | 否 | |
| `requested_at` | DATETIME | 否 | |
| `reviewed_by` | INTEGER | 是 | FK → `employees.id` |
| `reviewed_by_name` | VARCHAR(100) | 是 | |
| `reviewed_at` | DATETIME | 是 | |
| `review_note` | TEXT | 是 | |
| `completed_at` | DATETIME | 是 | |
| `migrated_record_counts_json` | TEXT | 是 | |

键：PK `id`；FK 员工、景点圈、工作组如上。

列级索引：`employee_id`、`employee_no`、`employee_name`、`source_attraction_id`、`target_attraction_id`、`status`、`requested_by`、`requested_at`。

复合索引：`ix_circle_transfer_status_scope` (`status`, `source_attraction_id`, `target_attraction_id`)。

---

## 认可

### `recognition_types`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `code` | VARCHAR(30) | 否 | UNIQUE |
| `name` | VARCHAR(30) | 否 | UNIQUE |
| `active` | BOOLEAN | 否 | |

### `recognition_score_rules`

全局规则，按角色和生效日版本化，没有景点圈字段。启动种子只在该角色还没有任何规则时写入 `2020-01-01` 的默认分值；之后只能走最高管理员的分值入口。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `role_id` | INTEGER | 否 | FK → `roles.id` |
| `score` | NUMERIC(10,2) | 否 | |
| `effective_date` | VARCHAR(10) | 否 | |
| `active` | BOOLEAN | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_recognition_score_rule` (`role_id`, `effective_date`)；FK `role_id`。

列级索引：`role_id`、`effective_date`。

### `recognition_records`

`fraction` 是登记时原始分；`credited_fraction` 是封顶后实际计入分。`recognizer_employee_id` 提交真实员工 ID。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` 被认可人 |
| `employee_no` | VARCHAR(50) | 否 | 快照 |
| `employee_name` | VARCHAR(100) | 否 | 快照 |
| `employee_role_snapshot` | VARCHAR(30) | 否 | |
| `employee_role_code_snapshot` | VARCHAR(30) | 是 | |
| `home_attraction_id` | INTEGER | 是 | FK → `attractions.id` 所属圈 |
| `home_attraction_name` | VARCHAR(100) | 是 | |
| `occurred_attraction_id` | INTEGER | 否 | FK → `attractions.id` 发生地 |
| `recognition_date` | VARCHAR(10) | 否 | |
| `recognition_month` | VARCHAR(7) | 否 | |
| `recognition_type_id` | INTEGER | 否 | FK → `recognition_types.id` |
| `recognition_type_name` | VARCHAR(30) | 否 | |
| `content` | VARCHAR(20) | 否 | 普通认可短描述 |
| `recognizer_employee_id` | INTEGER | 否 | FK → `employees.id` 认可人 |
| `recognizer_name` | VARCHAR(100) | 否 | |
| `recognizer_role_snapshot` | VARCHAR(30) | 否 | |
| `recognizer_role_code_snapshot` | VARCHAR(30) | 是 | |
| `operator_employee_id` | INTEGER | 否 | FK → `employees.id` 代录/提交人 |
| `operator_name` | VARCHAR(100) | 否 | |
| `operator_role_snapshot` | VARCHAR(30) | 是 | |
| `operator_role_code_snapshot` | VARCHAR(30) | 是 | |
| `source` | VARCHAR(20) | 否 | |
| `fraction` | NUMERIC(10,2) | 否 | 原始分 |
| `credited_fraction` | NUMERIC(10,2) | 否 | 实际计入 |
| `monthly_cap_rule_code` | VARCHAR(50) | 是 | |
| `monthly_cap_status` | VARCHAR(30) | 否 | 默认 `not_applicable` |
| `monthly_cap_limit` | NUMERIC(10,2) | 是 | |
| `monthly_cap_confirmed_before` | NUMERIC(10,2) | 是 | |
| `monthly_cap_reason` | TEXT | 是 | |
| `monthly_cap_evaluated_at` | DATETIME | 是 | |
| `poc_period_type` | VARCHAR(20) | 是 | POC 周期 |
| `poc_period_key` | VARCHAR(20) | 是 | |
| `poc_reason` | TEXT | 是 | |
| `status` | VARCHAR(20) | 否 | 默认 `pending` |
| `same_day_duplicate_group` | VARCHAR(160) | 是 | |
| `same_day_duplicate_sequence` | INTEGER | 是 | |
| `assigned_reviewer_id` | INTEGER | 是 | FK → `employees.id` |
| `reviewed_by` | INTEGER | 是 | FK → `employees.id` |
| `reviewed_by_name` | VARCHAR(100) | 是 | |
| `reviewed_at` | DATETIME | 是 | |
| `review_note` | TEXT | 是 | |
| `submitted_at` | DATETIME | 否 | |
| `voided_by` | INTEGER | 是 | FK → `employees.id` |
| `voided_by_name` | VARCHAR(100) | 是 | |
| `voided_by_role_code` | VARCHAR(30) | 是 | |
| `voided_by_role_name` | VARCHAR(30) | 是 | |
| `void_permission_scope_snapshot` | VARCHAR(255) | 是 | |
| `voided_from_status` | VARCHAR(20) | 是 | |
| `voided_at` | DATETIME | 是 | |
| `void_reason` | TEXT | 是 | |

键：PK `id`；FK 员工、景点、类型如上。无业务 UNIQUE（同日重复靠确认与索引查找）。

列级索引：`employee_id`、`employee_no`、`employee_name`、`home_attraction_id`、`occurred_attraction_id`、`recognition_date`、`recognition_month`、`recognition_type_id`、`recognizer_employee_id`、`operator_employee_id`、`source`、`monthly_cap_status`、`status`、`same_day_duplicate_group`、`assigned_reviewer_id`、`submitted_at`。

复合索引：

| 名称 | 列 |
|---|---|
| `ix_recognition_monthly_cap_lookup` | `employee_id`, `recognition_month`, `recognition_type_id`, `status`, `reviewed_at`, `id` |
| `ix_recognition_same_day_duplicate_lookup` | `employee_id`, `recognition_date`, `recognition_type_id`, `recognizer_employee_id`, `status`, `submitted_at` |
| `ix_recognition_same_day_duplicate_group` | `same_day_duplicate_group`, `same_day_duplicate_sequence` |
| `ix_recognition_poc_ranking_lookup` | `recognition_date`, `status`, `recognizer_role_code_snapshot`, `operator_role_code_snapshot` |
| `ix_recognition_month_employee_status` | `recognition_month`, `employee_id`, `status` |
| `ix_recognition_pr_ranking` | `status`, `recognition_date`, `recognition_type_id`, `employee_id` |
| `ix_recognition_leader_ranking` | `home_attraction_id`, `source`, `status`, `recognition_date`, `operator_employee_id` |
| `ix_recognition_leader_participant_ranking` | `home_attraction_id`, `status`, `recognition_date`, `recognition_type_id` |
| `ix_recognition_month_close_scope` | `home_attraction_id`, `recognition_month`, `status` |

### `recognition_attachments`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `recognition_id` | INTEGER | 否 | FK → `recognition_records.id` ON DELETE CASCADE |
| `file_id` | INTEGER | 否 | FK → `stored_files.id` |
| `attachment_type` | VARCHAR(30) | 否 | 默认 `evidence` |
| `sort_order` | INTEGER | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_recognition_attachment_type` (`recognition_id`, `attachment_type`)；UNIQUE `uq_recognition_attachment_file` (`file_id`)。

列级索引：`recognition_id`、`file_id`。

### `recognition_monthly_quotas`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `quota_code` | VARCHAR(30) | 否 | |
| `quota_month` | VARCHAR(7) | 否 | |
| `recognition_id` | INTEGER | 否 | FK → `recognition_records.id` ON DELETE CASCADE |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_recognition_monthly_quota` (`employee_id`, `quota_code`, `quota_month`)；UNIQUE `uq_recognition_monthly_quota_record` (`recognition_id`)。

列级索引：`employee_id`、`quota_code`、`quota_month`、`recognition_id`。

### `recognition_reviews`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `recognition_id` | INTEGER | 否 | FK → `recognition_records.id` ON DELETE CASCADE |
| `action` | VARCHAR(20) | 否 | |
| `before_status` | VARCHAR(20) | 否 | |
| `after_status` | VARCHAR(20) | 否 | |
| `reviewer_id` | INTEGER | 否 | FK → `employees.id` |
| `reviewer_name` | VARCHAR(100) | 否 | |
| `note` | TEXT | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `recognition_id`、`reviewer_id`。列级索引：`recognition_id`。

### `stored_files`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `storage_key` | VARCHAR(255) | 否 | UNIQUE，磁盘相对键 |
| `original_filename` | VARCHAR(255) | 否 | |
| `extension` | VARCHAR(20) | 否 | |
| `mime_type` | VARCHAR(100) | 是 | |
| `file_size` | INTEGER | 否 | |
| `sha256` | VARCHAR(64) | 否 | |
| `uploaded_by` | INTEGER | 否 | FK → `employees.id` |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `uploaded_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `storage_key`；FK `uploaded_by`。列级索引：`sha256`。

---

## 缺勤、全勤、月结、治理

### `attendance_rules`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `base_score` | NUMERIC(10,2) | 否 | 默认 10 |
| `perfect_bonus` | NUMERIC(10,2) | 否 | 默认 2 |
| `sick_day_deduction` | NUMERIC(10,2) | 否 | 默认 0.45 |
| `zero_threshold` | NUMERIC(10,2) | 否 | 默认 0.10 |
| `effective_date` | VARCHAR(10) | 否 | |
| `active` | BOOLEAN | 否 | |

键：PK `id`。列级索引：`effective_date`。

### `month_closures`

当前月结闸门。每次关闭/重开仍写审计。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `closure_month` | VARCHAR(7) | 否 | |
| `attraction_id` | INTEGER | 是 | FK → `attractions.id`；空=全局 |
| `status` | VARCHAR(20) | 否 | 默认 `open` |
| `closed_by` | INTEGER | 是 | FK → `employees.id` |
| `closed_by_name` | VARCHAR(100) | 是 | |
| `closed_at` | DATETIME | 是 | |
| `close_reason` | TEXT | 是 | |
| `reopened_by` | INTEGER | 是 | FK → `employees.id` |
| `reopened_by_name` | VARCHAR(100) | 是 | |
| `reopened_at` | DATETIME | 是 | |
| `reopen_reason` | TEXT | 是 | |
| `updated_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_month_closure_scope` (`closure_month`, `attraction_id`)。

部分 UNIQUE：`uq_month_closure_global` (`closure_month`) WHERE `attraction_id IS NULL`。

列级索引：`closure_month`、`attraction_id`、`status`。

复合索引：`ix_month_closure_lookup` (`closure_month`, `attraction_id`, `status`)。

### `governance_cases`

工单本身不改分数；获准后仍走既有作废/重开闸门。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `case_type` | VARCHAR(30) | 否 | |
| `record_type` | VARCHAR(30) | 是 | |
| `record_id` | INTEGER | 是 | |
| `attraction_id` | INTEGER | 是 | FK → `attractions.id` |
| `score_month` | VARCHAR(7) | 是 | |
| `subject_employee_id` | INTEGER | 是 | FK → `employees.id` |
| `submitted_by` | INTEGER | 否 | FK → `employees.id` |
| `submitted_by_name` | VARCHAR(100) | 否 | |
| `reason` | TEXT | 否 | |
| `status` | VARCHAR(20) | 否 | 默认 `open` |
| `decision` | VARCHAR(30) | 是 | |
| `resolution` | TEXT | 是 | |
| `resolved_by` | INTEGER | 是 | FK → `employees.id` |
| `resolved_by_name` | VARCHAR(100) | 是 | |
| `resolved_at` | DATETIME | 是 | |
| `submitted_at` | DATETIME | 否 | |
| `due_at` | DATETIME | 否 | |

键：PK `id`；FK `attraction_id`、`subject_employee_id`、`submitted_by`、`resolved_by`。

列级索引：`case_type`、`record_type`、`record_id`、`attraction_id`、`score_month`、`subject_employee_id`、`submitted_by`、`status`、`submitted_at`、`due_at`。

复合索引：

- `ix_governance_case_status_scope` (`case_type`, `status`, `attraction_id`, `submitted_at`)
- `ix_governance_case_subject` (`record_type`, `record_id`, `status`)

### `employee_month_organization_snapshots`

该月第一次核准关闭时锁定组织归属。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` ON DELETE CASCADE |
| `score_month` | VARCHAR(7) | 否 | |
| `attraction_id` | INTEGER | 是 | FK → `attractions.id` |
| `attraction_name` | VARCHAR(100) | 是 | |
| `group_id` | INTEGER | 是 | FK → `work_groups.id` |
| `group_name` | VARCHAR(100) | 是 | |
| `leader_employee_id` | INTEGER | 是 | FK → `employees.id` |
| `leader_name` | VARCHAR(100) | 是 | |
| `captured_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_employee_month_organization_snapshot` (`employee_id`, `score_month`)。

列级索引：`employee_id`、`score_month`、`attraction_id`。

复合索引：`ix_employee_month_organization_scope` (`score_month`, `attraction_id`)。

### `sick_leave_records`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `employee_no_snapshot` | VARCHAR(50) | 是 | |
| `employee_name_snapshot` | VARCHAR(100) | 是 | |
| `employee_role_snapshot` | VARCHAR(30) | 是 | |
| `attraction_id_snapshot` | INTEGER | 是 | 快照，无 FK |
| `attendance_month` | VARCHAR(7) | 否 | |
| `leave_start_date` | VARCHAR(10) | 否 | |
| `leave_end_date` | VARCHAR(10) | 否 | |
| `leave_days` | NUMERIC(6,1) | 否 | |
| `charged_days` | NUMERIC(6,1) | 否 | |
| `proof_file_id` | INTEGER | 否 | FK → `stored_files.id` |
| `is_violation` | BOOLEAN | 否 | 违规病假 |
| `violation_deduction_id` | INTEGER | 是 | FK → `deduction_records.id`，UNIQUE |
| `note` | TEXT | 是 | |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `submitted_by` | INTEGER | 否 | FK → `employees.id` |
| `submitted_by_name` | VARCHAR(100) | 否 | |
| `submitted_at` | DATETIME | 否 | |
| `voided_by` | INTEGER | 是 | FK → `employees.id` |
| `voided_by_name` | VARCHAR(100) | 是 | |
| `voided_by_role_code` | VARCHAR(30) | 是 | |
| `voided_by_role_name` | VARCHAR(30) | 是 | |
| `void_permission_scope_snapshot` | VARCHAR(255) | 是 | |
| `voided_from_status` | VARCHAR(20) | 是 | |
| `voided_at` | DATETIME | 是 | |
| `void_reason` | TEXT | 是 | |

键：PK `id`；UNIQUE `violation_deduction_id`（可空）；FK `employee_id`、`proof_file_id`、`violation_deduction_id`、`submitted_by`、`voided_by`。

列级索引：`employee_id`、`employee_no_snapshot`、`employee_name_snapshot`、`attraction_id_snapshot`、`attendance_month`、`is_violation`、`violation_deduction_id`、`status`、`submitted_by`。

部分 UNIQUE：`ix_sick_leave_violation_deduction` (`violation_deduction_id`) WHERE `violation_deduction_id IS NOT NULL`。

复合索引：

- `ix_sick_leave_month_employee_status` (`attendance_month`, `employee_id`, `status`)
- `ix_sick_leave_pr_ranking` (`status`, `leave_start_date`, `leave_end_date`, `employee_id`)

日期交集拦截在服务端按有效记录闭区间判断，没有额外 UNIQUE 约束。

### `attendance_monthly_scores`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `attendance_month` | VARCHAR(7) | 否 | |
| `month_end_role_id` | INTEGER | 是 | FK → `roles.id` |
| `eligible` | BOOLEAN | 否 | |
| `actual_sick_days` | NUMERIC(8,1) | 否 | |
| `charged_sick_days` | NUMERIC(8,1) | 否 | |
| `base_score` | NUMERIC(10,2) | 否 | |
| `perfect_bonus` | NUMERIC(10,2) | 否 | |
| `sick_deduction` | NUMERIC(10,2) | 否 | |
| `final_score` | NUMERIC(10,2) | 否 | |
| `rule_id` | INTEGER | 是 | FK → `attendance_rules.id` |
| `calculation_version` | INTEGER | 否 | |
| `calculated_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_attendance_employee_month` (`employee_id`, `attendance_month`)。

列级索引：`employee_id`、`attendance_month`。

复合索引：`ix_attendance_month_employee` (`attendance_month`, `employee_id`)。

---

## 扣分与材料

### `deduction_types` / `deduction_levels`

类型：`id` PK；`code`、`name` UNIQUE；`active`。

等级另有 `points NUMERIC(10,2)`。

### `deduction_records`

照片提交先落库，PDF 生成成功后才 `active` 计分。

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `employee_no` | VARCHAR(50) | 否 | 快照 |
| `employee_name` | VARCHAR(100) | 否 | |
| `employee_role_snapshot` | VARCHAR(30) | 否 | |
| `employee_group_id_snapshot` | INTEGER | 是 | 无 FK |
| `attraction_id_snapshot` | INTEGER | 是 | 无 FK |
| `deduction_type_id` | INTEGER | 否 | FK → `deduction_types.id` |
| `deduction_type_name` | VARCHAR(30) | 否 | |
| `deduction_level_id` | INTEGER | 否 | FK → `deduction_levels.id` |
| `deduction_level_name` | VARCHAR(30) | 否 | |
| `points` | NUMERIC(10,2) | 否 | |
| `occurred_on` | VARCHAR(10) | 否 | |
| `deduction_month` | VARCHAR(7) | 否 | |
| `description` | TEXT | 否 | |
| `document_file_id` | INTEGER | 否 | FK → `stored_files.id` |
| `submitter_id` | INTEGER | 否 | FK → `employees.id` |
| `submitter_name` | VARCHAR(100) | 否 | |
| `submitter_role_snapshot` | VARCHAR(30) | 否 | |
| `permission_scope_snapshot` | VARCHAR(100) | 否 | |
| `status` | VARCHAR(20) | 否 | 默认 `active` |
| `material_status` | VARCHAR(30) | 否 | 默认 `ready` |
| `material_error` | TEXT | 是 | |
| `material_source_type` | VARCHAR(20) | 否 | 默认 `pdf` |
| `material_job_id` | INTEGER | 是 | 无 FK |
| `material_uploaded_by` | INTEGER | 是 | FK → `employees.id` |
| `material_uploaded_by_name` | VARCHAR(100) | 是 | |
| `material_uploaded_at` | DATETIME | 是 | |
| `material_revision` | INTEGER | 否 | 默认 1 |
| `legacy_upgrade_excluded` | BOOLEAN | 否 | 历史声明不自动升级 |
| `legacy_upgrade_note` | TEXT | 是 | |
| `submitted_at` | DATETIME | 否 | |
| `voided_by` | INTEGER | 是 | FK → `employees.id` |
| `voided_by_name` | VARCHAR(100) | 是 | |
| `voided_by_role_code` | VARCHAR(30) | 是 | |
| `voided_by_role_name` | VARCHAR(30) | 是 | |
| `void_permission_scope_snapshot` | VARCHAR(255) | 是 | |
| `voided_from_status` | VARCHAR(20) | 是 | |
| `voided_at` | DATETIME | 是 | |
| `void_reason` | TEXT | 是 | |
| `upgrade_request_id` | INTEGER | 是 | 无 FK |
| `upgrade_role` | VARCHAR(30) | 是 | `source_first` / `source_second` / `result` |
| `upgrade_state` | VARCHAR(30) | 是 | |

键：PK `id`；FK 员工、类型、等级、文件、提交人、补材料人、作废人。待补去重靠查询索引，不是 UNIQUE。

列级索引：`employee_id`、`employee_no`、`employee_name`、`occurred_on`、`deduction_month`、`submitter_id`、`status`、`material_status`、`material_job_id`、`legacy_upgrade_excluded`、`submitted_at`、`upgrade_request_id`、`upgrade_role`、`upgrade_state`。

复合索引：

| 名称 | 列 |
|---|---|
| `ix_deduction_repeat_lookup` | `employee_id`, `deduction_type_id`, `status`, `occurred_on` |
| `ix_deduction_statement_upgrade_lookup` | `employee_id`, `deduction_type_id`, `status`, `deduction_level_id`, `occurred_on`, `upgrade_state`, `id` |
| `ix_deduction_material_state` | `material_status`, `status`, `submitted_at` |
| `ix_deduction_pending_material_lookup` | `status`, `material_status`, `employee_id`, `deduction_type_id`, `deduction_level_id`, `occurred_on` |
| `ix_deduction_month_employee_status` | `deduction_month`, `employee_id`, `status` |
| `ix_deduction_pr_ranking` | `status`, `occurred_on`, `deduction_type_id`, `employee_id` |
| `ix_deduction_month_close_scope` | `attraction_id_snapshot`, `deduction_month`, `status` |

### `deduction_material_jobs`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `deduction_id` | INTEGER | 否 | FK → `deduction_records.id` ON DELETE CASCADE |
| `output_file_id` | INTEGER | 否 | FK → `stored_files.id` |
| `source_file_ids_json` | TEXT | 否 | 临时原图 ID 列表 |
| `mode` | VARCHAR(30) | 否 | 默认 `deduction` |
| `reviewer_id` | INTEGER | 是 | FK → `employees.id` |
| `first_deduction_id` | INTEGER | 是 | FK → `deduction_records.id` |
| `status` | VARCHAR(30) | 否 | 默认 `queued` |
| `error_code` | VARCHAR(50) | 是 | |
| `error_message` | TEXT | 是 | |
| `attempts` | INTEGER | 否 | |
| `created_at` | DATETIME | 否 | |
| `started_at` | DATETIME | 是 | |
| `completed_at` | DATETIME | 是 | |

键：PK `id`；FK `deduction_id`、`output_file_id`、`reviewer_id`、`first_deduction_id`。

列级索引：`deduction_id`、`output_file_id`、`status`、`created_at`。

复合索引：

- `ix_deduction_material_job_status_created` (`status`, `created_at`)
- `ix_deduction_material_job_deduction` (`deduction_id`, `id`)

### `deduction_upgrade_requests`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `deduction_type_id` | INTEGER | 否 | FK → `deduction_types.id` |
| `first_deduction_id` | INTEGER | 否 | FK → `deduction_records.id` UNIQUE |
| `second_deduction_id` | INTEGER | 否 | FK → `deduction_records.id` UNIQUE |
| `reviewer_id` | INTEGER | 否 | FK → `employees.id` |
| `reviewer_name` | VARCHAR(100) | 否 | |
| `status` | VARCHAR(30) | 否 | 默认 `pending` |
| `result_level_id` | INTEGER | 是 | FK → `deduction_levels.id` |
| `result_deduction_id` | INTEGER | 是 | FK → `deduction_records.id` UNIQUE |
| `handling_note` | TEXT | 是 | |
| `issued_confirmed` | BOOLEAN | 否 | |
| `submitted_by` | INTEGER | 否 | FK → `employees.id` |
| `submitted_by_name` | VARCHAR(100) | 否 | |
| `resolved_by` | INTEGER | 是 | FK → `employees.id` |
| `resolved_by_name` | VARCHAR(100) | 是 | |
| `resolved_at` | DATETIME | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `first_deduction_id`、`second_deduction_id`、`result_deduction_id`。

列级索引：`employee_id`、`deduction_type_id`、`first_deduction_id`、`second_deduction_id`、`reviewer_id`、`status`、`submitted_by`、`created_at`。

复合索引：`ix_deduction_upgrade_assignee_status` (`reviewer_id`, `status`, `created_at`)。

### `deduction_upgrade_transfers`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `request_id` | INTEGER | 否 | FK → `deduction_upgrade_requests.id` ON DELETE CASCADE |
| `from_reviewer_id` | INTEGER | 否 | FK → `employees.id` |
| `from_reviewer_name` | VARCHAR(100) | 否 | |
| `to_reviewer_id` | INTEGER | 否 | FK → `employees.id` |
| `to_reviewer_name` | VARCHAR(100) | 否 | |
| `reason` | TEXT | 否 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `request_id`、`from_reviewer_id`、`to_reviewer_id`。

列级索引：`request_id`、`from_reviewer_id`、`to_reviewer_id`、`created_at`。

复合索引：`ix_deduction_upgrade_transfer_request` (`request_id`, `created_at`)。

### `deduction_follow_ups`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `supervisor_id` | INTEGER | 否 | FK → `employees.id` |
| `supervisor_name` | VARCHAR(100) | 否 | |
| `employee_id` | INTEGER | 否 | FK → `employees.id` |
| `employee_no` | VARCHAR(50) | 否 | |
| `employee_name` | VARCHAR(100) | 否 | |
| `deduction_type_id` | INTEGER | 否 | FK → `deduction_types.id` |
| `deduction_type_name` | VARCHAR(30) | 否 | |
| `occurred_on` | VARCHAR(10) | 否 | |
| `previous_record_ids` | TEXT | 否 | |
| `status` | VARCHAR(20) | 否 | 默认 `pending` |
| `issued_deduction_id` | INTEGER | 是 | FK → `deduction_records.id` |
| `issued_by` | INTEGER | 是 | FK → `employees.id` |
| `issued_by_name` | VARCHAR(100) | 是 | |
| `issued_at` | DATETIME | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_deduction_follow_up_event` (`supervisor_id`, `employee_id`, `deduction_type_id`, `occurred_on`)。

列级索引：`supervisor_id`、`employee_id`、`employee_no`、`employee_name`、`deduction_type_id`、`occurred_on`、`status`、`created_at`。

---

## 审计与系统

### `audit_logs`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `operator_id` | INTEGER | 是 | FK → `employees.id` |
| `operator_name` | VARCHAR(100) | 是 | |
| `action` | VARCHAR(100) | 否 | |
| `entity_type` | VARCHAR(50) | 否 | |
| `entity_id` | VARCHAR(50) | 是 | |
| `before_json` | TEXT | 是 | |
| `after_json` | TEXT | 是 | |
| `reason` | TEXT | 是 | |
| `ip_address` | VARCHAR(100) | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；FK `operator_id`。列级索引：`action`、`entity_type`、`created_at`。

复合索引：`ix_audit_operator_action_recent` (`operator_id`, `action`, `created_at DESC`, `id DESC`)；用于每个操作人最近导出记录。

不记录图片内容、明文密码或会话 Cookie。

### `system_alerts`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `alert_type` | VARCHAR(50) | 否 | |
| `dedupe_key` | VARCHAR(100) | 否 | |
| `employee_id` | INTEGER | 是 | FK → `employees.id` |
| `group_id` | INTEGER | 是 | FK → `work_groups.id` |
| `due_date` | VARCHAR(10) | 是 | |
| `message` | TEXT | 否 | |
| `status` | VARCHAR(20) | 否 | 默认 `open` |
| `handled_by` | INTEGER | 是 | FK → `employees.id` |
| `handled_at` | DATETIME | 是 | |
| `created_at` | DATETIME | 否 | |

键：PK `id`；UNIQUE `uq_system_alert` (`alert_type`, `dedupe_key`)。列级索引：`alert_type`、`status`。

### `system_job_runs`

| 字段 | 类型 | 空 | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | 否 | |
| `job_type` | VARCHAR(50) | 否 | |
| `business_date` | VARCHAR(10) | 否 | |
| `idempotency_key` | VARCHAR(100) | 否 | |
| `status` | VARCHAR(20) | 否 | |
| `result` | TEXT | 是 | |
| `started_at` | DATETIME | 否 | |
| `completed_at` | DATETIME | 是 | |

键：PK `id`；UNIQUE `uq_system_job_run` (`job_type`, `idempotency_key`)。列级索引：`job_type`。

---

## 视图 `v_employee_month_scores`

该视图保留给兼容调用。现用首页、组员汇总、统计明细和统计/导出通过 `score_queries.py` 的参数化查询先把月份与员工范围下推到各聚合源，避免查询一个月或一个人时扫描全部历史。

启动时 `DROP VIEW IF EXISTS` 再重建。按员工、月份汇总：

- 加分：`status='confirmed'`；`recognition_date < 2026-09-01` 用 `fraction`，否则用 `credited_fraction`
- 全勤：`eligible=1` 时的 `final_score`
- 扣分：`status='active'` 的 `points` 合计
- `total_score` = 加分 + 全勤 − 扣分

列：`employee_id`、`employee_no`、`employee_name`、`score_month`、`recognition_score`、`attendance_score`、`deduction_score`、`total_score`。

---

## 关系要点

```
employees.id  ← 几乎所有业务表的员工外键
user_accounts.employee_id 1:1 employees
user_sessions.account_id  → user_accounts
stored_files.id  ← recognition_attachments / sick_leave.proof / deduction.document / material_jobs.output
recognition_records 1:N attachments, reviews；可选 1:1 monthly_quota
deduction_records 1:N material_jobs
sick_leave.violation_deduction_id 0..1 deduction_records
deduction_upgrade_requests 连接 first/second/result 三条 deduction_records
```

员工号变更只写 `employee_number_history`，业务行继续用原来的 `employee_id`。

---

## 维护

| 改了 | 同步 |
|---|---|
| `v2_models.py` 表/字段/约束 | 本文件对应节 |
| `v2_database.py` 补丁或 `CREATE INDEX` | 本文件索引与迁移节 |
| 月度分值口径 | 视图节 |

测试必须用隔离数据目录，不要指向生产库。
