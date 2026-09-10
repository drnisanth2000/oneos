"""Historical, exact-diff authority for governed lifecycle maintenance."""
from pathlib import Path
import subprocess
import tempfile

from .lifecycle_receipts import (ACTIONS, REGISTRIES, LifecycleReceipt,
    parse_lifecycle_receipt, digest, canonical)
from .lifecycle_repair import LifecycleError, require_policy, vault_identity
from .action_receipts import InvalidActionReceipt
from .lifecycle_shape import required_shape
from .scope import Scope


def _git(vault, *args):
    try:
        return subprocess.run(['git', *args], cwd=vault, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('historical lifecycle evidence unavailable') from exc


def _tree(vault, commit):
    entries = {}
    for item in _git(vault, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if item:
            meta, path = item.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            entries[path.decode()] = (mode, kind, oid)
    return entries


def _require(condition):
    if not condition:
        raise ValueError('historical lifecycle authority does not match')


def _shape(vault, parent, proposal):
    with tempfile.TemporaryDirectory(prefix='oneos-lifecycle-history-') as directory:
        root = Path(directory)
        for path in REGISTRIES:
            raw = _git(vault, 'show', parent + ':' + path)
            _require(digest(raw) == proposal['registry_sha256'][path])
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        require_policy((root / REGISTRIES[-1]).read_bytes(), proposal['action'], proposal['entity'], 'owner')
        entries = required_shape(Scope(root, proposal['entity']))
    value = [{'path': e.path, 'kind': e.kind, 'declaration': e.declaration} for e in entries]
    _require(digest(canonical(value)) == proposal['shape_sha256'])
    return entries


def validated_commit(vault: Path, oid: str) -> tuple[LifecycleReceipt, str, str]:
    """Validate a candidate using immutable parent/commit blobs, never live rules."""
    ancestry = _git(vault, 'rev-list', '--parents', '-n', '1', oid).decode().split()
    _require(len(ancestry) == 2 and ancestry[0] == oid)
    parent = ancestry[1]
    message = _git(vault, 'show', '-s', '--format=%B', oid).decode().rstrip('\n')
    action, separator, proposal_id = message.partition(': ')
    _require(separator == ': ' and action in ACTIONS and '\n' not in proposal_id)
    before, after = _tree(vault, parent), _tree(vault, oid)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    candidates = [p for p in changed if p.endswith('/outbox/.receipts/' + proposal_id + '.yaml')]
    _require(len(candidates) == 1)
    receipt_path = candidates[0]
    _require(receipt_path not in before and after[receipt_path][:2] == ('100644', 'blob'))
    receipt = parse_lifecycle_receipt(Path(receipt_path), _git(vault, 'show', oid + ':' + receipt_path))
    p = receipt.proposal
    _require(p['action'] == action and p['baseline_head'] == parent)
    _require(p['vault_identity'] == vault_identity(vault))
    _require(not _git(vault, 'log', '--format=%H', parent, '--', receipt_path).strip())
    for path in REGISTRIES:
        _require(before.get(path) == after.get(path) and before.get(path, ())[:2] in {('100644', 'blob'), ('100755', 'blob')})
    shape = _shape(vault, parent, p)
    declared = {e.path: e for e in shape}
    placeholders = {e['path'] + '/.gitkeep' for e in p['manifest']}
    _require(changed == placeholders | {receipt_path})
    for e in shape:
        if e.kind == 'file':
            _require(before.get(e.path, ())[:2] in {('100644', 'blob'), ('100755', 'blob')})
        elif len(e.path.split('/')) < 3:
            _require(any(path.startswith(e.path + '/') for path in before))
    for entry in p['manifest']:
        path = entry['path']
        _require(path in declared and declared[path].kind == 'directory' and declared[path].declaration == entry['declaration'])
        leaf = path + '/.gitkeep'
        source, destination = (before, after) if action == 'lifecycle_repair' else (after, before)
        _require(not any(name == path or name.startswith(path + '/') for name in source))
        _require(destination.get(leaf, ())[:2] == ('100644', 'blob'))
        _require(_git(vault, 'cat-file', 'blob', destination[leaf][2]) == b'')
        _require({name for name in destination if name.startswith(path + '/')} == {leaf})
    if action == 'lifecycle_rollback':
        ref = p['repair']
        original, original_path, original_blob = validated_commit(vault, ref['commit'])
        q = original.proposal
        _require(q['action'] == 'lifecycle_repair' and q['entity'] == p['entity'])
        _require(ref['receipt_path'] == original_path and ref['receipt_blob'] == original_blob)
        require_unreverted(vault, ref['commit'], parent)
        _require(before.get(original_path) == after.get(original_path) == ('100644', 'blob', original_blob))
        _require(len(q['manifest']) == len(p['manifest']))
        for original_entry, inverse in zip(q['manifest'], p['manifest']):
            for key in ('path', 'declaration', 'parent_identity', 'placeholder_sha256'):
                _require(original_entry[key] == inverse[key])
            _require(inverse['before_identity'][2] == original_entry['after_mode'])
    return receipt, receipt_path, after[receipt_path][2]


def require_unreverted(vault: Path, repair_oid: str, head: str) -> None:
    _git(vault, 'merge-base', '--is-ancestor', repair_oid, head)
    for oid in _git(vault, 'rev-list', repair_oid + '..' + head).decode().split():
        message = _git(vault, 'show', '-s', '--format=%s', oid).decode()
        if not message.startswith('lifecycle_rollback: '):
            continue
        try:
            receipt, _, _ = validated_commit(vault, oid)
        except (ValueError, InvalidActionReceipt, LifecycleError):
            continue
        if receipt.proposal['repair']['commit'] == repair_oid:
            raise ValueError('repair already has a sanctioned rollback')
