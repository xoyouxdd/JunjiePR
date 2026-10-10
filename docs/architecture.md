# 架构

## 现用系统

`backend/` 是唯一业务系统：

- 接口：FastAPI + SQLAlchemy。路由按业务域分在 `backend/app/routers/` 下（`auth`、`accounts`、`employees`、`organization`、`hr_admin`、`deductions`、`recognitions`、`sick_leaves`、`sick_leave_import`、`statistics`、`governance`、`files`、`rotation`、`announcements`、`announcement_media`）。统计路由负责参数、权限、缓存及响应，查询和明细装配交给应用服务；`v2.py` 只挂载各域。`_shared.py` 保留显式兼容导出，公共实现由各职责模块拥有
- 数据：SQLite（默认 `backend/data_v2/`）+ 独立附件目录；表、键、索引见 [sqlite-schema.md](sqlite-schema.md)
- 数据库职责：`database/connection.py` 唯一持有路径、Base、engine 和 SessionLocal；模型直接依赖该基础层。`database/bootstrap.py` 串行执行建表、一次性迁移及每次启动保障；种子、账号保障、分值视图与历史迁移各有所属模块。`v2_database.py` 保留显式兼容导出
- 页面：`backend/app/static/` 原生 HTML/JS/CSS，由 `backend/app/main.py` 直接提供。配色集中在 `style.css` 的 `:root` 变量，语义色成对命名（如 `--btn-secondary-bg` / `--btn-secondary-text`、`--readonly-bg` / `--readonly-text`）；状态色只从 danger / warn / ok / info 四组变量里取（各组有 `-text`、`-bg`、`-border` 等档位）。新增样式优先复用变量，不要再写硬编码色值。确认弹窗统一用 `confirmModal` / `noticeModal`，不用浏览器原生 `confirm` / `alert`；弹层关闭走 `bindDialogLayer`，由它负责 `.is-leaving` 退场动画。断点只用三档：≤760px 为手机（底部栏为待办加 `MOBILE_PRIMARY_TABS` 按角色指定的常用入口，其余进「更多」；「更多」顶部显示完整身份、底部为退出登录，顶部栏只显示系统简称和「姓名 · 景点圈」），761–1199px 为带短标签的图标栏，≥1200px 为完整的左侧分组导航（待办置顶，更新记录/密码在底部）。内容区最宽 1440px；761–1279px 下多列筛选表单自动换行
- Excel 写表：`backend/app/excel_export.py`（路由只负责取数、水印、审计、返回）
- 月度分数读查询：`backend/app/score_queries.py`（先限定月份和员工范围，再聚合；旧视图保留兼容）。`score_policy.py` 提供自然月 LOA 排除谓词，月度汇总、综合排名及代理期间得分共用；类别次数与未封顶认可排行仍保留原始业务记录
- LOA 写业务：`backend/app/services/loa_commands.py` 统一登记、结束、撤销、区间重叠及受影响月份重算；专用入口与 HR 员工编辑共用，权限、审计和事务提交由路由负责
- 统计查询：`services/monthly_statistics.py` 负责月度统计与层级，`performance_rankings.py` 负责排名，`performance_details.py` 共用批量记录加载及明细装配，`statistics_trends.py` 负责趋势。成员成绩与统计明细共用身份、病假材料和认可图片的批量加载；`services/declaration_statistics.py` 负责声明统计查询。HR 月报直接依赖这些服务，不导入路由
- 轮岗（测试）：`backend/app/rotation/` 为独立模块（规则引擎、运行服务、名单解析、专用账号），后台线程每秒推进，页面 `/rotation`、`/rotation/screen` 独立于主应用外壳；见 [rotation.md](rotation.md)
- 身份：本职身份与代理职务分开存储。`services/identity.py` 的 `role_at` 返回当天实际使用的职务，`base_role_at` 返回本职；计分、组员关系、全勤和排名人群按本职，权限取两者并集。规则见 [identity-and-supervisor.md](identity-and-supervisor.md)

业务基础服务按职责分为 `identity`（日期身份与认可人）、`organization`（小组与管理范围）、`attendance`（全勤与 LOA 月份）、`attachments`（文件存储）、`audit`（审计）和 `role_lifecycle`（到期编排）。单人和批量全勤共用纯计分函数，批量路径仍先查齐数据再插入缺失月份。`v2_services.py` 保留显式兼容导出及附件 `FILE_DIR` 注入包装，新服务直接依赖所属模块，不反向依赖兼容门面。

GSM/TA GSM 管理范围同步在 `services/management_scopes.py`，由人员变更、职务到期和启动编排复用；一次性旧考勤清理在 `maintenance/legacy_attendance.py`，只由显式维护入口调用，不进入启动链。迁移注册表以有序 callable 为唯一执行源，保留原 24 个 step key、顺序及历史提交行为。

公共查询、规则与响应投影分别放在 `organization_queries.py`、`access_policy.py`、`recognition_policy.py`、`deduction_policy.py`、`material_policy.py`、`record_payloads.py`、`month_closure.py` 等模块；日期、搜索、材料预览与请求元数据各有小型工具模块。响应缓存统一由 `data_cache.py` 持有，旧入口与新入口共用同一锁和缓存对象，失效操作不会分叉。

轮岗的服务事务持锁直到数据库提交完成，随后发布缓存与实时版本；嵌套预排和测试时钟操作共用最外层事务。失败丢弃私有状态副本并回滚数据库。当前仍为单进程运行时，不支持多个 worker 共用进程内锁和缓存。

主页面异步操作捕获来源页面上下文。导航可取消旧页面 GET；已经发出的提交继续完成，但来源页面失效后不再刷新或覆盖新页面。

主前端使用原生 ES 模块：`static/js/app.js` 只组合页面注册及初始化，`static/js/app/` 按 state、api、context、dialogs、files、登记、复核、统计、人事等职责明确导入。页面不反向依赖组合入口，导航通过已注册渲染器切换。`index.html` 的 import map 将全部依赖映射到带静态版本的 URL；渲染 HTML 后只为该 map 的精确内容追加 CSP 哈希授权，其余脚本策略仍为 self，不开放任意内联脚本。

员工编辑由 `services/employee_commands.py` 协调，人员状态与账号、身份代理、小组关系分别由 `employee_status.py`、`employee_identity_commands.py`、`employee_groups.py` 处理。共同上下文传递日期、月结门禁、审计和警告，服务不提交；路由统一权限、BEGIN IMMEDIATE、总审计、提交及异常回滚。

版本来源：`backend/app/version.py`（`年.月.日.当天第几版`，以文件为准）。用户在导航「更新记录」看到的内容来自 `backend/app/changelog.py`，按角色过滤。

## 目录职责

| 路径 | 角色 |
|---|---|
| `backend/` | 现用签卡、缺勤、月结、导出 |
| `scripts/` | 发布、备份、恢复和健康检查 |
| `docs/` | 说明文档 |

改业务只动 `backend/`；改发布或运维流程时改 `scripts/`。
