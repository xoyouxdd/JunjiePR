# HR月报模板与系统接入计划

阶段：三套可编辑模板已制作，等待成品确认。尚未接入业务系统。

## 已确认与待确认

- 入口角色已经用户确认：GSM、AM、OM、SYSTEM_ADMIN。TA GSM及HR角色不开放。
- 用户确认GSM可选择任意月份、任意单个景点圈及全部景点圈，仅扩大月报读取范围，不扩大登记、审核、作废权限。
- 16:9、薄荷绿森林都市主题、原创Image Gen素材、可编辑PPTX、照片人工提供，来自用户要求。
- 设计参数已确认：微软雅黑，深绿标题，大图表，排名自动分页，20页示例版。
- 用户确认首版提供森林都市、简约汇报、温暖团队三套模板。共用数据来源与章节，切换风格不改变统计口径。
- 模板确认后才实现系统接口，不修改登记、审核、作废规则。

## 拟修改文件

| 文件 | 工作 |
| --- | --- |
| 新增 `backend/app/hr_monthly_report.py` | 同一份标准报告数据，预览和导出共用，复用月度分数与历史组织信息 |
| 新增 `backend/app/hr_monthly_pptx.py` | 从确认后的原生模板填充文字、表格、图表及内嵌工作簿，自动分页 |
| 新增 `backend/app/routers/hr_monthly_reports.py` | 独立查看、素材上传、预览、导出端点，逐次校验角色与数据范围 |
| `backend/app/routers/v2.py` | 注册独立路由 |
| `backend/app/v2_database.py` | 专项月报权限，仅赋给确认的四类角色 |
| `backend/app/static/js/app.js` | 月份与范围、章节、候选确认、素材及下载 |
| `backend/app/static/css/style.css` | 沿用现有页面视觉与手机布局 |
| 新增 `backend/app/report_templates/hr_monthly/` | 原生PPTX、模板配置、原创图片与素材来源 |
| 新增 `backend/tests/test_hr_monthly_reports.py` | 权限、历史归属、LOA、状态及预览导出一致性 |
| 新增 `backend/tests/test_hr_monthly_pptx.py` | 原生对象、分页、中文、图片比例、无素材跳页 |
| `scripts/build_release.py`、依赖清单 | 将模板和实际所需生成依赖纳入包，不能依赖开发电脑的Codex运行时 |
| `backend/app/changelog.py`及说明文档 | 接入完成后才声明功能可用；发布公告选择保持原规则 |

现阶段仅新增本文档。以上是计划，不是已实现文件名单。

## 数据来源与映射

| 模块 | 当前来源 | 口径/约束 |
| --- | --- | --- |
| 综合分、实际加减分、出勤分 | `statistics_payload`、`employee_month_scores` | 有效状态、认可计入分值、LOA排除，不另算原始总分 |
| 月结与草稿状态 | `MonthClosure`、现有月结判断辅助函数 | 按选中各圈判定，混合范围不能仅标全局已月结 |
| 历史景点/小组 | `EmployeeMonthOrganizationSnapshot` | 月结快照优先；无快照须注明归属依据，不能伪称历史已封存 |
| 事件景点 | 认可的 `home_attraction_id`、处分的 `attraction_id_snapshot` | 与分数归属区分，使用登记时快照 |
| 加分类别 | `RecognitionRecord`、`RecognitionType` | `confirmed`记录统计；分值与记录数分开，LOA有效记录可保留数量但计分为排除 |
| 减分类别 | `DeductionRecord`、现有处分统计去重 | 按业务事件月，区分已生效及非生效记录，升级来源/结果不得重复计数 |
| 认可发放人 | 登记人、签卡人及角色快照，现有`gsm_recognizer_ranking_payload` | 签卡人与代录人可能不是同一人，不把二者相加；最终定义待确认 |
| 认可率 | 有效认可记录数、同范围月度统计人数 | 认可数÷统计人数×100%，可超过100%；零分母显示“不适用” |
| 小组分 | 当月有效CM/TR综合分、小组与组长快照 | 建议按组员平均综合分排行，列人数、总分及均分；LOA不进入分母 |
| PR排名 | 月度计分结果、`roles_at`、历史归属 | 综合分降序，同分稳定工号顺序；CM、TR、主管分别展示 |
| 优秀员工 | 对应类别排名候选 | 仅推荐，需人工确认；不自动宣布获奖，不修改业务记录 |
| 工作说明、活动、生日 | 用户手填文字和上传图片 | 当前`Employee`没有生日字段，不自动生成真实生日名单 |

## 发现的复用边界

1. `statistics_payload`分数景点归属优先月结快照，但`statistics_hierarchy`仍按组员关系、组长关系和管理范围查询月末组织。闭合月份应以已存小组快照构建月报，不直接依赖这段层级汇总。
2. `pr_ranking_payload`带有专属角色限制且部分分支会物化考勤并提交。月报读取不得直接借用接口绕开权限或引入写副作用，优先复用分数和历史角色基础函数。
3. 现有排名“认可发放”可能同时展示签卡人和操作人。月报发放榜需要独立明确归属，避免代录重复。
4. `declaration_payload`已经对已批准升级的第二来源记录去重。但事件数量和实际计分不是同一口径，应分别校验。
5. 旧月报图表存在小数值，不应默认为“次数”。模板默认用明确注明单位的有效记录数占比，计分另外展示；如采用分值占比需用户确认。

## 建议模板结构

固定：封面、月度概览、减分分类、加分分类、认可发放、认可统计及趋势、小组分、优秀员工候选确认结果、各景点及CM/TR/主管PR排名。

可选：工作说明、活动照片、生日祝福、结束页。示例版以明确标注的模拟数据展示，不复制真实员工个人信息。

活动布局：单图、双图、四图，多图自动分页。照片默认完整适配，不自动裁掉人物。无素材默认隐藏章节。

## 原创图像提示词（待设计确认后执行）

### 封面

Use case: productivity-visual. Asset: 16:9 HR monthly report cover background. Original editorial illustration of a forest blended with a modern animal-friendly city, pale mint green and deep forest green with restrained warm yellow details. Clean sophisticated soft paper-cut illustration, adult professional audience, warm team atmosphere. Buildings and trees concentrated on the right and lower edges, large quiet light mint negative space on the left for editable Chinese title. No text, numbers, logo, watermark, famous fictional characters, or photographic employees. Do not draw slide UI, cards or charts.

### 数据页背景/章节装饰

Use case: productivity-visual. Asset: 16:9 reusable data-page background. Nearly white pale mint field with delicate original forest leaves and distant contemporary animal-city rooftops only at extreme bottom right and top right corners. Keep more than 90 percent of canvas quiet and clean for native editable charts and tables. Palette pale mint, forest green and tiny warm yellow accents. Professional editorial illustration matching a forest-city HR report. No text, numbers, logo, watermark, chart, card grid or recognizable franchise characters.

### 结束页

Use case: illustration-story. Asset: 16:9 HR report closing illustration. A small original diverse group of friendly anthropomorphic woodland animals sharing a warm team moment in an airy modern garden plaza, positioned along the lower right edge. Sophisticated editorial paper-cut style, pale mint backdrop, deep green and subtle warm yellow, spacious composition with quiet left upper half for editable closing text. These are fictional original illustrations, not real staff or event photos. No text, logo, watermark or famous characters.

## 接入与验证边界

- 导出使用同一份服务端规范化报告数据，不能在浏览器另算指标。
- 不新增冗余加分、缺勤或处分表。素材如需要持久化，复用受控文件存储，另议是否需要“报告草稿配置”而非业务记录表。
- 上传限制类型、数量、大小和像素，防止大图挤占内存；清除图片不必要元数据；不能通过任意文件路径/URL加载他人附件。
- 导出记录账号、月份、圈范围、章节、模板版本、统计口径及数据时间；水印适用现有权限原则。
- 模板制作运行时与生产生成运行时分离，生产不能要求Codex、Image Gen或开发电脑Node路径。
- 测试允许/拒绝角色、跨圈越权、事件月份、历史归属、LOA、上限、升级去重、零分母、草稿、分页、预览和导出一致。
- 模板尚未确认，不宣称接口完成、生产上线或测试通过。
