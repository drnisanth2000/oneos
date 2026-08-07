from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEPRECATED = (
    "Life" + "OS",
    "OneOS " + "Web",
    "OneOS " + "Console",
    "oneos-" + "web",
    "life" + "os" + "_",
)
SCANNED_SUFFIXES = {".py", ".md", ".toml", ".css", ".html", ".yaml", ".yml"}
PROCESS_RECORDS = (ROOT / "docs" / "superpowers", ROOT / ".superpowers")


def test_python_project_is_oneos():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "oneos"


def test_lockfile_uses_oneos_project_name():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "oneos"' in lock
    assert 'name = "' + "oneos-" + 'web"' not in lock


def test_status_uses_portable_oneos_spec_path():
    status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    assert "$ONEOS_VAULT/_system/docs/oneos-spec.md" in status
    assert "`_system/docs/oneos-spec.md`" not in status


def test_active_public_files_use_oneos_names():
    findings = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if (
            ".git" in path.parts
            or ".worktrees" in path.parts
            or ".venv" in path.parts
            or any(record in path.parents for record in PROCESS_RECORDS)
        ):
            continue
        text = path.read_text(encoding="utf-8")
        for term in DEPRECATED:
            if term.casefold() in text.casefold() or term.casefold() in path.name.casefold():
                findings.append(f"{path.relative_to(ROOT)}: {term}")
    assert findings == []
