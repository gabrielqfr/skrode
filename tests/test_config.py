"""Tests for router.config: the user's per-repo setup commands."""

from pathlib import Path

import pytest

from router.config import CONFIG_PATH, ConfigError, load_config


def test_the_default_path_is_under_the_users_config_dir():
    expected = Path.home() / ".config" / "hybrid-router" / "config.toml"
    assert expected == CONFIG_PATH


def test_a_missing_file_is_an_empty_config(tmp_path):
    assert load_config(tmp_path / "config.toml") == {}


def test_a_missing_file_is_an_error_when_required(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(ConfigError) as caught:
        load_config(path, required=True)
    assert str(caught.value) == f"{path} does not exist"


def test_an_empty_file_is_an_empty_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("")
    assert load_config(path, required=True) == {}


def test_each_repo_maps_its_resolved_path_to_its_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    path = tmp_path / "config.toml"
    path.write_text(
        "[[repo]]\n"
        f'path = "{tmp_path / "link"}"\n'
        'setup = ["uv sync --frozen", "make deps"]\n'
        "\n"
        "[[repo]]\n"
        'path = "~/code/other"\n'
        "setup = []\n"
    )
    assert load_config(path) == {
        real.resolve(): ("uv sync --frozen", "make deps"),
        (tmp_path / "home" / "code" / "other").resolve(): (),
    }


def test_a_file_that_is_not_toml_is_an_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[[repo]\n")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert str(caught.value).startswith(f"{path}: ")


NO_PATH = "repo 1: path must be a non-empty string"
NO_SETUP = "repo 1: setup must be a list of non-empty strings"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("repos = []\n", "unknown key 'repos'"),
        ('repo = "x"\n', "repo must be an array of tables"),
        ('repo = ["x"]\n', "repo must be an array of tables"),
        ('[[repo]]\npath = "/r"\nsetup = []\nenv = 1\n', "repo 1: unknown key 'env'"),
        ("[[repo]]\nsetup = []\n", NO_PATH),
        ('[[repo]]\npath = ""\nsetup = []\n', NO_PATH),
        ("[[repo]]\npath = 3\nsetup = []\n", NO_PATH),
        (
            '[[repo]]\npath = "code/r"\nsetup = []\n',
            "repo 1: path must be absolute or start with ~",
        ),
        ('[[repo]]\npath = "/r"\n', NO_SETUP),
        ('[[repo]]\npath = "/r"\nsetup = "uv sync"\n', NO_SETUP),
        ('[[repo]]\npath = "/r"\nsetup = [""]\n', NO_SETUP),
        ('[[repo]]\npath = "/r"\nsetup = [1]\n', NO_SETUP),
        (
            '[[repo]]\npath = "/r"\nsetup = []\n[[repo]]\npath = "/r/"\nsetup = []\n',
            "repo 2: /r is listed twice",
        ),
    ],
)
def test_a_badly_shaped_file_is_an_error(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert str(caught.value) == f"{path}: {message}"
