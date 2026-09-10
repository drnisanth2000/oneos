"""Policy-gated lifecycle maintenance through exact-byte outbox proposals."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import stat

from .action_receipts import InvalidActionReceipt, SpentAction, resolve_head_receipt, receipt_relative_path
from .git_transaction import (PathState, PathChange, DirectoryChange, TransactionPlan,
    TransactionPreconditionRefused, action_lock, capture_path_state, execute_transaction,
    _walk_to_parent, _open_checked_directory, _git, _git_text)
from .lifecycle_receipts import (ACTIONS, REGISTRIES, EMPTY_SHA, LifecycleReceipt,
    canonical, digest, load_mapping, parse_proposal, render_lifecycle_receipt)
from .lifecycle_shape import required_shape, inspect_shape
from .outbox import OutboxError
from .proposal_identity import proposal_id_candidates, require_proposal_id
from .review_tokens import ReviewSnapshot, make_review_snapshot, require_review_match
from .scope import Scope


class LifecycleError(OutboxError):
    pass


def identity(vault: Path, relative: str) -> list[int]:
    parent,leaf=_walk_to_parent(vault,relative)
    try:
        fd=_open_checked_directory(leaf,'lifecycle directory',dir_fd=parent)
        try:
            s=os.fstat(fd)
            return [s.st_dev,s.st_ino,stat.S_IMODE(s.st_mode)]
        finally: os.close(fd)
    finally: os.close(parent)


def vault_identity(vault: Path) -> str:
    fd=_open_checked_directory(vault,'lifecycle vault')
    try:
        s=os.fstat(fd)
        return digest(canonical([str(vault),s.st_dev,s.st_ino]))
    finally: os.close(fd)


def require_policy(contents: bytes, action: str, entity: str, actor: str) -> None:
    if actor!='owner' or action not in ACTIONS:
        raise LifecycleError('lifecycle maintenance requires owner authorization')
    policy=load_mapping(contents)
    actions=policy.get('lifecycle_actions')
    if not isinstance(actions,dict) or set(actions)-ACTIONS:
        raise LifecycleError('lifecycle maintenance is not explicitly authorized')
    rule=actions.get(action)
    if not isinstance(rule,dict) or set(rule)!={'actor','entities'} or rule['actor']!='owner':
        raise LifecycleError('lifecycle action policy is invalid or absent')
    entities=rule['entities']
    if not isinstance(entities,list) or any(not isinstance(e,str) for e in entities) or len(entities)!=len(set(entities)) or entity not in entities:
        raise LifecycleError('lifecycle scope is not explicitly authorized')


def _require_git_environment() -> None:
    if any(os.environ.get(key) for key in ('GIT_INDEX_FILE','GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR')):
        raise LifecycleError('Git environment overrides are not supported for lifecycle actions')


def bindings(scope: Scope, action: str, actor: str) -> dict:
    _require_git_environment()
    entity=scope.current_entity();v=scope.root
    hashes={};head=_git_text(v,'rev-parse','HEAD').strip()
    for path in REGISTRIES:
        state=capture_path_state(v,path)
        if state.contents is None:raise LifecycleError('lifecycle declaration unavailable')
        if _git(v,'show',f'{head}:{path}').stdout!=state.contents:
            raise LifecycleError('lifecycle declarations differ from committed authority')
        hashes[path]=digest(state.contents)
        if path==REGISTRIES[-1]:require_policy(state.contents,action,entity,actor)
    shape=[{'path':e.path,'kind':e.kind,'declaration':e.declaration} for e in required_shape(scope)]
    # Historical audit must be able to prove required content and roots from
    # the same baseline. An untracked file cannot supply committed authority.
    tree = {}
    for record in _git(v, 'ls-tree', '-r', '-z', head).stdout.split(b'\0'):
        if record:
            metadata, path = record.split(b'\t', 1)
            mode, kind, _oid = metadata.split(b' ')
            tree[os.fsdecode(path)] = (mode, kind)
    if any(tree.get(path) not in {(b'100644', b'blob'), (b'100755', b'blob')} for path in REGISTRIES):
        raise LifecycleError('lifecycle registry modes lack committed authority')
    for entry in shape:
        path = entry['path']
        if entry['kind'] == 'file':
            if tree.get(path) not in {(b'100644', b'blob'), (b'100755', b'blob')}:
                raise LifecycleError('required content lacks committed authority')
        elif len(path.split('/')) < 3:
            if not any(name.startswith(path + '/') for name in tree):
                raise LifecycleError('required root lacks committed authority')
    return dict(entity=entity,baseline_head=head,vault_identity=vault_identity(v),
                registry_sha256=hashes,shape_sha256=digest(canonical(shape)))


def plan_repair(scope: Scope, *, actor: str) -> dict:
    b=bindings(scope,'lifecycle_repair',actor)
    missing=inspect_shape(scope)
    if not missing:raise LifecycleError('no missing lifecycle directories')
    manifest=[dict(path=e.path,declaration=e.declaration,
        parent_identity=identity(scope.root,str(Path(e.path).parent)),
        before_identity=None,after_mode=0o755,placeholder_sha256=EMPTY_SHA) for e in missing]
    return dict(**b,manifest=manifest,repair=None)


def _spent(scope: Scope, proposal_id: str):
    _require_git_environment()
    r=resolve_head_receipt(scope.root,scope.current_entity(),proposal_id)
    if r.error:raise r.error
    return r.receipt


def _write_proposal(scope: Scope, action: str, plan: dict) -> ReviewSnapshot:
    created=datetime.now().replace(microsecond=0)
    parent,leaf=_walk_to_parent(scope.root,f'{scope.current_entity()}/outbox/unused.yaml')
    try:
        for candidate in proposal_id_candidates(created):
            if _spent(scope,candidate):continue
            p=dict(version=1,id=candidate,action=action,created=created.isoformat(),**plan)
            raw=canonical(p)+b'\n';parse_proposal(raw)
            try:fd=os.open(candidate+'.yaml',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            except FileExistsError:continue
            with os.fdopen(fd,'wb') as f:
                f.write(raw); f.flush();os.fsync(f.fileno())
            return make_review_snapshot(p,raw)
    finally:os.close(parent)
    raise LifecycleError('could not allocate a fresh proposal identity')


def propose_repair(scope: Scope, *, actor: str) -> ReviewSnapshot:
    with action_lock(scope.root):
        return _write_proposal(scope,'lifecycle_repair',plan_repair(scope,actor=actor))


def get_review(scope: Scope, proposal_id: str) -> ReviewSnapshot:
    require_proposal_id(proposal_id)
    if _spent(scope,proposal_id):raise LifecycleError('lifecycle proposal identity is spent')
    relative=f'{scope.current_entity()}/outbox/{proposal_id}.yaml'
    state=capture_path_state(scope.root,relative)
    if state.contents is None:raise LifecycleError('lifecycle proposal is unavailable')
    if state.mode != 0o600:raise LifecycleError('lifecycle proposal mode changed')
    p=parse_proposal(state.contents,Path(relative))
    if p['entity']!=scope.current_entity():raise LifecycleError('lifecycle proposal belongs to another scope')
    return make_review_snapshot(p,state.contents)


def plan_rollback(scope: Scope, repair_commit: str, *, actor: str, journal) -> dict:
    from .lifecycle_history import validated_commit, require_unreverted
    b=bindings(scope,'lifecycle_rollback',actor)
    repair,receipt_path,blob=validated_commit(scope.root,repair_commit)
    p=repair.proposal
    if p['action']!='lifecycle_repair' or p['entity']!=scope.current_entity():
        raise LifecycleError('rollback must refer to a sanctioned scoped repair')
    require_unreverted(scope.root,repair_commit,b['baseline_head'])
    if _git(scope.root,'show',f"{b['baseline_head']}:{receipt_path}").stdout!=_git(scope.root,'show',f'{repair_commit}:{receipt_path}').stdout:
        raise LifecycleError('original repair evidence changed')
    if journal is None:raise LifecycleError('original repair checkpoint evidence is required')
    from .lifecycle_journal import load_journal
    from tools.gate3_audit import collect_gate3_evidence
    from dataclasses import asdict
    checkpoints=load_journal(journal.session_path,journal.anchor)
    matches=[c for c in checkpoints if c['phase']=='after' and c['commit_oid']==repair_commit]
    if len(matches)!=1:raise LifecycleError('original repair checkpoint evidence is unavailable')
    current_fs=collect_gate3_evidence(scope.root).filesystem
    manifest=[]
    for e in p['manifest']:
        original_state=matches[0]['snapshot']['filesystem'].get(e['path'])
        current_state=current_fs.get(e['path'])
        if current_state is None or asdict(current_state)!=original_state:
            raise LifecycleError('repair directory ownership changed')
        path=e['path']; parent=identity(scope.root,str(Path(path).parent))
        if parent!=e['parent_identity']:raise LifecycleError('repair parent changed')
        current=identity(scope.root,path)
        if current[2]!=e['after_mode']:raise LifecycleError('repair directory mode changed')
        fd,leaf=_walk_to_parent(scope.root,path)
        try:
            child=_open_checked_directory(leaf,'rollback directory',dir_fd=fd)
            try:
                if os.listdir(child)!=['.gitkeep']:raise LifecycleError('repair directory is occupied')
            finally:os.close(child)
        finally:os.close(fd)
        if capture_path_state(scope.root,path+'/.gitkeep')!=PathState.regular(b'',0o644):
            raise LifecycleError('repair placeholder changed')
        manifest.append(dict(e,before_identity=current,after_mode=None))
    return dict(**b,manifest=manifest,repair=dict(commit=repair_commit,receipt_path=receipt_path,receipt_blob=blob))


def propose_rollback(scope: Scope, repair_commit: str, *, actor: str, journal) -> ReviewSnapshot:
    with action_lock(scope.root):
        return _write_proposal(scope,'lifecycle_rollback',plan_rollback(scope,repair_commit,actor=actor,journal=journal))


def approve_lifecycle(scope: Scope, proposal_id: str, review_sha256: object, *, actor: str, journal):
    existing=_spent(scope,proposal_id)
    if existing:return SpentAction(existing)
    review=get_review(scope,proposal_id);require_review_match(review.contents,review_sha256)
    p=review.value
    if journal is None:raise LifecycleError('lifecycle audit journal is required')
    relative=f'{scope.current_entity()}/outbox/{proposal_id}.yaml'
    proposal_state=capture_path_state(scope.root,relative)
    if proposal_state.mode != 0o600:raise LifecycleError('lifecycle proposal mode changed')
    require_review_match(proposal_state.contents,review.sha256)
    receipt_path=receipt_relative_path(scope.current_entity(),proposal_id)
    receipt_bytes=render_lifecycle_receipt(review.contents)
    changes=[];directories=[]
    for e in p['manifest']:
        removing=p['action']=='lifecycle_rollback'
        changes.append(PathChange(e['path']+'/.gitkeep',PathState.regular(b'',0o644) if removing else PathState.absent(),
                                  PathState.absent() if removing else PathState.regular(b'',0o644)))
        directories.append(DirectoryChange(e['path'],tuple(e['before_identity']) if removing else None,e['after_mode'],tuple(e['parent_identity'])))
    changes.append(PathChange(receipt_path,PathState.absent(),PathState.regular(receipt_bytes,0o644),create_parent=True))
    def precondition():
        spent=_spent(scope,proposal_id)
        if spent:return spent
        current=(plan_repair(scope,actor=actor) if p['action']=='lifecycle_repair'
                 else plan_rollback(scope,p['repair']['commit'],actor=actor,journal=journal))
        expected={k:p[k] for k in current}
        if canonical(current)!=canonical(expected):raise LifecycleError('lifecycle proposal is stale')
        return None
    plan=TransactionPlan(message=f"{p['action']}: {proposal_id}",changes=tuple(changes),
        commit_paths=tuple(c.path for c in changes),
        owned_changes=(PathChange(relative,proposal_state,PathState.absent()),),
        preconditions=(precondition,),directories=tuple(directories),
        before_apply=lambda:journal.checkpoint(scope.root,proposal_id,'before'),
        after_commit=lambda result:journal.checkpoint(scope.root,proposal_id,'after',commit_oid=result.commit_oid))
    result=execute_transaction(scope.root,plan)
    if isinstance(result,TransactionPreconditionRefused):return SpentAction(result.reason)
    return result
