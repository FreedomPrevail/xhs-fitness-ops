"""Runtime gate for operator-triggered Xiaohongshu workflows.

Collection, search, detail hydration and Golden revalidation may run only from
explicit dashboard/API actions. Interaction and publishing stay blocked.
"""
from __future__ import annotations

import os

DEFAULT_MODE = "manual_connected"
MODE = (os.environ.get("XHS_OPERATION_MODE") or DEFAULT_MODE).strip().lower()

PLATFORM_ACTIONS = {
    "platform_search",
    "platform_collect",
    "creator_center_collect",
    "platform_detail_fetch",
    "platform_revalidate",
    "platform_publish",
    "platform_interaction",
    "verification_session",
}

def blocked_actions(mode: str | None = None) -> set[str]:
    """Return actions that are never part of the content-analysis workflow."""
    return {"platform_publish", "platform_interaction"}


BLOCKED_ACTIONS = blocked_actions()


def check(action: str, mode: str | None = None) -> dict:
    """Return a machine-readable decision for one operation."""
    action = str(action or "").strip()
    selected = str(mode or MODE or DEFAULT_MODE).strip().lower()
    blocked = action in blocked_actions(selected)
    if blocked:
        message = (
            "手动连接模式允许用户主动触发数据读取、搜索和 Golden 复查，"
            "但不执行平台互动或自动发布。"
        )
    else:
        message = "操作符合当前运行模式"
    return {
        "allowed": not blocked,
        "mode": selected,
        "action": action,
        "code": "INTERACTION_OR_PUBLISH_BLOCKED" if blocked else "ALLOWED",
        "message": message,
    }


def blocked_result(action: str) -> dict | None:
    decision = check(action)
    if decision["allowed"]:
        return None
    return {"ok": False, "operation_policy": decision, "error": decision["message"]}
