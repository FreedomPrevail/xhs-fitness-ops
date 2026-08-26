"""User-triggered Golden Sample freshness revalidation."""
from __future__ import annotations

import random
import time
from datetime import date, datetime

from collect import collect
from compliance import operation_policy

from . import repository
from .hydrate import _detail_to_fields


def _heat(likes, collects, comments) -> float:
    return float(likes) + 2.0 * float(collects) + 3.0 * float(comments)


def _days_between(a: str, b: str) -> int:
    def parse(ts):
        value = str(ts or "").strip()[:10]
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    first, second = parse(a), parse(b)
    if first is None or second is None:
        return 0
    return max(0, (second - first).days)


def compute_revalidation_factor(snapshots: list[dict]) -> float:
    """Compute an independent 0.3–1.8 multiplier from observed snapshots."""
    if len(snapshots) < 2:
        return 1.0
    baseline, latest = snapshots[0], snapshots[-1]
    days = max(1, _days_between(baseline.get("observed_at"), latest.get("observed_at")))

    def rate(key):
        current = float(latest.get(key) or 0)
        base = float(baseline.get(key) or 0)
        return (current - base) / max(1.0, base) / days

    velocity = rate("likes") + 2.0 * rate("collects") + 3.0 * rate("comments")
    recent = [_heat(s.get("likes", 0), s.get("collects", 0), s.get("comments", 0))
              for s in snapshots[-3:]]
    persistent = len(recent) >= 2 and all(recent[i] <= recent[i + 1]
                                          for i in range(len(recent) - 1))
    factor = 1.0 + 80.0 * velocity
    if persistent and velocity > 0:
        factor += 0.15
    elif velocity <= 0:
        factor -= 0.15
    return round(min(1.8, max(0.3, factor)), 4)


def revalidate_golden(limit: int = 10) -> dict:
    """Fetch due Golden metrics after a human click and update Pattern evidence.

    The function performs no background scheduling and no retries. Authentication,
    verification and rate-limit errors stop the current batch immediately.
    """
    blocked = operation_policy.blocked_result("platform_revalidate")
    if blocked:
        return {"checked": 0, "revalidated": 0, "errors": [], **blocked}

    samples = repository.list_samples(True)
    today = date.today().isoformat()
    due = []
    for sample in samples:
        if not sample.get("url"):
            continue
        last = sample.get("last_revalidated_at") or sample.get("created_at") or ""
        days = _days_between(last, today)
        if days >= 1:
            due.append((days, sample))
    due.sort(key=lambda item: -item[0])
    todo = [sample for _, sample in due[:max(0, min(30, int(limit)))]]

    checked = 0
    revalidated = 0
    errors = []
    results = []
    hard_stop = None
    for index, sample in enumerate(todo):
        checked += 1
        if index:
            time.sleep(2.5 + random.uniform(0, 1.5))
        try:
            payload, err = collect.run_cli(["note", sample.get("url") or ""], retries=0)
        except RuntimeError as exc:
            payload, err = None, {"code": "RUNTIME", "message": str(exc)}
        if err:
            failure = {"golden_id": sample["golden_id"],
                       "title": (sample.get("title") or "")[:60],
                       "code": str(err.get("code") or "ERROR"),
                       "error": str(err.get("message") or err)[:240]}
            errors.append(failure)
            if failure["code"].upper() in {"SECURITY_CHECK", "RATE_LIMITED", "AUTH"}:
                hard_stop = failure
                break
            continue
        fields = _detail_to_fields(payload)
        likes = int(fields.get("likes") or 0)
        collects = int(fields.get("collects") or 0)
        comments = int(fields.get("comments") or 0)
        if likes <= 0 and collects <= 0 and comments <= 0:
            errors.append({"golden_id": sample["golden_id"], "code": "EMPTY_METRICS",
                           "error": "详情返回无热度字段"})
            continue
        observed_at = repository.now()
        repository.record_heat_snapshot(sample["golden_id"], likes, collects, comments,
                                        is_baseline=False, observed_at=observed_at)
        snapshots = repository.list_heat_snapshots(sample["golden_id"])
        factor = compute_revalidation_factor(snapshots)
        repository.update_revalidation(sample["golden_id"], factor)
        revalidated += 1
        results.append({"golden_id": sample["golden_id"], "title": sample.get("title", ""),
                        "likes": likes, "collects": collects, "comments": comments,
                        "revalidation_score": factor, "snapshots": len(snapshots),
                        "observed_at": observed_at})

    return {"mode": "live_user_triggered", "due": len(due), "checked": checked,
            "revalidated": revalidated, "results": results, "errors": errors,
            "hard_stop": hard_stop,
            "note": "仅在用户点击复查时读取；未设置后台定时任务。"}


def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Golden Sample 热度复查")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(revalidate_golden(args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
