import os
from pathlib import Path
import shutil
import subprocess

import pytest


WRAPPER = Path(__file__).parents[1] / "tools" / "run_gitleaks.sh"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


@pytest.fixture
def fake_gitleaks(tmp_path: Path):
    def make(*, version: str, exit_code: int = 0) -> tuple[Path, Path, Path]:
        binary = tmp_path / f"gitleaks-{version}-{exit_code}"
        calls = tmp_path / f"calls-{version}-{exit_code}"
        report_record = tmp_path / f"report-{version}-{exit_code}"
        binary.write_text(
            "#!/usr/bin/env bash\n"
            "if [ \"${1:-}\" = version ]; then\n"
            f"  printf '%s\\n' '{version}'\n"
            "  exit 0\n"
            "fi\n"
            "for argument in \"$@\"; do\n"
            "  printf '%s\\n' \"$argument\" >> \"$FAKE_CALLS\"\n"
            "  case \"$argument\" in\n"
            "    --report-path=*)\n"
            "      path=${argument#--report-path=}\n"
            "      printf '%s\\n' \"$path\" > \"$FAKE_REPORT_RECORD\"\n"
            "      printf '{}\\n' > \"$path\"\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return binary, calls, report_record

    return make


def run_wrapper(
    repo: Path, binary: Path, calls: Path, report_record: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        GITLEAKS_BIN=str(binary),
        FAKE_CALLS=str(calls),
        FAKE_REPORT_RECORD=str(report_record),
    )
    return subprocess.run(
        [str(WRAPPER), str(repo)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def recorded_report(report_record: Path) -> Path:
    return Path(report_record.read_text(encoding="utf-8").strip())


def test_wrapper_rejects_wrong_version(fake_gitleaks, repo: Path):
    binary, calls, report_record = fake_gitleaks(version="8.30.0")

    result = run_wrapper(repo, binary, calls, report_record)

    assert result.returncode == 2
    assert "required Gitleaks version is 8.30.1" in result.stderr
    assert not calls.exists()
    assert not report_record.exists()


def test_wrapper_runs_redacted_git_history_scan(fake_gitleaks, repo: Path):
    binary, calls, report_record = fake_gitleaks(version="8.30.1")

    result = run_wrapper(repo, binary, calls, report_record)

    assert result.returncode == 0
    report = recorded_report(report_record)
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "git",
        "--no-banner",
        "--redact=100",
        "--exit-code=1",
        "--report-format=json",
        f"--report-path={report}",
        str(repo),
    ]
    assert not report.exists()


def test_wrapper_propagates_finding_exit_and_removes_report(
    fake_gitleaks, repo: Path
):
    binary, calls, report_record = fake_gitleaks(version="8.30.1", exit_code=1)

    result = run_wrapper(repo, binary, calls, report_record)

    assert result.returncode == 1
    assert calls.exists()
    assert not recorded_report(report_record).exists()


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="Gitleaks not installed")
def test_repository_history_passes_pinned_gitleaks():
    repository = Path(__file__).parents[1]

    result = subprocess.run(
        [str(WRAPPER), str(repository)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
