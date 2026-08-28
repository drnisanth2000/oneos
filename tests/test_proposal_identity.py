from datetime import datetime
from pathlib import Path

import pytest

import app.proposal_identity as identity


def test_id_combines_readable_timestamp_with_128_bit_random_suffix(monkeypatch):
    monkeypatch.setattr(identity.secrets, "token_hex", lambda size: "ab" * size)

    proposal_id = identity.generate_proposal_id(datetime(2026, 8, 15, 9, 7, 3))

    assert proposal_id == "20260815T090703-" + "ab" * 16


@pytest.mark.parametrize(
    "value",
    [
        None,
        7,
        "20260815T090703-delete",
        "20260815T090703-" + "AB" * 16,
        "20261315T090703-" + "ab" * 16,
        "20260815T250703-" + "ab" * 16,
        "20260815T090703-" + "ab" * 15,
        "../20260815T090703-" + "ab" * 16,
    ],
)
def test_id_validation_rejects_noncanonical_values(value):
    with pytest.raises(identity.ProposalIdentityError):
        identity.require_proposal_id(value)


def test_record_id_must_equal_yaml_filename_stem():
    proposal_id = "20260815T090703-" + "ab" * 16
    with pytest.raises(identity.ProposalIdentityError):
        identity.require_proposal_identity(
            Path("/vault/demo1/outbox/20260815T090703-" + "cd" * 16 + ".yaml"),
            proposal_id,
        )
