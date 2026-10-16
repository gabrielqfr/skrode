"""Totals over finished jobs' records, for the stats tool."""

import statistics

STATUSES = ("passed", "escalate", "failed")


def summarize(records: list[dict]) -> dict:
    status_counts = {status: 0 for status in STATUSES}
    reason_counts: dict[str, int] = {}
    walls: list[float] = []
    prompt = 0
    completion = 0
    for record in records:
        status = record.get("status")
        if isinstance(status, str) and status in status_counts:
            status_counts[status] += 1
        reason = record.get("reason")
        if reason is not None:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        wall = record.get("wall_s")
        # bool is a subclass of int; only real numbers count as seconds
        if isinstance(wall, (int, float)) and not isinstance(wall, bool):
            walls.append(float(wall))
        tokens = record.get("local_tokens")
        if isinstance(tokens, dict):
            prompt += tokens.get("prompt", 0)
            completion += tokens.get("completion", 0)
    return {
        "jobs": len(records),
        "first_job": records[0].get("job_id") if records else None,
        "last_job": records[-1].get("job_id") if records else None,
        "status": status_counts,
        "reasons": dict(
            sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "wall_s": {
            "median": round(float(statistics.median(walls)), 1) if walls else 0.0,
            "total": round(float(sum(walls)), 1) if walls else 0.0,
        },
        "local_tokens": {"prompt": prompt, "completion": completion},
    }
