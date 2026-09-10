"""Synthetic historical authority and intermediate lifecycle evidence."""
import importlib
from pathlib import Path
import pytest
from tests.test_lifecycle_repair import lifecycle_vault, git
from app.action_receipts import InvalidActionReceipt
from app.scope import Scope
from app.lifecycle_repair import propose_repair, approve_lifecycle, propose_rollback


class NoJournal:
    def checkpoint(self, *args, **kwargs):
        pass


def history():
    try:
        return importlib.import_module('app.lifecycle_history')
    except ModuleNotFoundError:
        pytest.fail('historical lifecycle validator is missing')


def repair(vault):
    scope = Scope(vault, 'sample')
    review = propose_repair(scope, actor='owner')
    return approve_lifecycle(scope, review.value['id'], review.sha256, actor='owner', journal=NoJournal())


def test_exact_repair_is_historical_authority(lifecycle_vault):
    result = repair(lifecycle_vault)
    receipt, path, blob = history().validated_commit(lifecycle_vault, result.commit_oid)
    assert receipt.action_kind == 'lifecycle_repair'
    assert git(lifecycle_vault, 'rev-parse', result.commit_oid + ':' + path) == blob


def test_governed_inverse_retains_both_receipts(lifecycle_vault):
    v = lifecycle_vault
    from app.lifecycle_journal import LifecycleJournal
    store = v.parent / 'evidence'; store.mkdir()
    journal = LifecycleJournal.create(store, v)
    scope = Scope(v, 'sample')
    r = propose_repair(scope, actor='owner')
    result = approve_lifecycle(scope, r.value['id'], r.sha256, actor='owner', journal=journal)
    review = propose_rollback(scope, result.commit_oid, actor='owner', journal=journal)
    inverse = approve_lifecycle(scope, review.value['id'], review.sha256, actor='owner', journal=journal)
    assert history().validated_commit(v, inverse.commit_oid)[0].action_kind == 'lifecycle_rollback'
    with pytest.raises((ValueError, InvalidActionReceipt)):
        history().require_unreverted(v, result.commit_oid, inverse.commit_oid)
    assert len(git(v, 'ls-tree', '-r', '--name-only', 'HEAD', 'sample/outbox/.receipts').splitlines()) == 2


@pytest.mark.parametrize('change', ['extra', 'placeholder', 'receipt'])
def test_forged_commit_is_refused(lifecycle_vault, change):
    v = lifecycle_vault
    result = repair(v)
    if change == 'extra':
        (v / 'extra').write_text('unauthorized')
    elif change == 'placeholder':
        (v / 'sample/01-work/active/.gitkeep').write_text('unauthorized')
    else:
        p = next((v / 'sample/outbox/.receipts').glob('*.yaml'))
        p.write_bytes(p.read_bytes().replace(b'"version":2', b'"version":3'))
    git(v, 'add', '-A'); git(v, 'commit', '--amend', '--no-edit', '-q')
    with pytest.raises((ValueError, InvalidActionReceipt)):
        history().validated_commit(v, git(v, 'rev-parse', 'HEAD'))


def session(v):
    from app.lifecycle_journal import LifecycleJournal
    store = v.parent / 'journal'; store.mkdir()
    journal = LifecycleJournal.create(store, v)
    scope = Scope(v, 'sample')
    r = propose_repair(scope, actor='owner')
    first = approve_lifecycle(scope, r.value['id'], r.sha256, actor='owner', journal=journal)
    r = propose_rollback(scope, first.commit_oid, actor='owner', journal=journal)
    approve_lifecycle(scope, r.value['id'], r.sha256, actor='owner', journal=journal)
    return journal


def test_complete_journal_has_two_sanctioned_commits(lifecycle_vault):
    from tools import gate3_audit as gate
    journal = session(lifecycle_vault)
    assert hasattr(gate, 'audit_lifecycle_session'), 'ordered lifecycle audit is missing'
    result = gate.audit_lifecycle_session(lifecycle_vault, journal.session_path, journal.anchor)
    assert result.ok, result
    assert len(result.sanctioned_commits) == 2


@pytest.mark.parametrize('damage', ['missing', 'reordered', 'tampered', 'receipt', 'extra'])
def test_incomplete_or_changed_session_refuses(lifecycle_vault, damage):
    from tools import gate3_audit as gate
    journal = session(lifecycle_vault)
    files = sorted(journal.session_path.glob('*.json'))
    if damage == 'missing': files[-1].unlink()
    elif damage == 'reordered':
        first, second = files[1].read_bytes(), files[2].read_bytes()
        files[1].write_bytes(second); files[2].write_bytes(first)
    elif damage == 'tampered': files[-1].write_bytes(files[-1].read_bytes() + b' ')
    elif damage == 'receipt': next((lifecycle_vault / 'sample/outbox/.receipts').glob('*.yaml')).unlink()
    else: (lifecycle_vault / 'extra').write_text('unaccounted')
    assert not gate.audit_lifecycle_session(lifecycle_vault, journal.session_path, journal.anchor).ok


def test_snapshot_only_cli_refuses_lifecycle_certification(lifecycle_vault, monkeypatch, tmp_path):
    from tools import gate3_audit as gate
    import json
    snapshot = tmp_path / 'baseline.json'
    snapshot.write_text(json.dumps(gate._snapshot_payload(lifecycle_vault)))
    journal = session(lifecycle_vault)
    monkeypatch.setattr(gate, '_vault', lambda: lifecycle_vault)
    monkeypatch.setattr(gate, '_snapshot_path', lambda vault: snapshot)
    monkeypatch.delenv('ONEOS_LIFECYCLE_JOURNAL', raising=False)
    monkeypatch.delenv('ONEOS_LIFECYCLE_ANCHOR', raising=False)
    assert gate.cmd_check() == 1
    monkeypatch.setenv('ONEOS_LIFECYCLE_JOURNAL', str(journal.session_path))
    monkeypatch.setenv('ONEOS_LIFECYCLE_ANCHOR', journal.anchor)
    assert gate.cmd_check() == 0


def test_commit_candidate_message_never_grants_authority(lifecycle_vault):
    from tools import gate3_audit as gate
    v = lifecycle_vault
    baseline = git(v, 'rev-parse', 'HEAD')
    repaired = repair(v)
    records = gate.collect_commit_records(v, baseline, repaired.commit_oid)
    assert gate.audit_commits(records, gate.AuditRules.load(v), v).ok
    (v / 'rogue').write_text('extra')
    git(v, 'add', '-A'); git(v, 'commit', '--amend', '--no-edit', '-q')
    records = gate.collect_commit_records(v, baseline, git(v, 'rev-parse', 'HEAD'))
    assert not gate.audit_commits(records, gate.AuditRules.load(v), v).ok


def test_git_invisible_extra_directory_is_rejected_between_checkpoints(lifecycle_vault):
    from app.lifecycle_journal import LifecycleJournal
    from tools import gate3_audit as gate
    v = lifecycle_vault
    store = v.parent / 'journal'; store.mkdir()
    journal = LifecycleJournal.create(store, v)
    scope = Scope(v, 'sample')
    r = propose_repair(scope, actor='owner')
    first = approve_lifecycle(scope, r.value['id'], r.sha256, actor='owner', journal=journal)
    (v / 'unaccounted-empty-directory').mkdir()
    r = propose_rollback(scope, first.commit_oid, actor='owner', journal=journal)
    approve_lifecycle(scope, r.value['id'], r.sha256, actor='owner', journal=journal)
    assert not gate.audit_lifecycle_session(v, journal.session_path, journal.anchor).ok


def test_cli_journal_cannot_omit_changes_after_snapshot(lifecycle_vault, monkeypatch, tmp_path):
    from tools import gate3_audit as gate
    import json
    snapshot = tmp_path / 'baseline.json'
    snapshot.write_text(json.dumps(gate._snapshot_payload(lifecycle_vault)))
    (lifecycle_vault / 'unaccounted-before-journal').mkdir()
    journal = session(lifecycle_vault)
    monkeypatch.setattr(gate, '_vault', lambda: lifecycle_vault)
    monkeypatch.setattr(gate, '_snapshot_path', lambda vault: snapshot)
    monkeypatch.setenv('ONEOS_LIFECYCLE_JOURNAL', str(journal.session_path))
    monkeypatch.setenv('ONEOS_LIFECYCLE_ANCHOR', journal.anchor)
    assert gate.cmd_check() == 1


def test_historical_shape_preserves_existing_regular_content_modes(lifecycle_vault):
    v = lifecycle_vault
    (v / 'sample/01-work/status.md').chmod(0o755)
    git(v, 'add', '-A'); git(v, 'commit', '-qm', 'existing executable mode')
    result = repair(v)
    assert history().validated_commit(v, result.commit_oid)[0].action_kind == 'lifecycle_repair'
