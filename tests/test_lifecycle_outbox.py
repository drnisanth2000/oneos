"""Synthetic lifecycle actions through the existing human review surface."""
import importlib
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.scope import Scope
from tests.test_lifecycle_repair import lifecycle_vault, git


@pytest.fixture
def pending(lifecycle_vault):
    from app.lifecycle_repair import propose_repair
    scope = Scope(lifecycle_vault, 'sample')
    return scope, propose_repair(scope, actor='owner')


def client_for(scope, monkeypatch):
    monkeypatch.setenv('ONEOS_VAULT', str(scope.root))
    import app.main as main
    importlib.reload(main)
    return main, TestClient(main.app)


def test_project_lifecycle_exact_bytes_without_classification_fields(pending):
    from app.outbox import project_outbox
    from app.lifecycle_receipts import render_lifecycle_receipt
    scope, review = pending
    listing = project_outbox(scope)
    assert not listing.blocked
    row, = listing.rows
    assert row.can_approve and row.can_reject
    assert row.review_sha256 == review.sha256
    assert row.proposal.action == 'lifecycle_repair'
    assert not hasattr(row.proposal, 'src')
    assert 'sample/01-work/active/.gitkeep' in row.diff
    assert 'conventions:v2:lifecycle' in row.diff
    assert render_lifecycle_receipt(review.contents).decode().strip() in row.diff


def test_lifecycle_card_shows_paths_and_review_controls(pending, monkeypatch):
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    response = client.get('/outbox/sample')
    assert response.status_code == 200
    assert 'sample/01-work/active' in response.text
    assert 'Approve repair' in response.text
    assert review.sha256 in response.text
    assert 'Approve move' not in response.text


def test_lifecycle_approve_without_journal_visibly_refuses(pending, monkeypatch):
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    before = git(scope.root, 'rev-parse', 'HEAD')
    response = client.post('/outbox/sample/approve', data={'id': review.value['id'], 'review_sha256': review.sha256})
    assert response.status_code < 500
    assert 'E-' in response.text
    assert git(scope.root, 'rev-parse', 'HEAD') == before
    assert not (scope.root / 'sample/01-work/active').exists()


def test_lifecycle_reject_quarantines_exact_review_without_commit(pending, monkeypatch):
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    before = git(scope.root, 'rev-parse', 'HEAD')
    response = client.post('/outbox/sample/reject', data={'id': review.value['id'], 'review_sha256': review.sha256})
    assert response.status_code == 200
    assert git(scope.root, 'rev-parse', 'HEAD') == before
    assert not (scope.root / 'sample/outbox' / (review.value['id'] + '.yaml')).exists()
    assert not (scope.root / 'sample/01-work/active').exists()
    assert any(p.is_file() and p.read_bytes() == review.contents for p in scope.root.rglob('.consumed/*.yaml'))


def test_lifecycle_approval_with_explicit_journal_creates_retained_receipt(pending, monkeypatch, tmp_path):
    from app.lifecycle_journal import LifecycleJournal
    from app.action_receipts import resolve_head_receipt
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    (tmp_path / 'evidence').mkdir()
    main.app.state.lifecycle_journal = LifecycleJournal.create(tmp_path / 'evidence', scope.root)
    response = client.post('/outbox/sample/approve', data={'id': review.value['id'], 'review_sha256': review.sha256})
    assert response.status_code == 200
    assert (scope.root / 'sample/01-work/active/.gitkeep').read_bytes() == b''
    assert resolve_head_receipt(scope.root, 'sample', review.value['id']).receipt.version == 2


def test_changed_lifecycle_review_shows_own_fields_and_does_not_reject(pending, monkeypatch):
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    path = scope.root / 'sample/outbox' / (review.value['id'] + '.yaml')
    proposal = dict(review.value, baseline_head='b' * 40)
    path.write_text(json.dumps(proposal))
    response = client.post('/outbox/sample/reject', data={
        'id': review.value['id'], 'review_sha256': review.sha256,
        'review_issue': 'abc123', 'reviewed_values': json.dumps({'baseline_head': review.value['baseline_head']})})
    assert response.status_code < 500
    assert 'E-REVIEW' in response.text
    assert 'Lifecycle repair' in response.text
    assert path.exists()


def test_lifecycle_preview_diff_accepts_typed_proposal(pending):
    from app.outbox import get_proposal, preview_diff
    scope, review = pending
    assert 'sample/01-work/active/.gitkeep' in preview_diff(scope, get_proposal(scope, review.value['id']))


def test_lifecycle_snapshot_parses_only_captured_bytes(pending, monkeypatch):
    import app.outbox as outbox
    scope, review = pending
    path = scope.root / 'sample/outbox' / (review.value['id'] + '.yaml')
    capture = outbox._capture_proposal_contents
    def replace_after_capture(scope, leaf):
        contents = capture(scope, leaf)
        leaf.write_text('invalid after capture')
        return contents
    monkeypatch.setattr(outbox, '_capture_proposal_contents', replace_after_capture)
    snapshot = outbox.review_snapshot_for(scope, path)
    assert snapshot.sha256 == review.sha256
    assert snapshot.value.manifest == review.value['manifest']


def test_committed_lifecycle_receipt_preempts_recreated_malformed_record(pending, monkeypatch, tmp_path):
    from app.lifecycle_journal import LifecycleJournal
    from app.outbox import project_outbox
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    (tmp_path / 'evidence').mkdir()
    main.app.state.lifecycle_journal = LifecycleJournal.create(tmp_path / 'evidence', scope.root)
    data = {'id': review.value['id'], 'review_sha256': review.sha256}
    assert client.post('/outbox/sample/approve', data=data).status_code == 200
    (scope.root / 'sample/outbox' / (review.value['id'] + '.yaml')).write_text('invalid YAML: [')
    before = git(scope.root, 'rev-parse', 'HEAD')
    listing = project_outbox(scope)
    assert not listing.blocked
    row, = listing.rows
    assert row.receipt.version == 2 and not row.can_approve and not row.can_reject
    response = client.post('/outbox/sample/approve', data=data)
    assert response.status_code == 200
    assert git(scope.root, 'rev-parse', 'HEAD') == before


def test_lifecycle_postcommit_failure_remains_visibly_applied(pending, monkeypatch, tmp_path):
    from app.lifecycle_journal import LifecycleJournal
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    (tmp_path / 'evidence').mkdir()
    journal = LifecycleJournal.create(tmp_path / 'evidence', scope.root)
    import app.git_transaction as transaction
    def fail_after_quarantine(*args, **kwargs):
        raise transaction.ReviewedPathUnavailable('synthetic consumption verification failure')
    monkeypatch.setattr(transaction, '_require_quarantined_record_unchanged', fail_after_quarantine)
    main.app.state.lifecycle_journal = journal
    response = client.post('/outbox/sample/approve', data={'id': review.value['id'], 'review_sha256': review.sha256})
    assert response.status_code == 500
    assert 'E-APPLIED' in response.text
    assert (scope.root / 'sample/01-work/active/.gitkeep').read_bytes() == b''


def test_existing_outbox_renders_and_approves_rollback(pending, monkeypatch, tmp_path):
    from app.lifecycle_journal import LifecycleJournal
    from app.lifecycle_repair import propose_rollback
    from app.action_receipts import validate_head_receipt_store
    scope, review = pending
    main, client = client_for(scope, monkeypatch)
    (tmp_path / 'evidence').mkdir()
    main.app.state.lifecycle_journal = LifecycleJournal.create(tmp_path / 'evidence', scope.root)
    assert client.post('/outbox/sample/approve', data={
        'id': review.value['id'], 'review_sha256': review.sha256}).status_code == 200
    repair_commit = git(scope.root, 'rev-parse', 'HEAD')
    rollback = propose_rollback(scope, repair_commit, actor='owner', journal=main.app.state.lifecycle_journal)
    response = client.get('/outbox/sample')
    assert response.status_code == 200 and 'Approve rollback' in response.text
    assert 'delete file: sample/01-work/active/.gitkeep' in response.text
    response = client.post('/outbox/sample/approve', data={
        'id': rollback.value['id'], 'review_sha256': rollback.sha256})
    assert response.status_code == 200
    assert not (scope.root / 'sample/01-work/active').exists()
    assert {r.action_kind for r in validate_head_receipt_store(scope.root, 'sample')} == {
        'lifecycle_repair', 'lifecycle_rollback'}
