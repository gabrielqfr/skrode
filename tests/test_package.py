import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Built from parts so this file does not match its own pattern.
OLD_NAME = re.compile(("hybrid" + "." + "router").encode(), re.IGNORECASE)


def test_the_package_imports():
    # Keeps `pytest -q` meaningful before S6 adds real modules: an empty run
    # exits 5, which verify.sh would report as a failure.
    import skrode

    assert skrode.__doc__


def test_no_tracked_file_names_the_old_name():
    # The rename (S1r) is complete only when no file outside .claude/ still
    # carries the working name; untracked files that git would add count too.
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    named = [
        path for path in listed
        if not path.startswith(".claude/")
        and (ROOT / path).is_file()
        and OLD_NAME.search((ROOT / path).read_bytes())
    ]
    assert named == []
