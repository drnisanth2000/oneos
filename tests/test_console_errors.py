import pytest
from app.console_errors import ConsoleError


def test_refusal_cannot_report_a_commit():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "retry", "yes", 422)


def test_committed_tier_must_stop_and_report_yes():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "retry", "yes", 500)
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "stop", "no", 500)


def test_recovery_tier_must_stop_and_report_unknown():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "recovery", "attention", "m", "stop", "no", 500)


def test_page_status_must_be_a_known_http_status():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 299)


def test_console_error_is_frozen():
    e = ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 422)
    with pytest.raises(Exception):
        e.code = "E-Y"
