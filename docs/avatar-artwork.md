# 个人头像装扮（本地候选版）

2026.10.28.4调整：GSM及以上（包括最高管理员）去掉独立「我的信息」导航和页面，头像装扮改为个人设置弹窗：电脑点顶部个人头像，手机在「更多」身份卡中打开。恢复GSM/TA GSM/AM/OM的手机登记入口及最高管理员的月结入口，不占新增业务页。TA GSM本人原有成绩首页保留在电脑菜单和手机更多中，普通主管/代理TA主管的首页底栏入口保留。HR两类账号仍不展示头像入口。弹窗按需加载、关闭和离页清理，保存后更新顶部头像，不改变业务页或计分权限。

2026.10.28.3：所有普通非HR账号均提供个人头像入口。CM/TR、主管、代理TA主管及本职主管的代理TA GSM进入「首页」；GSM、AM、OM、SYSTEM_ADMIN等非计分身份进入「我的信息」，只显示身份和头像装扮，不查询个人积分与勋章。手机底栏始终保留该个人入口，其余原有功能仍在「更多」。仅HR_CIRCLE、HR_ADMIN不提供入口，头像偏好接口同样拒绝这两种身份；临时轮岗专用身份继续保持原有隔离。

2026-10-28：用户确认已有明确的内部使用授权，并授权统一呈现样式。这里记录用户确认的使用范围，不构成对外发布许可。本次仅本地源码及素材，未推送、未打包、未部署。

## 素材

共65款，沿用55款并补齐10款。新增6款为内置 image_gen 生成的水彩手绘概念稿：盖瑞、Nibbles、Fru Fru、Dr. Fuzzby、Pawbert、闪电兜风。马市长、朱迪便装、尼克休闲装、甜甜圈豹警官四张生成被工具输出安全检查拦截，未提供具体原因；未重复规避检查，采用公开官方现成画面的裁切、缩放和WebP压缩。三维、二维原画与水彩笔触不同，统一圆形呈现与米色外框，不冒称全部为新手绘。

头像清单、64/160px文件名、字节数、SHA-256、素材类型，以及官方来源与裁切坐标保存在[静态清单](../backend/app/static/images/avatars/catalog.json)。新10款统一放入“疯狂动物城 · 新伙伴与装扮”。官方原画来自[迪士尼角色介绍](https://www.disney.co.jp/column/zootopia)、[动物城2官方图库](https://www.disney.co.jp/fc/zootopia2)和[10周年甜甜圈派对官方插画](https://store.disney.co.jp/special/zootopia-10th/)。其余既有原画来源逐项见清单。

全部65张64px文件合计76044字节，160px合计357628字节。静态清单使用缓存再验证；图片共享URL与ETag，最长缓存1小时。缩略图文件不存员工信息，运行时不请求图片供应商或图像生成服务。

## 本地接入

- 新增 employee_avatar_preferences 表，employee_id主键，仅保存头像key与个人边框。启动沿用Base.metadata.create_all幂等新增，不改旧表、分数、月结快照或历史原文。
- GET /api/self-avatar读取本人偏好及最多12个历史月份的可用边框；PUT只接受avatar_key和frame，拒绝外部URL、路径、自定义身份、未知key和伪造月度边框。临时轮岗身份不能使用此员工偏好接口。
- 普通边框直接可用；历史月度边框必须是本人当前有效封存、早于本月的勋章。保存只核验所选月份，不读取全部历史；普通边框保存完全不读取段位。已经保存的历史边框只是装饰，不是当前绩效；重新打开装扮会刷新可选月份，失效月份不能再次保存。
- 首页复用/me携带的偏好，不自动请求月度勋章。主动打开装扮才加载清单和边框；每页8张，取消不保存，失败保留选择。
- 组员列表、HR组织树各增加一次按已有授权人员ID集合的批量key查询，不提供跨人头像查询接口。组织树分支展开才设置图片src；列表使用64px静态图，不返回边框、排名、分值或勋章。
- 四种庆祝为本地CSS短效果，24个粒子，2.2秒内清理；每轮四种随机出现、相邻不重复。减少动态效果时使用静态文字。收起、换月、页签切换及离页清理效果。原勋章动画、音频逻辑保留。
- 保留HR提示完整原文、默认收起；收藏册与勋章仍默认收起。未变更权限、计分算法或月结流程。

## 验证

隔离数据库测试覆盖本人写入、额外身份字段拒绝、非法key/URL、临时账号隔离、伪造与失效月度边框、普通边框不查段位，以及400个ID只使用一次头像查询。该查询检查不等于400人并发压测。

浏览器夹具 avatar_ui.cjs 使用真实模块与同源严格CSP，验证根路径及/recognition/、延迟加载、分页、失败保留选择、跨次打开偏好、四种庆祝与清理、减少动态效果、320至1280宽度。既有 frontend_modules.cjs 和 monthly_rank_ui.cjs 继续验证完整模块图、个人荣誉、200组旧勋章状态。

追加真实运行验收：avatar_integration_ui.cjs 启动独立FastAPI与全新SQLite，使用随机临时测试密码验证真实登录、头像保存/刷新、主管组员记录和HR组织树；不对现用员工账号写入偏好。真实静态文件验证Cache-Control、ETag与304。截图位于本地output/avatar-live-acceptance。手机端主题使用原生下拉框，避免通用按钮样式把九个主题撑成长列表；边框与翻页保持紧凑布局。夹具服务验收后关闭，本地8000服务保持当前候选版运行。

## 本次生成提示词

使用内置 image_gen，每个角色单独一次调用，未使用CLI或外部生成服务。共同提示模板如下，替换 {subject} 为下一表原文即可还原每条完整提示词：

```text
Use case: illustration-story. Create ONE square employee profile avatar, a hand-painted watercolor and colored-pencil illustration on softly textured ivory paper. Subject: {subject}. Accurate recognizable character identity. Bust portrait with face centered and both ears / main identifying features inside generous circular-safe margins. Gentle warm daylight, delicate fur or scale detail, muted sage foliage and small pastel meadow flowers softly painted in the background. Match a cohesive traditional storybook watercolor avatar collection. No text, no labels, no watermark, no grid, no logo. User confirms authorized internal employee-system use.
```

| Key后缀 | 名称 | subject原文 |
| --- | --- | --- |
| gary | 蛇盖瑞 | Gary De’Snake, the friendly blue pit viper from Zootopia 2, expressive large eyes and two visible fangs |
| nibbles | 海狸Nibbles | Nibbles Maplestick from Zootopia 2, enthusiastic female beaver with prominent incisors and a practical fishing vest |
| frufru | Fru Fru | Fru Fru from Zootopia, tiny elegant Arctic shrew, pointed shrew nose, round ears, fashionable pink dress, definitely a shrew not sheep |
| fuzzby | Dr. Fuzzby | Dr. Fuzzby from Zootopia 2, friendly female quokka therapist with large pink glasses |
| pawbert | 猞猁Pawbert | Pawbert Lynxley from Zootopia 2, young male lynx, distinctive tufted ears and cheek fur, relaxed smile |
| winddancer | 马市长Winddancer | Mayor Brian Winddancer from Zootopia 2, charismatic male horse with long mane, suit and friendly expressive face |
| judy-casual | 朱迪·日常便装 | Judy Hopps from Zootopia, gray female rabbit, purple eyes, long ears, wearing a blue casual sweater instead of police uniform |
| nick-casual | 尼克·休闲装 | Nick Wilde from Zootopia, red fox with green eyes, green Hawaiian shirt and loose tie, relaxed friendly smile |
| clawhauser-donut | 豹警官·甜甜圈 | Benjamin Clawhauser from Zootopia, round cheerful cheetah police receptionist, wearing his blue police uniform and holding one pink donut |
| flash-driving | 闪电·兜风 | Flash the sloth from Zootopia, recognizable slow smiling sloth face in shirt and tie, hands on a small steering wheel at the bottom of the portrait |
