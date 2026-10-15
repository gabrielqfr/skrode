"""Tests for router.profiles: loading profiles and building request parameters."""

import pytest

from router.profiles import ProfileError, load_profile, request_params


def test_bonsai_profile_loads():
    p = load_profile("bonsai2-27b")
    assert p["status"] == "untested"
    assert p["agent"] == {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.05,
        "max_tokens": 16384,
        "chat_template_kwargs": {"reasoning_effort": "medium"},
    }
    assert p["oneshot"] == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.5,
    }


def test_request_params_agent():
    p = load_profile("bonsai2-27b")
    assert request_params(p) == p["agent"]


def test_extra_is_merged_last():
    profile = {"agent": {"temperature": 1.0, "extra": {"temperature": 0.5, "n_probs": 0}}}
    assert request_params(profile) == {"temperature": 0.5, "n_probs": 0}


def test_request_params_is_a_copy():
    p = load_profile("bonsai2-27b")
    out = request_params(p)
    out["chat_template_kwargs"]["reasoning_effort"] = "changed"
    assert p["agent"]["chat_template_kwargs"] == {"reasoning_effort": "medium"}


def test_missing_or_unknown_mode():
    with pytest.raises(ProfileError):
        request_params({"agent": {}}, "oneshot")
    with pytest.raises(ProfileError):
        request_params({"agent": {}}, "chat")


@pytest.mark.parametrize("name", ["", "../x", ".hidden", "a/b"])
def test_bad_names_are_refused(name):
    with pytest.raises(ProfileError):
        load_profile(name)


def test_bad_files_are_refused(tmp_path):
    with pytest.raises(ProfileError):
        load_profile("missing", directory=tmp_path)
    (tmp_path / "bad1.yaml").write_text("agent:\n  temperature: 5\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile("bad1", directory=tmp_path)
    (tmp_path / "bad2.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile("bad2", directory=tmp_path)
    (tmp_path / "bad3.yaml").write_text("agent: [\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile("bad3", directory=tmp_path)
