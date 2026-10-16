from skrode.stats import STATUSES, summarize


def record(job_id, status, wall_s, prompt, completion, reason=None):
    out = {
        "job_id": job_id,
        "status": status,
        "wall_s": wall_s,
        "local_tokens": {"prompt": prompt, "completion": completion, "reasoning": 7},
    }
    if reason is not None:
        out["reason"] = reason
    return out


def test_statuses_are_the_three_record_statuses():
    assert STATUSES == ("passed", "escalate", "failed")


def test_no_records_summarize_to_zeros():
    assert summarize([]) == {
        "jobs": 0,
        "first_job": None,
        "last_job": None,
        "status": {"passed": 0, "escalate": 0, "failed": 0},
        "reasons": {},
        "wall_s": {"median": 0.0, "total": 0.0},
        "local_tokens": {"prompt": 0, "completion": 0},
    }


def test_counts_statuses_reasons_wall_time_and_tokens():
    records = [
        record("j-20261015-aaaaaa", "passed", 100.0, 1000, 200),
        record("j-20261015-bbbbbb", "escalate", 300.2, 3000, 600, "turn_limit"),
        record("j-20261016-cccccc", "failed", 0.5, 0, 0, "cancelled"),
        record("j-20261016-dddddd", "escalate", 40.0, 500, 100, "turn_limit"),
    ]
    summary = summarize(records)
    assert summary == {
        "jobs": 4,
        "first_job": "j-20261015-aaaaaa",
        "last_job": "j-20261016-dddddd",
        "status": {"passed": 1, "escalate": 2, "failed": 1},
        "reasons": {"turn_limit": 2, "cancelled": 1},
        "wall_s": {"median": 70.0, "total": 440.7},
        "local_tokens": {"prompt": 4500, "completion": 900},
    }
    assert list(summary) == [
        "jobs",
        "first_job",
        "last_job",
        "status",
        "reasons",
        "wall_s",
        "local_tokens",
    ]
    assert list(summary["status"]) == ["passed", "escalate", "failed"]


def test_reasons_run_from_most_to_least_common_then_by_name():
    records = [
        record("j-1", "failed", 1.0, 0, 0, "timeout"),
        record("j-2", "escalate", 1.0, 0, 0, "checks_failed"),
        record("j-3", "escalate", 1.0, 0, 0, "turn_limit"),
        record("j-4", "escalate", 1.0, 0, 0, "turn_limit"),
        record("j-5", "failed", 1.0, 0, 0, "cancelled"),
    ]
    assert list(summarize(records)["reasons"].items()) == [
        ("turn_limit", 2),
        ("cancelled", 1),
        ("checks_failed", 1),
        ("timeout", 1),
    ]


def test_wall_time_rounds_to_a_tenth():
    records = [
        record("j-1", "passed", 1.04, 0, 0),
        record("j-2", "passed", 2.13, 0, 0),
    ]
    assert summarize(records)["wall_s"] == {"median": 1.6, "total": 3.2}


def test_missing_fields_count_as_nothing():
    records = [
        {"job_id": "j-20261015-aaaaaa", "status": "passed"},
        {"status": "weird", "wall_s": True, "local_tokens": {"prompt": 5}},
        {"wall_s": 3, "reason": "endpoint"},
    ]
    assert summarize(records) == {
        "jobs": 3,
        "first_job": "j-20261015-aaaaaa",
        "last_job": None,
        "status": {"passed": 1, "escalate": 0, "failed": 0},
        "reasons": {"endpoint": 1},
        "wall_s": {"median": 3.0, "total": 3.0},
        "local_tokens": {"prompt": 5, "completion": 0},
    }
