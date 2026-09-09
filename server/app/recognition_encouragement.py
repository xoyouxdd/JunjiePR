"""Local, deterministic encouragement copy for frontline recognition submissions.

This module never calls an external model or service. It deliberately uses
only the existing recognition category and a small behavior tag inferred from
the already-submitted short note; it neither reads nor processes attachments.
"""

from __future__ import annotations

from hashlib import sha256


_TYPE_THEMES = {
    "安全": ("对安全细节的坚持", "把风险止于未然的专业", "守住现场安心的责任感"),
    "礼仪": ("真诚周到的服务", "耐心与分寸并重的回应", "让游客感到被尊重的细致"),
    "包容": ("对不同需要的体察", "愿意理解与接纳的耐心", "让身边人感到被照顾的善意"),
    "效率": ("主动补位的协作", "忙而不乱的执行", "让现场更从容的担当"),
    "演出": ("认真投入的呈现", "让体验更有光彩的专注", "为现场氛围增添的用心"),
}
_BEHAVIOR_THEMES = (
    ("协作", ("协作", "配合", "帮助", "支援", "分流", "补位"), "看见了伙伴之间彼此支撑的力量。"),
    ("沟通", ("沟通", "解释", "耐心", "引导", "询问", "回应"), "把耐心留在每一次真诚沟通里。"),
    ("游客", ("游客", "客人", "宾客", "服务", "客诉"), "让对游客的关照落在了具体行动上。"),
    ("细节", ("细节", "检查", "整理", "及时", "主动", "发现"), "把容易被忽略的细节认真守住。"),
    ("安全", ("安全", "提醒", "隐患", "秩序", "风险"), "用专业守护了现场的安心。"),
    ("演出", ("演出", "互动", "氛围", "表演", "舞台"), "让现场体验多了一份投入与光彩。"),
)
_SUBMIT_OPENERS = (
    "你认真记录下了这份认可。", "谢谢你及时看见身边的闪光。", "这份用心的记录，值得被好好保存。", "你让发生在现场的努力有了回响。",
    "认真看见，本身就是一种温暖的力量。", "你把当下的好，留成了可被看见的记录。", "每一次及时认可，都在为团队增添一份确定。", "你用一条记录回应了身边真实的付出。",
)
_CONFIRM_OPENERS = (
    "这份认可已完成确认。", "你记录的这份认可已完成确认，也被正式看见。", "这次认真发现，已完成团队确认。", "这份来自现场的肯定，已完成确认并留下清晰印记。",
    "你及时提交的认可，现已完成确认。", "这份对伙伴的看见，已经得到确认。", "现场的闪光被郑重记录，也已完成确认。", "这份认可已经走完确认流程。",
)
_CLOSINGS = (
    "愿这份认真，继续点亮更多平凡而可贵的时刻。", "让每一次用心，都在团队里留下温暖的回应。", "谢谢你让优秀不只停留在当下。", "把身边的美好看见，也把团队的力量汇聚起来。",
    "愿我们继续把细致、担当和善意传递下去。", "每一份具体的肯定，都会让前行更有力量。", "这样的看见，会让团队的温度更清晰。", "谢谢你为团队留下这份真实、可感的鼓励。",
)


def _pick(values: tuple[str, ...], seed: str, offset: int) -> str:
    digest = sha256(f"{seed}:{offset}".encode("utf-8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def _behavior_theme(content: str, recognition_type_name: str) -> tuple[str, str]:
    normalized = str(content or "").replace(" ", "")
    for key, keywords, sentence in _BEHAVIOR_THEMES:
        if any(keyword in normalized for keyword in keywords):
            return key, sentence
    themes = _TYPE_THEMES.get(str(recognition_type_name or ""), ("现场的认真投入",))
    return "general", f"你看见了{themes[0]}。"


def encouragement_options(*, record_id: int, recognition_type_name: str, content: str, stage: str, count: int = 6) -> list[dict[str, str]]:
    """Return local options for the client to rotate without a new request."""
    phase = "confirmed" if stage == "confirmed" else "submitted"
    behavior_key, behavior_sentence = _behavior_theme(content, recognition_type_name)
    category_themes = _TYPE_THEMES.get(str(recognition_type_name or ""), ("现场的认真投入",))
    seed = f"{record_id}:{recognition_type_name}:{behavior_key}:{phase}"
    openers = _CONFIRM_OPENERS if phase == "confirmed" else _SUBMIT_OPENERS
    options: list[dict[str, str]] = []
    for index in range(max(1, count)):
        category = _pick(category_themes, seed, index + 11)
        message = f"{_pick(openers, seed, index + 23)} {behavior_sentence}{category}，{_pick(_CLOSINGS, seed, index + 37)}"
        options.append({"template_id": f"v1:{phase}:{recognition_type_name or 'general'}:{behavior_key}:{index}", "message": message})
    return options


def encouragement_message(**kwargs: object) -> str:
    """Choose a deterministic first option for an in-app or push confirmation."""
    return encouragement_options(**kwargs, count=1)[0]["message"]
