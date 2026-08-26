"""健身内容的版本化、可解释合规门禁。

这不是“绕违禁词”工具。BLOCK 不自动同义替换，必须修改事实表达或人工复核。
"""
from __future__ import annotations

import re
from datetime import date

POLICY_VERSION = "2026-08-19-connected-v4"
POLICY_SOURCES = [
    "https://agree.xiaohongshu.com/h5/terms/ZXXY20221213003/-1",
    "https://pgy.xiaohongshu.com/help/detail?id=6495c527d1eedeeb48fb18b1f875650e&userType=4",
]

ABSOLUTE_CLAIMS = ("保证瘦", "一定瘦", "永久", "百分百", "100%有效", "包瘦", "必掉秤", "零风险", "最管用", "彻底改善", "最有效")
MEDICAL_CLAIMS = ("治疗", "治愈", "根治", "预防疾病", "替代药物", "消除炎症", "修复半月板")
EXTREME_METHODS = ("绝食", "催吐", "一天一顿", "不吃主食", "断水减重", "每天只吃")
BODY_ANXIETY = ("胖就是不自律", "丑胖", "肥婆", "没人喜欢胖子", "胖子不配")
FALSE_EXPERIENCE = ("亲测百分百", "所有人都有效", "照做必瘦", "医生都不会告诉你")
CONTACT_PATTERNS = (r"加\s*[Vv微薇]\s*[信❤❤️]?", r"私[信聊]\s*(我|领取|咨询|领福利)",
                    r"扫码\s*(加|进群)", r"(看|移步|点开)\s*(我)?\s*(主页|简介)",
                    r"完整(版|内容).{0,6}(主页|私信|其他地方)", r"联系方式")


def _issue(code: str, severity: str, message: str, evidence: str = "", suggestion: str = "") -> dict:
    return {"code": code, "severity": severity, "message": message,
            "evidence": evidence[:80], "suggestion": suggestion}


def evaluate(title: str, body: str, cover_text: str = "", *, aigc_label_required: bool = False) -> dict:
    text = "\n".join(str(x or "") for x in (title, body, cover_text))
    issues = []
    for phrase in ABSOLUTE_CLAIMS:
        if phrase in text:
            issues.append(_issue("absolute_result_claim", "BLOCK", "绝对化健身/减重效果承诺", phrase,
                                 "改为个人条件、过程和不确定性说明"))
    for phrase in MEDICAL_CLAIMS:
        if phrase in text:
            issues.append(_issue("medical_effect_claim", "BLOCK", "疑似疾病治疗或医疗效果宣称", phrase,
                                 "删除治疗结论；必要时建议咨询合格专业人士"))
    for phrase in EXTREME_METHODS:
        if phrase in text:
            issues.append(_issue("unsafe_fitness_method", "BLOCK", "包含极端饮食或危险减重方式", phrase,
                                 "改为可持续饮食与训练建议"))
    for phrase in BODY_ANXIETY:
        if phrase in text:
            issues.append(_issue("body_shaming", "BLOCK", "包含身材羞辱或焦虑制造", phrase,
                                 "使用尊重、健康导向的表达"))
    for phrase in FALSE_EXPERIENCE:
        if phrase in text:
            issues.append(_issue("false_or_unverifiable_experience", "BLOCK", "疑似虚构体验或不可验证保证", phrase,
                                 "改为可观察事实并注明个人差异"))
    for pattern in CONTACT_PATTERNS:
        hit = re.search(pattern, text)
        if hit:
            issues.append(_issue("off_platform_diversion", "BLOCK", "疑似站外导流", hit.group(0), "删除导流表达"))
    if re.search(r"\d+\s*(天|分钟|周).{0,8}(瘦|掉|减)\s*\d+\s*(斤|公斤|kg)", text, re.I):
        issues.append(_issue("rapid_weight_loss_claim", "WARN", "包含明确期限与减重结果，需核实真实性和安全性",
                             suggestion="说明个体差异、基础条件和安全边界"))
    if any(x in text for x in ("前后对比", "逆袭照", "对比图")):
        issues.append(_issue("before_after_evidence", "WARN", "效果对比图需要确认未过度修饰且条件一致"))
    if aigc_label_required:
        issues.append(_issue("aigc_disclosure_manual", "WARN",
                             "发布前需在小红书官方发布界面人工确认适用的AI辅助创作标识",
                             suggestion="该状态无法由本地正文判断或自动勾选"))
    if title and body and not any(token in body for token in re.findall(r"[\u4e00-\u9fff]{2,4}", title)[:6]):
        issues.append(_issue("title_body_alignment", "WARN", "标题与正文的显式关键词关联较弱"))
    status = "BLOCK" if any(x["severity"] == "BLOCK" for x in issues) else \
             "WARN" if issues else "PASS"
    return {"status": status, "issues": issues, "policy_version": POLICY_VERSION,
            "checked_at": date.today().isoformat(), "sources": POLICY_SOURCES,
            "note": "规则是风险筛查，不替代平台最终审核或专业法律/医疗意见。"}


def quality_assessment(title: str, body: str, cover_text: str="") -> dict:
    """可解释的账号内容质量检查；不是冒充平台内部流量公式。"""
    title=str(title or ""); body=str(body or ""); cover_text=str(cover_text or "")
    lines=[x.strip() for x in body.splitlines() if x.strip()]
    specificity=min(1.0,(len(re.findall(r"\d+|次|组|分钟|克|步骤",title+body))/5))
    actionable=min(1.0,len(re.findall(r"建议|动作|步骤|先|再|注意|保持|训练|休息|替换",body))/8)
    structure=min(1.0,len(lines)/8)
    caveat=1.0 if any(x in body for x in ("根据自身","循序渐进","个体差异","如有不适","咨询专业")) else .35
    audience=1.0 if any(x in title+body for x in ("新手","久坐","上班族","女生","男生","膝盖","减脂期","居家")) else .45
    title_tokens=set(re.findall(r"[\u4e00-\u9fff]{2,4}",title))
    body_tokens=set(re.findall(r"[\u4e00-\u9fff]{2,4}",body))
    alignment=min(1.0,len(title_tokens&body_tokens)/max(1,min(3,len(title_tokens)))) if title_tokens else .5
    if cover_text:
        cover_tokens=set(re.findall(r"[\u4e00-\u9fff]{2,4}",cover_text))
        alignment=(alignment+(min(1.0,len(cover_tokens&title_tokens)/max(1,len(cover_tokens))) if cover_tokens else .5))/2
    dimensions={"specificity":specificity,"actionability":actionable,"structure":structure,
                "safety_context":caveat,"audience_clarity":audience,"title_body_cover_alignment":alignment}
    weights={"specificity":.15,"actionability":.25,"structure":.15,"safety_context":.15,
             "audience_clarity":.15,"title_body_cover_alignment":.15}
    score=sum(dimensions[k]*weights[k] for k in weights)
    suggestions=[]
    if actionable<.5:suggestions.append("增加可执行步骤、动作要点或替代方案")
    if specificity<.4:suggestions.append("增加可核实的数量、条件或训练细节")
    if caveat<.5:suggestions.append("补充个体差异和安全边界")
    if alignment<.5:suggestions.append("加强标题、正文和封面表达的一致性")
    return {"score":round(score,3),"dimensions":{k:round(v,3) for k,v in dimensions.items()},
            "suggestions":suggestions,"note":"本地可解释质量检查，不代表平台内部优质创作者评分。"}
