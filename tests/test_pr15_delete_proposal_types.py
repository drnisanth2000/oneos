"""PR #15 must-fix 5: delete-proposal field-type validation.

`DeleteProposal` is a plain `@dataclass` with no runtime enforcement of its
field annotations, so a persisted record with `kind: []` (or another
non-string `entity`/`kind`/`slug`) passed `get_delete_proposal` unchallenged
and later made `_REGISTRY_FILE.get(prop.kind)` raise a raw `TypeError` (an
unhashable dict key) inside `execute_delete`, escaping as `E-UNKNOWN` instead
of `E-UNREADABLE`.

`entity`, `kind`, and `slug` are validated as strings before `DeleteProposal`
is constructed, checked after the existing action-type check (so a record of
the wrong action keeps its own, more specific message) and before the
existing entity-ownership check (so a malformed entity gets this message
first). `id` is not re-checked: `require_proposal_identity` already
guarantees it is a canonical string before any of this runs — pinned here as
a control, not re-implemented. `path` stays the server-derived `Path` and is
never validated as persisted data, since it never comes from the record.
"""
from __future__ import annotations

import pytest

from tests.conftest import write_vault

ENTITIES = 'version: "1.0"\nentities:\n  demo: {label: Demo, flags: []}\n'


def _scope(tmp_path):
    from app.scope import Scope

    write_vault(tmp_path, ENTITIES)
    (tmp_path / "demo").mkdir(exist_ok=True)
    return Scope(tmp_path, "demo")


def _write_delete_proposal(tmp_path, proposal_id: str, body: str):
    outbox = tmp_path / "demo" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"{proposal_id}.yaml").write_text(body, encoding="utf-8")


def _base_record(proposal_id: str) -> dict:
    return {
        "id": proposal_id,
        "action": "delete",
        "entity": "demo",
        "kind": "product",
        "slug": "widget",
    }


def _dump(record: dict) -> str:
    import yaml

    return yaml.safe_dump(record, sort_keys=False)


@pytest.mark.parametrize(
    "label, field_name, bad_value",
    [
        ("kind is a list", "kind", []),
        ("kind is a mapping", "kind", {}),
        ("kind is a number", "kind", 5),
        ("slug is a list", "slug", []),
        ("entity is a list", "entity", []),
        ("entity is a number", "entity", 5),
    ],
)
def test_malformed_field_type_becomes_unreadable(tmp_path, label, field_name, bad_value):
    from app.console_errors import describe
    from app.outbox import UnreadableProposalRecord
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "ab" * 16
    record = _base_record(proposal_id)
    record[field_name] = bad_value
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    with pytest.raises(UnreadableProposalRecord) as raised:
        get_delete_proposal(scope, proposal_id)
    assert describe(raised.value).code == "E-UNREADABLE", label


def test_kind_as_unhashable_list_no_longer_raises_typeerror_in_execute_delete(tmp_path):
    """The exact reproduction from the CodeRabbit finding: `kind: []` must
    not let `get_delete_proposal` succeed only to crash `execute_delete`
    with a raw `TypeError` from `_REGISTRY_FILE.get(prop.kind)` (an
    unhashable dict key). An empty entity root (no front matter, no
    books.db, no workspaces registry) means `reference_count` returns all
    zeros regardless of `kind`'s shape, so this isolates the exact crash
    site the finding names."""
    from app.outbox import UnreadableProposalRecord
    from app.registry import execute_delete

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "cd" * 16
    record = _base_record(proposal_id)
    record["kind"] = []
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    with pytest.raises(UnreadableProposalRecord):
        execute_delete(scope, proposal_id)


def test_wrong_action_type_keeps_its_own_message(tmp_path):
    """Control: the pre-existing action-type check still wins over the new
    field-type check for a record that is not a delete proposal at all —
    the new check must not shadow it, even though such a record typically
    lacks a `kind`/`slug` field altogether."""
    from app.registry import RegistryError, get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "ef" * 16
    record = {"id": proposal_id, "action": "classify", "entity": "demo"}
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    with pytest.raises(RegistryError, match="is not a delete proposal"):
        get_delete_proposal(scope, proposal_id)


def test_non_string_id_keeps_its_own_pre_existing_message(tmp_path):
    """Control: `id` is not re-validated by must-fix 5's new check — it is
    already guaranteed to be a canonical string by the pre-existing
    `require_proposal_identity` call, which raises `RegistryError`
    ('invalid delete proposal id'), not `UnreadableProposalRecord`. This
    pins that must-fix 5 does not duplicate or change that pre-existing,
    already-correct conversion.

    I1 (S6 review): the previous version of this test never called
    `get_delete_proposal` at all — it wrapped `require_proposal_id(5)`
    directly in `pytest.raises(Exception)`, so it passed unchanged whether
    or not `id` was ever added to must-fix 5's new isinstance loop, whether
    or not that whole loop existed, and regardless of which exception type
    or message `get_delete_proposal` itself actually raises for a
    non-string `id`. This version drives the real function with a record
    whose `id` is a non-string and pins the actual type AND message."""
    from app.registry import RegistryError, get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "ee" * 16
    record = _base_record(proposal_id)
    record["id"] = 5
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    with pytest.raises(RegistryError, match="invalid delete proposal id"):
        get_delete_proposal(scope, proposal_id)


def test_well_formed_delete_proposal_still_constructs(tmp_path):
    """Control: well-formed string fields are untouched by the new check."""
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "01" * 16
    record = _base_record(proposal_id)
    record["total_references"] = 0
    record["impact"] = {}
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    prop = get_delete_proposal(scope, proposal_id)
    assert prop.kind == "product"
    assert prop.slug == "widget"
    assert prop.entity == "demo"
    assert prop.id == proposal_id


def test_path_field_is_never_read_from_the_record(tmp_path):
    """`path` is server-derived (the proposal's own filesystem location), not
    persisted data — it must come from `_delete_proposal_path`, not be
    validated as if it were a record field.

    I1 (S6 review): the previous version of this record carried no `path:`
    key at all, so `DeleteProposal(..., rec.get("path", path), ...)` — take
    `path` FROM the record when present — would have returned the exact
    same, server-derived value via its fallback and passed identically.
    Measured: that mutation left the whole file green (11 passed). A hostile
    `path:` key in the record is required to distinguish "server-derived"
    from "record-supplied, falling back to server-derived" at all."""
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "02" * 16
    record = _base_record(proposal_id)
    record["path"] = "/etc/passwd"
    _write_delete_proposal(tmp_path, proposal_id, _dump(record))

    prop = get_delete_proposal(scope, proposal_id)
    expected = scope.resolve("outbox") / f"{proposal_id}.yaml"
    assert prop.path == expected
    assert str(prop.path) != "/etc/passwd"
