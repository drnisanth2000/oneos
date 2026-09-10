"""Synthetic directory transactions preserve existing Git and filesystem state."""
import stat
import pytest
import app.git_transaction as tx
from tests.conftest import git_entity_vault, git_bytes


def identity(path):
    value = path.lstat()
    return value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode)


def vault(tmp_path):
    root = git_entity_vault(tmp_path, ('synthetic',), {'synthetic/module/status.md': 'status\n', 'dirty': 'base\n', '.gitignore': '*.ignored\n'})
    (root / 'dirty').write_text('staged\n')
    git_bytes(root, 'add', 'dirty')
    (root / 'dirty').write_text('unstaged\n')
    (root / 'extra.ignored').write_text('ignored\n')
    return root


def plan(root, removing=False):
    path = 'synthetic/module/active'
    empty, absent = tx.PathState.regular(b'', 0o644), tx.PathState.absent()
    placeholder = path + '/.gitkeep'
    return tx.TransactionPlan(
        'maintenance: synthetic structure',
        (tx.PathChange(placeholder, empty if removing else absent,
                       absent if removing else empty),),
        (placeholder,),
        directories=(tx.DirectoryChange(
            path, identity(root / path) if removing else None,
            None if removing else 0o755, identity(root / 'synthetic/module'),
        ),),
    )


def test_directory_api_is_explicit():
    assert hasattr(tx, 'DirectoryChange'), 'explicit directory ownership contract is missing'


def test_create_then_inverse_preserves_unrelated_state(tmp_path):
    root = vault(tmp_path)
    cached = git_bytes(root, 'diff', '--cached', '--', 'dirty')
    tx.execute_transaction(root, plan(root))
    assert (root / 'synthetic/module/active/.gitkeep').read_bytes() == b''
    assert identity(root / 'synthetic/module/active')[2] == 0o755
    tx.execute_transaction(root, plan(root, True))
    assert not (root / 'synthetic/module/active').exists()
    assert git_bytes(root, 'diff', '--cached', '--', 'dirty') == cached
    assert (root / 'dirty').read_text() == 'unstaged\n'
    assert (root / 'extra.ignored').read_text() == 'ignored\n'


@pytest.mark.parametrize('removing', [False, True])
@pytest.mark.parametrize('checkpoint', ['directory-created', 'filesystem-path-applied', 'filesystem-applied', 'alternate-index-ready', 'reviewed-paths-staged', 'commit-created', 'real-index-synchronized'])
def test_failure_restores_tree_and_full_index(tmp_path, monkeypatch, removing, checkpoint):
    root = vault(tmp_path)
    if removing:
        tx.execute_transaction(root, plan(root))
    if removing and checkpoint == 'directory-created':
        checkpoint = 'directory-removed'
    index = git_bytes(root, 'write-tree')
    head = git_bytes(root, 'rev-parse', 'HEAD')
    def fail(name):
        if name == checkpoint:
            raise RuntimeError('injected')
    monkeypatch.setattr(tx, '_checkpoint', fail)
    with pytest.raises(tx.GitTransactionFailure):
        tx.execute_transaction(root, plan(root, removing))
    assert git_bytes(root, 'write-tree') == index
    assert git_bytes(root, 'rev-parse', 'HEAD') == head
    assert (root / 'synthetic/module/active/.gitkeep').exists() == removing
    assert (root / 'synthetic/module/active').exists() == removing


@pytest.mark.parametrize('kind', ['occupied', 'symlink', 'parent'])
def test_stale_or_occupied_directory_refused_before_write(tmp_path, kind):
    root = vault(tmp_path)
    tx.execute_transaction(root, plan(root))
    reviewed = plan(root, True)
    directory = root / 'synthetic/module/active'
    if kind == 'occupied':
        (directory / 'extra.ignored').write_text('preserve')
    elif kind == 'symlink':
        directory.rename(directory.with_name('saved'))
        directory.symlink_to('saved', target_is_directory=True)
    else:
        (root / 'synthetic/module').chmod(0o700)
    head = git_bytes(root, 'rev-parse', 'HEAD')
    with pytest.raises(tx.GitTransactionError):
        tx.execute_transaction(root, reviewed)
    assert git_bytes(root, 'rev-parse', 'HEAD') == head
    assert (directory / '.gitkeep').exists()


def test_before_callback_failure_has_no_effect(tmp_path):
    from dataclasses import replace
    root = vault(tmp_path)
    def fail():
        raise OSError('journal unavailable')
    reviewed = replace(plan(root), before_apply=fail)
    head = git_bytes(root, 'rev-parse', 'HEAD')
    with pytest.raises(OSError):
        tx.execute_transaction(root, reviewed)
    assert not (root / 'synthetic/module/active').exists()
    assert git_bytes(root, 'rev-parse', 'HEAD') == head


def test_after_callback_failure_retains_committed_effect(tmp_path):
    from dataclasses import replace
    root = vault(tmp_path)
    def fail(result):
        assert git_bytes(root, 'rev-parse', 'HEAD').strip().decode() == result.commit_oid
        raise OSError('journal unavailable')
    with pytest.raises(tx.GitTransactionCommittedError):
        tx.execute_transaction(root, replace(plan(root), after_commit=fail))
    assert (root / 'synthetic/module/active/.gitkeep').exists()


def test_directory_replacement_during_failure_is_preserved(tmp_path, monkeypatch):
    root = vault(tmp_path)
    directory = root / 'synthetic/module/active'
    def replace(name):
        if name == 'filesystem-applied':
            directory.rename(directory.with_name('preserved'))
            directory.mkdir()
            (directory / '.gitkeep').write_bytes(b'')
            raise RuntimeError('injected replacement')
    monkeypatch.setattr(tx, '_checkpoint', replace)
    with pytest.raises(tx.GitTransactionRecoveryError):
        tx.execute_transaction(root, plan(root))
    assert (directory / '.gitkeep').exists()
    assert (directory.with_name('preserved') / '.gitkeep').exists()


def test_postcommit_directory_replacement_is_not_reported_success(tmp_path, monkeypatch):
    root = vault(tmp_path)
    directory = root / 'synthetic/module/active'
    def replace(name):
        if name == 'before-proposal-quarantine':
            directory.rename(root.parent / (root.name + '-preserved'))
            directory.mkdir()
            (directory / '.gitkeep').write_bytes(b'')
    monkeypatch.setattr(tx, '_checkpoint', replace)
    with pytest.raises(tx.PostCommitConsumptionError):
        tx.execute_transaction(root, plan(root))
    assert (directory / '.gitkeep').exists()


def test_lifecycle_publish_never_overwrites_racing_placeholder(tmp_path, monkeypatch):
    root = vault(tmp_path)
    placeholder = root / 'synthetic/module/active/.gitkeep'
    original_replace = tx.os.replace
    original_move = tx._move_no_replace
    raced = False
    def insert(destination):
        nonlocal raced
        if destination == '.gitkeep' and not raced:
            raced = True
            placeholder.write_bytes(b'concurrent owner')
    def replacing(source, destination, **kwargs):
        insert(destination)
        return original_replace(source, destination, **kwargs)
    def moving(source_fd, source, destination_fd, destination):
        insert(destination)
        return original_move(source_fd, source, destination_fd, destination)
    monkeypatch.setattr(tx.os, 'replace', replacing)
    monkeypatch.setattr(tx, '_move_no_replace', moving)
    with pytest.raises(tx.GitTransactionRecoveryError):
        tx.execute_transaction(root, plan(root))
    assert placeholder.read_bytes() == b'concurrent owner'


def test_same_byte_leaf_replacement_survives_recovery(tmp_path, monkeypatch):
    root = vault(tmp_path)
    placeholder = root / 'synthetic/module/active/.gitkeep'
    saved = root.parent / 'saved-placeholder'
    def replace(name):
        if name == 'filesystem-applied':
            placeholder.rename(saved)
            placeholder.write_bytes(b'')
            raise RuntimeError('replacement race')
    monkeypatch.setattr(tx, '_checkpoint', replace)
    with pytest.raises(tx.GitTransactionRecoveryError):
        tx.execute_transaction(root, plan(root))
    assert placeholder.read_bytes() == b''
    assert saved.read_bytes() == b''


def test_failed_directory_removal_restores_original_inode_and_retry(tmp_path, monkeypatch):
    root = vault(tmp_path)
    tx.execute_transaction(root, plan(root))
    reviewed = plan(root, True)
    directory = root / 'synthetic/module/active'
    original_identity = identity(directory)
    def fail(name):
        if name == 'directory-removed':
            raise RuntimeError('after removal')
    with monkeypatch.context() as patch:
        patch.setattr(tx, '_checkpoint', fail)
        with pytest.raises(tx.GitTransactionFailure):
            tx.execute_transaction(root, reviewed)
    assert identity(directory) == original_identity
    tx.execute_transaction(root, reviewed)
    assert not directory.exists()
    assert not list((root / '.git').glob('oneos-directory-*'))


def test_same_byte_leaf_replacement_cannot_report_success(tmp_path, monkeypatch):
    root = vault(tmp_path)
    placeholder = root / 'synthetic/module/active/.gitkeep'
    def replace(name):
        if name == 'filesystem-applied':
            placeholder.rename(root.parent / 'displaced-placeholder')
            placeholder.write_bytes(b'')
    monkeypatch.setattr(tx, '_checkpoint', replace)
    with pytest.raises(tx.GitTransactionRecoveryError):
        tx.execute_transaction(root, plan(root))
    assert placeholder.exists()


def test_unowned_recovery_container_is_reported_as_conflict(tmp_path, monkeypatch):
    root = vault(tmp_path)
    tx.execute_transaction(root, plan(root))
    original = tx._open_checked_directory
    def fail(path, description, **kwargs):
        if description == 'directory recovery':
            raise tx.ReviewedPathUnavailable('injected recovery open failure')
        return original(path, description, **kwargs)
    monkeypatch.setattr(tx, '_open_checked_directory', fail)
    with pytest.raises(tx.GitTransactionRecoveryError):
        tx.execute_transaction(root, plan(root, True))
    assert (root / 'synthetic/module/active/.gitkeep').exists()
    assert list((root / '.git').glob('oneos-directory-*'))
