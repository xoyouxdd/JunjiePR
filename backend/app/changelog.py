"""In-app release notes. Keep APP_VERSION equal to the first entry's version."""

from __future__ import annotations

from app.version import APP_VERSION

# audiences: role codes, or "all". permissions: optional extra match on user.permissions.
RELEASES: list[dict] = [
    {
        "version": "2026.09.10.8",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "待复核列表不会被不通过记录挤掉",
                "detail": "待处理只显示待复核；已确认和不通过放到已处理历史。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR"],
            },
            {
                "summary": "材料任务改为一次只能被一个人领取",
                "detail": "后台生成PDF时用条件更新抢任务；成功写入后再删除源文件，异常中断可在超时后重试。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM"],
            },
            {
                "summary": "撤回和整组移交改用统一确认框",
                "detail": "不再弹出浏览器原生确认；撤回按钮显示文字，并带有明确说明。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.10.7",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "上传组件升级，大文件不再卡死服务",
                "detail": "服务端解析库已升级到含大文件修复的版本，并与100MB材料上限对齐。",
                "audiences": ["all"],
            },
            {
                "summary": "弹窗和手机更多菜单共用同一套键盘操作",
                "detail": "确认框、输入框、预览和更多抽屉都会锁住背景、Tab循环、Esc关闭，并回到原来的按钮。日期和文件选择器仍走系统原生窗口。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.10.6",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "启动不再覆盖已保存的角色分值",
                "detail": "默认分值只在全新库补一次。已经人工改过的分值，跨日或跨月重启后仍按保存结果计分。",
                "audiences": ["SYSTEM_ADMIN", "HR_CIRCLE", "HR_ADMIN"],
            },
            {
                "summary": "POC只能走专用入口",
                "detail": "普通加分登记不再出现POC类型；没有开具权限的账号用任何入口提交都会被拒绝。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM"],
            },
            {
                "summary": "记录同时显示原始分和计入分",
                "detail": "被月度上限卡住的加分会标明实际计入分，手机卡片也能看到签卡人、说明和材料。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.10.5",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "新建员工账号表单重新排版",
                "detail": "电脑端字段顶部对齐并调整宽度，创建按钮收至右侧；平板自动两列，手机保持单列满宽。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.10.4",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "查询、复核和页面切换更稳定",
                "detail": "月度分数只计算当前范围；复核分开待处理与历史；切页会停止旧请求和材料轮询。",
                "audiences": ["all"],
            },
            {
                "summary": "管理代录和员工管理恢复可用",
                "detail": "管理人员代录无需认可图片；员工批量保存按钮使用稳定绑定，手机端避开底部导航。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "SYSTEM_ADMIN", "HR_CIRCLE", "HR_ADMIN"],
            },
            {
                "summary": "分值设置明确为全局规则",
                "detail": "仅最高管理员可调整；其他HR角色可查看当前规则，历史签卡不追溯改分。",
                "audiences": ["SYSTEM_ADMIN", "HR_CIRCLE", "HR_ADMIN"],
            },
        ],
    },
    {
        "version": "2026.09.10.3",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "员工管理导入和新建账号对齐",
                "detail": "选择文件、下载模板、导入按钮同一行同高；新建账号的创建按钮单独一行。手机上按钮拉满宽度。",
                "audiences": ["SYSTEM_ADMIN", "HR_CIRCLE", "HR_ADMIN"],
            },
        ],
    },
    {
        "version": "2026.09.10.2",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "电脑登录页补回整张卡片",
                "detail": "标题和表单重新包在同一白底边框里。手机仍是顶部深蓝、白卡片压在分界上。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.10.1",
        "date": "2026-09-10",
        "status": "candidate",
        "items": [
            {
                "summary": "顶栏绑定提示去掉「外传可追溯」",
                "detail": "只保留姓名、工号和时间。全页水印略加深，仍比最初淡。",
                "audiences": ["all"],
            },
            {
                "summary": "电脑登录页恢复正常",
                "detail": "深蓝品牌区只在手机上用；电脑仍是浅底深色标题。手机「更多」能看到更新记录和修改密码。",
                "audiences": ["all"],
            },
        ],
    },
    {
        "version": "2026.09.09.1",
        "date": "2026-09-09",
        "status": "candidate",
        "items": [
            {
                "summary": "版本号改成日期加当天第几版",
                "detail": "例如 2026.09.09.1 表示 2026年9月9日第1版。以前的 2.23.73 仍保留在记录里。",
                "audiences": ["all"],
            },
            {
                "summary": "水印更轻，顶部增加绑定提示",
                "detail": "全页水印改为「内部资料 + 工号」，更淡更少。顶栏显示本页绑定的姓名工号和时间，方便外传追溯。",
                "audiences": ["all"],
            },
            {
                "summary": "导航增加「更新记录」",
                "detail": "只显示和当前角色相关的变更，方便核对自己界面改了什么。",
                "audiences": ["all"],
            },
            {
                "summary": "登录页手机更好用",
                "detail": "卡片垂直居中；输入焦点为品牌蓝；顶部深蓝与地址栏连成一体。可显示密码、记住上次员工号；登录失败有红底提示。",
                "audiences": ["all"],
            },
            {
                "summary": "修改密码放到导航栏",
                "detail": "和其它角色一样，从导航进入修改密码；首页不再单独放按钮。手机在「更多」里。",
                "audiences": ["CM", "TR"],
            },
            {
                "summary": "手机底部导航，更多功能进全屏列表",
                "detail": "小屏不再挤在顶部标签；待办角标仍会显示。",
                "audiences": ["all"],
            },
            {
                "summary": "错误提示可点关闭，不会一闪而过",
                "detail": "成功提示仍会自动消失。",
                "audiences": ["all"],
            },
            {
                "summary": "清空日期后不会再被旧日期顶回去",
                "detail": "选过日期再清掉，提交会要求重新选择，而不是静默用上一次的日期。",
                "audiences": ["CM", "TR", "TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM"],
            },
            {
                "summary": "切页时旧内容不会盖住新页面",
                "detail": "网络慢时快速切换模块，最终只保留当前页。",
                "audiences": ["all"],
            },
            {
                "summary": "材料生成完成后不再显示「生成中」",
                "detail": "成功、失败、待补充是不同状态；失败可以重新提交。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM"],
            },
            {
                "summary": "复核和登记记录：手机用卡片，电脑仍用表格",
                "detail": "操作按钮不变。电脑宽表首列冻结，方便横滑对照人员。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM"],
            },
            {
                "summary": "缺勤员工搜索与提交范围一致",
                "detail": "主管按姓名/工号可搜全部圈在职 CM/TR；TA主管仍限本圈。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR"],
            },
            {
                "summary": "超过 6 页的 PDF 会被拒绝，并且不留临时文件",
                "detail": "请拆分后再传。",
                "audiences": ["TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM"],
            },
            {
                "summary": "POC 特别贡献重复提交只会记一笔",
                "detail": "同一提交键、同一内容会返回原记录；改了内容再用旧键会提示冲突。",
                "audiences": ["TA_GSM", "GSM", "AM"],
            },
            {
                "summary": "Excel 导出更易读、更安全",
                "detail": "中文列宽按字数加宽；以 = + - @ 开头的文字不会被 Excel 当成公式；排名导出也有中文文件名。统计导出封面含加分/扣分合计和综合分 Top3。",
                "audiences": ["GSM", "AM", "OM", "HR_ADMIN", "SYSTEM_ADMIN"],
                "permissions": ["DATA_EXPORT"],
            },
            {
                "summary": "全局月结会检查所有圈的待办",
                "detail": "有待复核、待补材料等事项时不能关账。最高管理员可选「全部景点圈」。",
                "audiences": ["SYSTEM_ADMIN", "HR_CIRCLE", "HR_ADMIN"],
            },
            {
                "summary": "发布打包不再带上本机环境文件",
                "detail": "排除 .env、.venv、secrets.json 和数据库。包名跟程序版本走。",
                "audiences": ["SYSTEM_ADMIN"],
            },
        ],
    },
    {
        "version": "2.23.73",
        "date": "2026-09-04",
        "status": "production",
        "items": [
            {
                "summary": "当前生产版本",
                "detail": "正式入口已统一到直连地址。本页从 2026.09.09.1 起按角色记录界面变更；更早细节以当时培训说明为准。",
                "audiences": ["all"],
            },
        ],
    },
]


def item_visible(item: dict, role_code: str, permissions: set[str]) -> bool:
    audiences = item.get("audiences") or ["all"]
    if "all" in audiences or role_code in audiences:
        return True
    needed = set(item.get("permissions") or [])
    return bool(needed and needed & permissions)


def visible_releases(role_code: str, permissions: set[str]) -> list[dict]:
    releases = []
    for release in RELEASES:
        items = [
            {"summary": item["summary"], "detail": item.get("detail") or ""}
            for item in release.get("items") or []
            if item_visible(item, role_code, permissions)
        ]
        if not items:
            continue
        releases.append(
            {
                "version": release["version"],
                "date": release.get("date") or "",
                "status": release.get("status") or "",
                "current": release["version"] == APP_VERSION,
                "items": items,
            }
        )
    return releases
