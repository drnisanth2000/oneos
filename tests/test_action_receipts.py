"""Committed action receipts: closed bytes and current-HEAD authority only."""
from __future__ import annotations

import ast
import builtins
import dataclasses
import os
from pathlib import Path
import subprocess

import pytest
import yaml

import app.action_receipts as action_receipts
from app.action_receipts import (
    ActionReceipt,
    InvalidActionReceipt,
    ReceiptResolution,
    ReceiptStoreIntegrityError,
    ReceiptStoreUnavailable,
    make_action_receipt,
    parse_action_receipt,
    receipt_relative_path,
    render_action_receipt,
    resolve_head_receipt,
    resolve_head_receipts,
    validate_all_head_receipt_stores,
    validate_head_receipt_store,
)
from app.review_tokens import InvalidReviewToken
from app.proposal_identity import ProposalIdentityError


ENTITY = "synthetic"
PROPOSAL_ID = "20260824T120000-" + "ab" * 16
OTHER_ID = "20260824T120001-" + "cd" * 16
DIGEST = "a" * 64


def _proposal_id(number: int) -> str:
    return f"20260824T120000-{number:032x}"


def _receipt_bytes(
    proposal_id: str = PROPOSAL_ID,
    digest: str = DIGEST,
    action_kind: str = "approval",
) -> bytes:
    return (
        "version: 1\n"
        f"proposal_id: {proposal_id}\n"
        f"review_sha256: {digest}\n"
        f"action_kind: {action_kind}\n"
    ).encode("ascii")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "baseline.txt")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def _commit_receipts(repo: Path, records: dict[str, bytes]) -> None:
    root = repo / ENTITY / "outbox" / ".receipts"
    root.mkdir(parents=True, exist_ok=True)
    for relative, contents in records.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    _git(repo, "add", "--", f"{ENTITY}/outbox/.receipts")
    _git(repo, "commit", "-q", "-m", "add receipts")


def _mktree(repo: Path, entries: bytes = b"") -> str:
    return subprocess.run(
        ["git", "mktree", "-z"],
        cwd=repo,
        check=True,
        input=entries,
        capture_output=True,
    ).stdout.decode("ascii").strip()


def _commit_empty_nested_receipt_tree(repo: Path) -> None:
    empty = _mktree(repo)
    receipts = _mktree(
        repo, f"040000 tree {empty}\tempty\0".encode("ascii")
    )
    outbox = _mktree(
        repo, f"040000 tree {receipts}\t.receipts\0".encode("ascii")
    )
    entity = _mktree(
        repo, f"040000 tree {outbox}\toutbox\0".encode("ascii")
    )
    root = _mktree(
        repo, f"040000 tree {entity}\t{ENTITY}\0".encode("ascii")
    )
    commit = _git(repo, "commit-tree", root, "-m", "nested receipt tree")
    _git(repo, "update-ref", "HEAD", commit.stdout.decode("ascii").strip())


# --- closed schema and deterministic bytes ----------------------------------


def test_action_receipt_round_trips_the_closed_schema():
    """Adding, removing, or renaming a receipt field must make this fail."""
    receipt = make_action_receipt(PROPOSAL_ID, DIGEST, "approval")
    raw = render_action_receipt(receipt)

    assert yaml.safe_load(raw) == {
        "version": 1,
        "proposal_id": PROPOSAL_ID,
        "review_sha256": DIGEST,
        "action_kind": "approval",
    }
    assert raw == _receipt_bytes()
    assert render_action_receipt(receipt) == raw
    assert parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw) == receipt


def test_action_receipt_value_is_frozen():
    receipt = make_action_receipt(PROPOSAL_ID, DIGEST, "approval")
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.action_kind = "registry deletion"


@pytest.mark.parametrize("action_kind", ["approval", "registry deletion"])
def test_both_closed_action_kinds_round_trip(action_kind):
    receipt = make_action_receipt(PROPOSAL_ID, DIGEST, action_kind)
    assert parse_action_receipt(
        Path(f"{PROPOSAL_ID}.yaml"), render_action_receipt(receipt)
    ) == receipt


@pytest.mark.parametrize(
    "extra", [{"created": "now"}, {"entity": "synthetic"}, {"target": "a/b"}]
)
def test_receipt_parser_refuses_fields_outside_the_closed_schema(extra):
    record = {
        "version": 1,
        "proposal_id": PROPOSAL_ID,
        "review_sha256": DIGEST,
        "action_kind": "approval",
        **extra,
    }
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(
            Path(f"{PROPOSAL_ID}.yaml"),
            yaml.safe_dump(record, sort_keys=False).encode("utf-8"),
        )


@pytest.mark.parametrize(
    "missing", ["version", "proposal_id", "review_sha256", "action_kind"]
)
def test_receipt_parser_refuses_every_missing_field(missing):
    record = {
        "version": 1,
        "proposal_id": PROPOSAL_ID,
        "review_sha256": DIGEST,
        "action_kind": "approval",
    }
    del record[missing]
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(
            Path(f"{PROPOSAL_ID}.yaml"),
            yaml.safe_dump(record, sort_keys=False).encode("utf-8"),
        )


@pytest.mark.parametrize("version", [0, 2, "1", True, None])
def test_receipt_parser_refuses_any_version_other_than_integer_one(version):
    record = yaml.safe_load(_receipt_bytes())
    record["version"] = version
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(
            Path(f"{PROPOSAL_ID}.yaml"),
            yaml.safe_dump(record, sort_keys=False).encode("utf-8"),
        )


@pytest.mark.parametrize(
    "record_id", [None, "", "../escape", "20260230T120000-" + "ab" * 16]
)
def test_receipt_parser_refuses_noncanonical_record_ids(record_id):
    record = yaml.safe_load(_receipt_bytes())
    record["proposal_id"] = record_id
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(
            Path(f"{PROPOSAL_ID}.yaml"),
            yaml.safe_dump(record, sort_keys=False).encode("utf-8"),
        )


@pytest.mark.parametrize(
    "digest", [None, "", "0" * 63, "A" * 64, "0" * 64 + "\n", b"0" * 64]
)
def test_receipt_parser_translates_invalid_stored_digests(digest):
    record = yaml.safe_load(_receipt_bytes())
    record["review_sha256"] = digest
    raw = yaml.safe_dump(record, sort_keys=False).encode("utf-8")
    with pytest.raises(InvalidActionReceipt) as raised:
        parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw)
    assert not isinstance(raised.value, InvalidReviewToken)


@pytest.mark.parametrize(
    "action_kind", [None, "", "reject", "registry_delete", True, 1]
)
def test_receipt_parser_refuses_action_kinds_outside_the_two_literals(action_kind):
    record = yaml.safe_load(_receipt_bytes())
    record["action_kind"] = action_kind
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(
            Path(f"{PROPOSAL_ID}.yaml"),
            yaml.safe_dump(record, sort_keys=False).encode("utf-8"),
        )


@pytest.mark.parametrize("raw", [b"[]\n", b"text\n", b"null\n", b"1\n"])
def test_receipt_parser_refuses_non_mapping_yaml(raw):
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw)


def test_receipt_parser_refuses_duplicate_fields():
    raw = _receipt_bytes() + b"action_kind: registry deletion\n"
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw)


def test_receipt_parser_refuses_non_utf8():
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), b"\xff\xfe")


def test_receipt_parser_binds_filename_to_content_id():
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(f"{OTHER_ID}.yaml"), _receipt_bytes())


def test_receipt_digest_is_audit_only_and_is_not_compared_to_receipt_bytes():
    """Comparing the digest to this or any pending record must make this fail."""
    raw = _receipt_bytes(digest="b" * 64)
    parsed = parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw)
    assert parsed.review_sha256 == "b" * 64


def test_receipt_relative_path_is_entity_local_and_id_bound():
    assert receipt_relative_path(ENTITY, PROPOSAL_ID) == (
        f"{ENTITY}/outbox/.receipts/{PROPOSAL_ID}.yaml"
    )


# --- current HEAD authority and per-id resolution ---------------------------


def test_one_receipt_resolution_exports_noncanonical_id_as_domain_failure(repo):
    with pytest.raises(ProposalIdentityError):
        resolve_head_receipt(repo, ENTITY, "not-a-proposal-id")


def test_worktree_deletion_does_not_hide_a_head_receipt(repo):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    (repo / receipt_relative_path(ENTITY, PROPOSAL_ID)).unlink()

    assert resolve_head_receipt(repo, ENTITY, PROPOSAL_ID) == ReceiptResolution(
        PROPOSAL_ID,
        ActionReceipt(1, PROPOSAL_ID, DIGEST, "approval"),
        None,
    )


def test_worktree_rewrite_does_not_replace_head_authority(repo):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    (repo / receipt_relative_path(ENTITY, PROPOSAL_ID)).write_bytes(
        _receipt_bytes(action_kind="registry deletion")
    )

    resolved = resolve_head_receipt(repo, ENTITY, PROPOSAL_ID)
    assert resolved.receipt is not None
    assert resolved.receipt.action_kind == "approval"


def test_absent_head_tree_is_a_valid_empty_store(repo):
    ids = (PROPOSAL_ID, OTHER_ID)
    assert resolve_head_receipts(repo, ENTITY, ids) == {
        proposal_id: ReceiptResolution(proposal_id, None, None)
        for proposal_id in ids
    }


def test_absent_matching_receipt_is_unspent_in_a_valid_store(repo):
    _commit_receipts(repo, {f"{OTHER_ID}.yaml": _receipt_bytes(OTHER_ID)})
    assert resolve_head_receipt(repo, ENTITY, PROPOSAL_ID) == ReceiptResolution(
        PROPOSAL_ID, None, None
    )


def test_non_tree_receipt_root_is_entity_wide_integrity_failure(repo):
    root = repo / ENTITY / "outbox" / ".receipts"
    root.parent.mkdir(parents=True)
    root.write_text("not a directory\n", encoding="utf-8")
    _git(repo, "add", "--", f"{ENTITY}/outbox/.receipts")
    _git(repo, "commit", "-q", "-m", "bad receipt root")

    with pytest.raises(ReceiptStoreIntegrityError):
        resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID, OTHER_ID])


def test_unreadable_head_is_entity_wide_unavailable(tmp_path):
    _git(tmp_path, "init", "-q")
    with pytest.raises(ReceiptStoreUnavailable):
        resolve_head_receipts(tmp_path, ENTITY, [PROPOSAL_ID])


def test_one_malformed_matching_receipt_does_not_disable_its_sibling(repo):
    _commit_receipts(
        repo,
        {
            f"{PROPOSAL_ID}.yaml": b"not: the closed schema\n",
            f"{OTHER_ID}.yaml": _receipt_bytes(OTHER_ID),
        },
    )

    resolved = resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID, OTHER_ID])
    assert resolved[PROPOSAL_ID].receipt is None
    assert isinstance(resolved[PROPOSAL_ID].error, InvalidActionReceipt)
    assert resolved[OTHER_ID] == ReceiptResolution(
        OTHER_ID,
        ActionReceipt(1, OTHER_ID, DIGEST, "approval"),
        None,
    )


def test_twenty_ids_use_one_root_lookup_and_one_batch_process(repo, monkeypatch):
    ids = tuple(_proposal_id(index) for index in range(20))
    _commit_receipts(repo, {f"{ids[0]}.yaml": _receipt_bytes(ids[0])})
    real_run = subprocess.run
    calls = []

    def recording_run(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(action_receipts.subprocess, "run", recording_run)
    resolved = resolve_head_receipts(repo, ENTITY, ids)

    assert len(resolved) == 20
    assert len(calls) == 2, "receipt lookup spawned more than two Git processes"
    assert calls[0][0][0][1:3] == ["ls-tree", "-z"]
    assert calls[1][0][0][1:3] == ["cat-file", "--batch"]
    assert calls[1][1]["input"].count(b"\n") == 20
    assert calls[0][1]["env"]["LC_ALL"] == "C"
    assert calls[1][1]["env"]["LC_ALL"] == "C"


def test_single_lookup_uses_the_same_batched_protocol(repo, monkeypatch):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    real_run = subprocess.run
    commands = []

    def recording_run(*args, **kwargs):
        commands.append(args[0])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(action_receipts.subprocess, "run", recording_run)
    assert resolve_head_receipt(repo, ENTITY, PROPOSAL_ID).receipt is not None
    assert [command[1] for command in commands] == ["ls-tree", "cat-file"]


@pytest.mark.parametrize(
    "root_output",
    [
        b"malformed\0",
        b"040000 tree " + b"a" * 40 + b"\twrong/path\0",
        (
            b"040000 tree " + b"a" * 40 + b"\t" +
            f"{ENTITY}/outbox/.receipts".encode() + b"\0" +
            b"040000 tree " + b"b" * 40 + b"\t" +
            f"{ENTITY}/outbox/.receipts".encode() + b"\0"
        ),
    ],
)
def test_malformed_root_protocol_is_unavailable(repo, monkeypatch, root_output):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, root_output, b"")

    monkeypatch.setattr(action_receipts.subprocess, "run", fake_run)
    with pytest.raises(ReceiptStoreUnavailable):
        resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID])


@pytest.mark.parametrize(
    "batch_output",
    [
        b"not-a-header\n",
        b"a" * 40 + b" blob nope\n",
        b"a" * 40 + b" blob 4\nabc\n",
        b"a" * 40 + b" blob 3\nabcX",
        b"unexpected missing\n",
        b"\xff blob 3\nabc\n",
    ],
)
def test_malformed_batch_protocol_is_unavailable(repo, monkeypatch, batch_output):
    root = f"{ENTITY}/outbox/.receipts".encode()
    root_output = b"040000 tree " + b"a" * 40 + b"\t" + root + b"\0"
    outputs = iter((root_output, batch_output))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, next(outputs), b"")

    monkeypatch.setattr(action_receipts.subprocess, "run", fake_run)
    with pytest.raises(ReceiptStoreUnavailable):
        resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID])


def test_non_blob_matching_object_is_a_per_id_invalid_receipt(repo, monkeypatch):
    root = f"{ENTITY}/outbox/.receipts".encode()
    root_output = b"040000 tree " + b"a" * 40 + b"\t" + root + b"\0"
    batch_output = b"b" * 40 + b" tree 0\n\n"
    outputs = iter((root_output, batch_output))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, next(outputs), b"")

    monkeypatch.setattr(action_receipts.subprocess, "run", fake_run)
    result = resolve_head_receipt(repo, ENTITY, PROPOSAL_ID)
    assert result.receipt is None
    assert isinstance(result.error, InvalidActionReceipt)


def test_git_command_failure_is_store_unavailable(repo, monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, b"", b"failure")

    monkeypatch.setattr(action_receipts.subprocess, "run", fake_run)
    with pytest.raises(ReceiptStoreUnavailable):
        resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID])


# --- offline complete-store validation --------------------------------------


def test_offline_validator_returns_every_receipt_sorted_by_id(repo):
    _commit_receipts(
        repo,
        {
            f"{OTHER_ID}.yaml": _receipt_bytes(OTHER_ID, action_kind="registry deletion"),
            f"{PROPOSAL_ID}.yaml": _receipt_bytes(PROPOSAL_ID),
        },
    )
    receipts = validate_head_receipt_store(repo, ENTITY)
    assert [receipt.proposal_id for receipt in receipts] == [PROPOSAL_ID, OTHER_ID]
    assert [receipt.action_kind for receipt in receipts] == [
        "approval",
        "registry deletion",
    ]


def test_complete_offline_validator_discovers_stores_from_head_not_manifest(repo):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    manifest = repo / "_system/entities.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("entities: {}\n", encoding="utf-8")

    assert validate_all_head_receipt_stores(repo) == (
        ActionReceipt(1, PROPOSAL_ID, DIGEST, "approval"),
    )


def test_complete_offline_validator_rejects_duplicate_ids_across_entities(repo):
    first = receipt_relative_path(ENTITY, PROPOSAL_ID)
    second = receipt_relative_path("secondary", PROPOSAL_ID)
    for relative in (first, second):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_receipt_bytes())
    _git(repo, "add", "--", first, second)
    _git(repo, "commit", "-q", "-m", "duplicate cross-entity receipt")

    with pytest.raises(ReceiptStoreIntegrityError, match="more than one entity"):
        validate_all_head_receipt_stores(repo)


def test_offline_validator_accepts_the_forced_empty_store(repo):
    assert validate_head_receipt_store(repo, ENTITY) == ()


@pytest.mark.parametrize("bad_leaf", ["note.txt", "nested/receipt.yaml"])
def test_offline_validator_rejects_non_receipt_store_entries(repo, bad_leaf):
    _commit_receipts(repo, {bad_leaf: _receipt_bytes()})
    with pytest.raises(ReceiptStoreIntegrityError):
        validate_head_receipt_store(repo, ENTITY)


def test_offline_validator_refuses_an_empty_nested_tree(repo):
    """Restoring recursive-only enumeration must make this test fail."""
    _commit_empty_nested_receipt_tree(repo)
    with pytest.raises(ReceiptStoreIntegrityError):
        validate_head_receipt_store(repo, ENTITY)


def test_offline_validator_rejects_any_malformed_historical_receipt(repo):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": b"not: closed\n"})
    with pytest.raises(InvalidActionReceipt):
        validate_head_receipt_store(repo, ENTITY)


@pytest.mark.parametrize(
    ("case", "filename", "record"),
    [
        ("invalid-yaml", f"{PROPOSAL_ID}.yaml", b"[unterminated\n"),
        (
            "extra-field",
            f"{PROPOSAL_ID}.yaml",
            _receipt_bytes() + b"created: now\n",
        ),
        (
            "missing-field",
            f"{PROPOSAL_ID}.yaml",
            b"version: 1\n"
            + f"proposal_id: {PROPOSAL_ID}\n".encode("ascii")
            + b"review_sha256: " + DIGEST.encode("ascii") + b"\n",
        ),
        (
            "version-mismatch",
            f"{PROPOSAL_ID}.yaml",
            _receipt_bytes().replace(b"version: 1", b"version: 2"),
        ),
        (
            "filename-content-mismatch",
            f"{OTHER_ID}.yaml",
            _receipt_bytes(PROPOSAL_ID),
        ),
        (
            "invalid-digest",
            f"{PROPOSAL_ID}.yaml",
            _receipt_bytes().replace(DIGEST.encode("ascii"), b"short"),
        ),
        (
            "invalid-action-kind",
            f"{PROPOSAL_ID}.yaml",
            _receipt_bytes(action_kind="reject"),
        ),
    ],
)
def test_offline_validator_rejects_every_historical_record_defect(
    repo, case, filename, record
):
    _commit_receipts(repo, {filename: record})

    with pytest.raises(InvalidActionReceipt, match="action receipt|receipt"):
        validate_head_receipt_store(repo, ENTITY)


def test_offline_validator_rejects_duplicate_receipt_ids(repo, monkeypatch):
    root = f"{ENTITY}/outbox/.receipts"
    object_id = "a" * 40
    root_output = (
        f"040000 tree {object_id}\t{root}\0".encode("ascii")
    )
    path = f"{root}/{PROPOSAL_ID}.yaml"
    duplicate_entries = (
        f"100644 blob {object_id}\t{path}\0"
        f"100644 blob {object_id}\t{path}\0"
    ).encode("ascii")
    outputs = iter((root_output, duplicate_entries))

    def fake_git(*_args, **_kwargs):
        return next(outputs)

    monkeypatch.setattr(action_receipts, "_git", fake_git)

    with pytest.raises(ReceiptStoreUnavailable, match="duplicate receipt path"):
        validate_head_receipt_store(repo, ENTITY)


def test_offline_validator_wrong_object_root_is_not_the_empty_store(repo):
    assert validate_head_receipt_store(repo, ENTITY) == ()
    root = repo / ENTITY / "outbox" / ".receipts"
    root.parent.mkdir(parents=True)
    root.write_text("blob, not tree\n", encoding="utf-8")
    _git(repo, "add", "--", f"{ENTITY}/outbox/.receipts")
    _git(repo, "commit", "-q", "-m", "wrong receipt root object")

    with pytest.raises(ReceiptStoreIntegrityError, match="not a Git tree"):
        validate_head_receipt_store(repo, ENTITY)


def test_offline_validator_reads_head_and_never_changes_the_filesystem(repo):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    path = repo / receipt_relative_path(ENTITY, PROPOSAL_ID)
    path.write_bytes(b"working tree replacement\n")
    status_before = _git(repo, "status", "--porcelain=v1", "-z").stdout
    bytes_before = path.read_bytes()

    receipts = validate_head_receipt_store(repo, ENTITY)

    assert receipts == (ActionReceipt(1, PROPOSAL_ID, DIGEST, "approval"),)
    assert _git(repo, "status", "--porcelain=v1", "-z").stdout == status_before
    assert path.read_bytes() == bytes_before


def test_offline_validator_has_no_filesystem_write_or_repair_path(
    repo, monkeypatch
):
    _commit_receipts(repo, {f"{PROPOSAL_ID}.yaml": _receipt_bytes()})
    real_open = builtins.open

    def checked_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            pytest.fail(f"offline receipt audit opened a writer: {mode}")
        return real_open(file, mode, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("offline receipt audit attempted filesystem mutation")

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    monkeypatch.setattr(os, "rename", forbidden)
    monkeypatch.setattr(os, "mkdir", forbidden)

    assert validate_head_receipt_store(repo, ENTITY) == (
        ActionReceipt(1, PROPOSAL_ID, DIGEST, "approval"),
    )


def test_offline_validator_duplicate_membership_is_constant_time():
    """Replacing the seen-path set with ordered-list membership must fail."""
    source = Path(action_receipts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    reader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_head_receipt_entries"
    )
    set_names = set()
    for node in ast.walk(reader):
        value = getattr(node, "value", None)
        if not (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "set"
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        set_names.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )
    membership_targets = {
        comparator.id
        for node in ast.walk(reader)
        if isinstance(node, ast.Compare)
        and any(isinstance(operator, ast.In) for operator in node.ops)
        for comparator in node.comparators
        if isinstance(comparator, ast.Name)
    }
    assert set_names & membership_targets, (
        "full-store duplicate detection does not use constant-time set membership"
    )
