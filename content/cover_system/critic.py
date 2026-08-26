from __future__ import annotations


def critique(brief: dict) -> dict:
    """轻量视觉 Critic：先做可计算的排版约束，不伪装成视觉大模型。"""
    issues = []
    primary = (brief.get("primary_text") or "").strip()
    secondary = (brief.get("secondary_text") or "").strip()

    readability = 1.0
    hierarchy = 1.0
    clickability = 0.82

    if len(primary) > 16:
        issues.append("主标题偏长，已建议压缩到16字以内")
        readability -= 0.18
    if len(primary) < 5:
        issues.append("主标题信息量偏低")
        clickability -= 0.12
    if len(secondary) > 24:
        issues.append("副标题偏长")
        hierarchy -= 0.12
    if brief.get("highlight_text") and brief.get("highlight_text") == primary:
        hierarchy -= 0.05
    if brief.get("question"):
        clickability += 0.06

    return {
        "readability": round(max(0, min(1, readability)), 2),
        "visual_hierarchy": round(max(0, min(1, hierarchy)), 2),
        "clickability": round(max(0, min(1, clickability)), 2),
        "issues": issues,
        "pass": readability >= 0.75 and hierarchy >= 0.75,
    }
