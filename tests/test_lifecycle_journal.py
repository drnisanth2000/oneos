"""Synthetic-only journal evidence, integrity, and preservation regressions."""
import importlib
import json
import os
from pathlib import Path
import subprocess

import pytest


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


@pytest.fixture
def journal_api():
    assert importlib.util.find_spec('app.lifecycle_journal') is not None, 'journal support is absent'
    return importlib.import_module('app.lifecycle_journal')


@pytest.fixture
def roots(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    git(vault, 'init', '-q')
    git(vault, 'config', 'user.email', 'fixture@example.invalid')
    git(vault, 'config', 'user.name', 'Fixture')
    (vault / 'note').write_text('original')
    git(vault, 'add', 'note')
    git(vault, 'commit', '-qm', 'initial')
    store = tmp_path / 'evidence'
    store.mkdir()
    return vault, store


def test_roundtrip_captures_complete_evidence_without_index_mutation(journal_api, roots):
    vault, store = roots
    (vault / 'note').write_text('staged')
    git(vault, 'add', 'note')
    (vault / 'note').write_text('unstaged')
    (vault / 'untracked').write_text('untouched')
    index = (vault / '.git/index').read_bytes()
    journal = journal_api.LifecycleJournal.create(store, vault)
    journal.checkpoint(vault, 'repair-one', 'before')
    head = git(vault, 'rev-parse', 'HEAD').decode().strip()
    journal.checkpoint(vault, 'repair-one', 'after', head)
    records = journal_api.load_journal(journal.session_path, journal.anchor)
    assert [row['sequence'] for row in records] == [0, 1, 2]
    assert [row['phase'] for row in records] == ['initial', 'before', 'after']
    assert records[-1]['commit_oid'] == head
    assert records[0]['snapshot']['dirty']['note']
    assert records[0]['index_tree'] is not None
    assert (vault / '.git/index').read_bytes() == index
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in journal.session_path.iterdir())


@pytest.mark.parametrize('where', ['vault', 'repo', 'symlink'])
def test_refuses_internal_or_redirected_store(journal_api, roots, where):
    vault, store = roots
    if where == 'vault':
        store = vault
    elif where == 'repo':
        store = Path(__file__).resolve().parents[1]
    else:
        link = store.parent / 'link'
        link.symlink_to(store, target_is_directory=True)
        store = link
    with pytest.raises(journal_api.JournalError):
        journal_api.LifecycleJournal.create(store, vault)


@pytest.mark.parametrize('damage', ['missing', 'extra', 'tamper', 'symlink', 'mode', 'anchor', 'reorder'])
def test_refuses_damaged_journal(journal_api, roots, damage):
    vault, store = roots
    journal = journal_api.LifecycleJournal.create(store, vault)
    journal.checkpoint(vault, 'repair-one', 'before')
    paths = sorted(journal.session_path.iterdir())
    anchor = journal.anchor
    if damage == 'missing':
        paths[0].unlink()
    elif damage == 'extra':
        (journal.session_path / 'unexpected').write_text('unexpected')
    elif damage == 'tamper':
        paths[0].write_bytes(paths[0].read_bytes() + b' ')
    elif damage == 'symlink':
        paths[1].unlink()
        paths[1].symlink_to(paths[0])
    elif damage == 'mode':
        paths[0].chmod(0o644)
    elif damage == 'anchor':
        anchor = '0' * 64
    else:
        first, second = (path.read_bytes() for path in paths)
        paths[0].write_bytes(second)
        paths[1].write_bytes(first)
    with pytest.raises(journal_api.JournalError):
        journal_api.load_journal(journal.session_path, anchor)


def test_refuses_pending_or_mismatched_checkpoints(journal_api, roots):
    vault, store = roots
    journal = journal_api.LifecycleJournal.create(store, vault)
    journal.checkpoint(vault, 'repair-one', 'before')
    with pytest.raises(journal_api.JournalError):
        journal.checkpoint(vault, 'repair-two', 'before')
    with pytest.raises(journal_api.JournalError):
        journal.checkpoint(vault, 'repair-two', 'after', '0' * 40)
    with pytest.raises(journal_api.JournalError):
        journal.checkpoint(vault, 'repair-one', 'after', '0' * 40)


def test_refuses_missing_store_and_wrong_vault(journal_api, roots):
    vault, store = roots
    with pytest.raises(journal_api.JournalError):
        journal_api.LifecycleJournal.create(store / 'missing', vault)
    journal = journal_api.LifecycleJournal.create(store, vault)
    with pytest.raises(journal_api.JournalError):
        journal.checkpoint(store, 'repair-one', 'before')


def test_journal_capability_exists():
    assert importlib.util.find_spec('app.lifecycle_journal') is not None


def test_fsync_failure_preserves_evidence_and_refuses_retry(journal_api, roots, monkeypatch):
    vault, store = roots
    journal = journal_api.LifecycleJournal.create(store, vault)
    def fail(_descriptor):
        raise OSError('injected fsync failure')
    with monkeypatch.context() as patch:
        patch.setattr(journal_api.os, 'fsync', fail)
        with pytest.raises(journal_api.JournalError):
            journal.checkpoint(vault, 'repair-one', 'before')
    assert len(list(journal.session_path.iterdir())) == 2
    with pytest.raises(journal_api.JournalError):
        journal.checkpoint(vault, 'repair-one', 'before')


def test_empty_sha256_index_tree_is_correct(journal_api, tmp_path):
    vault = tmp_path / 'sha256-vault'
    vault.mkdir()
    git(vault, 'init', '-q', '--object-format=sha256')
    git(vault, 'config', 'user.email', 'fixture@example.invalid')
    git(vault, 'config', 'user.name', 'Fixture')
    git(vault, 'commit', '--allow-empty', '-qm', 'initial')
    store = tmp_path / 'external'
    store.mkdir()
    journal = journal_api.LifecycleJournal.create(store, vault)
    record = journal_api.load_journal(journal.session_path, journal.anchor)[0]
    assert record['index_tree'] == git(vault, 'rev-parse', 'HEAD^{tree}').decode().strip()


def test_computed_staged_tree_matches_git(journal_api, roots):
    vault, store = roots
    for path in ['a.b/file', 'a/file', 'z', 'a0']:
        target = vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path)
    git(vault, 'add', '.')
    expected = git(vault, 'write-tree').decode().strip()
    before = (vault / '.git/index').read_bytes()
    journal = journal_api.LifecycleJournal.create(store, vault)
    assert journal_api.load_journal(journal.session_path, journal.anchor)[0]['index_tree'] == expected
    assert (vault / '.git/index').read_bytes() == before


def test_rejects_unknown_dirty_kind_in_checkpoint(journal_api, roots):
    vault, store = roots
    (vault / 'note').write_text('dirty')
    journal = journal_api.LifecycleJournal.create(store, vault)
    journal.checkpoint(vault, 'repair-one', 'before')
    path = sorted(journal.session_path.iterdir())[-1]
    record = json.loads(path.read_bytes())
    record['snapshot']['dirty']['note']['kind'] = 'invented-kind'
    path.write_bytes((json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode())
    with pytest.raises(journal_api.JournalError):
        journal_api.load_journal(journal.session_path, journal.anchor)


def test_unmerged_index_is_preserved_and_has_no_tree(journal_api, roots):
    vault, store = roots
    blob = git(vault, 'rev-parse', 'HEAD:note').decode().strip()
    subprocess.run(['git', '-C', str(vault), 'update-index', '--index-info'],
                   input=f'0 {"0" * 40}\tnote\n100644 {blob} 1\tnote\n100644 {blob} 2\tnote\n'.encode(), check=True)
    before = (vault / '.git/index').read_bytes()
    journal = journal_api.LifecycleJournal.create(store, vault)
    assert journal_api.load_journal(journal.session_path, journal.anchor)[0]['index_tree'] is None
    assert (vault / '.git/index').read_bytes() == before
