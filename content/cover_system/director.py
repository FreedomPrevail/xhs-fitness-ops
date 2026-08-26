from __future__ import annotations

import re
from dataclasses import dataclass, asdict

CATEGORY_META = {
    "fitness_value": {"kicker": "FITNESS NOTES", "badge": "训练干货", "tone": "energy"},
    "health_risk": {"kicker": "HEALTH GUIDE", "badge": "健康提醒", "tone": "calm"},
    "insurance_soft": {"kicker": "SMART HEALTH", "badge": "理性保障", "tone": "trust"},
    "_default": {"kicker": "DAILY GUIDE", "badge": "今日笔记", "tone": "neutral"},
}

PAIN_WORDS = ("别", "错", "坑", "误区", "为什么", "怎么", "越", "不瘦", "变胖", "粗", "疼", "风险", "注意")
ACTION_WORDS = ("方法", "步骤", "动作", "清单", "计划", "攻略", "教程", "练法", "吃法", "技巧")


@dataclass
class CoverBrief:
    primary_text: str
    secondary_text: str
    highlight_text: str
    kicker: str
    badge: str
    tone: str
    recommended_template: str
    number_token: str
    question: bool

    def to_dict(self):
        return asdict(self)


def _clean(s: str) -> str:
    s = re.sub(r"[\r\n\t]+", " ", s or "")
    s = re.sub(r"[#*【】\[\]<>]", "", s)
    return re.sub(r"\s+", " ", s).strip(" ，,。.!！?？、")


def _split_copy(text: str) -> tuple[str, str]:
    """把长正文标题压缩成封面主副标题，不让整句全部塞到封面。"""
    text = _clean(text)
    if not text:
        return "今天练什么？", "一眼看懂重点"

    # 先利用明显标点/转折切分。
    parts = [p.strip() for p in re.split(r"[：:｜|—–，,。；;！!？?]", text) if p.strip()]
    if len(parts) >= 2:
        primary = parts[0]
        secondary = " · ".join(parts[1:3])
    else:
        primary, secondary = text, ""

    # 主标题控制在手机首屏容易读完的长度。
    if len(primary) > 16:
        primary = primary[:16]
    if not secondary and len(text) > len(primary):
        secondary = text[len(primary):].strip(" ，,。.!！?？、")
    if len(secondary) > 22:
        secondary = secondary[:22]
    return primary, secondary


def _highlight(primary: str) -> str:
    n = re.search(r"\d+(?:\.\d+)?(?:个|天|周|分钟|%|斤|步|招|点)?", primary)
    if n:
        return n.group(0)
    for word in PAIN_WORDS + ACTION_WORDS:
        if word in primary:
            # 给高亮一个可见短语，而不是只高亮一个“别/错”字。
            i = primary.find(word)
            return primary[max(0, i - 2): min(len(primary), i + max(3, len(word) + 2))]
    return primary[:4]


def build_brief(cover_text: str, category: str = "_default", title: str = "") -> dict:
    source = _clean(cover_text) or _clean(title)
    primary, secondary = _split_copy(source)
    question = any(x in source for x in ("?", "？", "为什么", "怎么", "到底", "吗"))
    number = re.search(r"\d+(?:\.\d+)?", source)
    if question or any(w in source for w in PAIN_WORDS):
        recommended = "question"
    elif number or any(w in source for w in ACTION_WORDS):
        recommended = "steps"
    else:
        recommended = "editorial"

    meta = CATEGORY_META.get(category, CATEGORY_META["_default"])
    return CoverBrief(
        primary_text=primary,
        secondary_text=secondary or "把重点说清楚，比堆信息更重要",
        highlight_text=_highlight(primary),
        kicker=meta["kicker"],
        badge=meta["badge"],
        tone=meta["tone"],
        recommended_template=recommended,
        number_token=number.group(0) if number else "01",
        question=question,
    ).to_dict()
