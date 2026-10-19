# 2026-10-19 本地需求复查

本轮只修改本地源码和测试，未提交Git、未推送GitHub、未打生产包、未上线。现有工作区其他已完成修改保留。

| 已确认事项 | 本地实现位置 | 复查与限制 |
| --- | --- | --- |
| iPhone图片名称为JPEG却提示格式无效 | `backend/app/services/attachments.py`、`static/js/app/registration.js` | 按真实编码识别HEIC/HEIF，保留原文件并生成JPEG预览；损坏和像素限制继续有效。自动化夹具验证，仍需真实iPhone/微信端验收。 |
| 公告PDF/Excel首页精简、包含发布内容、去掉如何阅读 | `announcement_exports.py` | 首页集中内容、概要与状态；长发布内容以完整保留为先，不能靠小字塞一页。 |
| 签收按小组编排，组长/代理一起，显示签字和时间 | `announcement_export_data.py`、`announcement_exports.py` | 沿用授权签收名单；长名单合理续页，普通小组集中一页，不以缺失内容换页数。 |
| 未签、代签说明可查原因，压缩报告 | 同上 | 批量读取停用/新建账号等可核实事实，不推定动机；缩放复用签字图片，保留签收记录及权限。 |
| 三套完整专业PPT，同等质量、不同配图 | `hr_monthly_layouts.py`、`hr_monthly_pptx.py`及`report_templates/hr_monthly/` | 管理纪要、数据简报、团队月刊三套接入，独立示意图；原生文字表格图表，可编辑、16:9、自然分页、空节隐藏、照片保比例。已有实际PPTX与逐页预览在`output/monthly-ppt-complete-20261019/`，兼容性边界见其中验收说明。 |
| 其他数据导出简化阅读，内容不能少 | `excel_presentation.py`、`excel_export*.py`及统计/声明/缺勤路由 | 总览前置、完整人员表和全部原工作表保留，低频列折叠可展开；长内容换行、打印表头、水印素材复用。同批次核对脚本及Excel实际文件位于`output/export-refresh-20261019/`。 |
| 我的荣誉三个页签 | `static/js/app/honors.js`、`home.js`、`services/honors.py` | 从预览接入真实本人数据；荣誉典藏、月度寄语/精选原文、八款自由装扮。 |
| 八款装扮、收藏和原文选择 | `EmployeeHonorStyle`、`EmployeeHonorMemory`、`/api/self-honors` | 保存到账户，不只保存在当前浏览器；所有原计分身份和全部档位同样可用，权限不扩大。 |
| 高光只写好的，具体事例在前，不编造 | `services/honors.py` | 只收已确认的有效认可，包括封顶零计分。采用透明、有限的关键词整理规则；寄语直接引用最多3条实际原文，来源可核对。没有额外模型服务，也没有虚构结果。 |
| 勋章拼接更顺滑 | `home.js`、`style.css` | 连续分区减小接缝，组成层在原场景渐隐，保留十档独立动作、重播/跳过、默认静音及减少动态效果。 |
| 最终十档名称 | `services/monthly_ranks.py` | 启航新星、成长青铜、探索白银、荣耀黄金、流光铂金、永恒钻石、璀璨星耀、超凡大师、傲世宗师、无双王者。只改名称，不改分档边界、分数或历史快照。 |

## 验证证据

- 今日相关15个Python模块逐文件隔离执行，239项测试通过，日志及结果写入`output/local-review-20261019/verification.json`及同目录日志，避免多个模块共享启动配置。这是本轮相关验证，不是全仓全量测试。
- `test_self_honors.py`：本人隔离、跨月、有效认可筛选、原文来源、封顶、偏好写入、重开、非法参数。
- `monthly_rank_ui.cjs`：真实业务模块、生产CSP、三个页签与八款装扮；200组布局、十档动作与音频验证通过，新增旧回顾GET/偏好PUT延迟响应不能覆盖新月份验证。截图在`.handover_review/monthly-rank-ui/`。
- `frontend_modules.cjs`：完整前端模块图、版本化import map、CSP、原有UI及提交保护；新模块已进入版本化依赖图。

本地隔离测试不等于已部署或真人手机验收。此次未连接正式服务器，未读取或上传真实员工资料。新增偏好表在下次正常启动时自动幂等创建；无需重算月结或修改旧业务记录。
