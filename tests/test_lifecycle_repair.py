"""Real synthetic Git actions; no live configuration or private fixtures."""
import importlib
import subprocess
import hashlib
import json
from pathlib import Path
import pytest
from tests.conftest import write_vault, entities_yaml, git_bytes


def git(v,*args):
    return git_bytes(v,*args).decode().strip()

@pytest.fixture
def lifecycle_vault(tmp_path):
    v=tmp_path/'vault'; v.mkdir()
    write_vault(v,entities_yaml('sample'),'''version: "2.0"
flags: {}
modules:
  01-work: {block: build, lifecycle_pattern: true}
submodules: {}
''')
    p=v/'sample/01-work';p.mkdir(parents=True)
    (p/'_templates').mkdir(); (p/'_templates/.gitkeep').write_bytes(b'')
    (p/'archive').mkdir(); (p/'archive/.gitkeep').write_bytes(b'')
    (p/'status.md').write_text('existing status\n')
    (v/'sample/outbox').mkdir();(v/'sample/outbox/.gitkeep').write_bytes(b'')
    s=v/'_system/scripts';s.mkdir()
    (s/'action-policy.yaml').write_text(json.dumps({'lifecycle_actions':{
        a:{'actor':'owner','entities':['sample']} for a in ['lifecycle_repair','lifecycle_rollback']}}))
    git(v,'init','-q');git(v,'config','user.name','Synthetic');git(v,'config','user.email','test@example.invalid')
    git(v,'add','.');git(v,'commit','-qm','fixture')
    return v


def service():
    try: return importlib.import_module('app.lifecycle_repair')
    except ModuleNotFoundError: pytest.fail('lifecycle action boundary is not implemented')


def test_readonly_plan_then_bound_outbox_proposal(lifecycle_vault):
    from app.scope import Scope
    s=service();scope=Scope(lifecycle_vault,'sample')
    before=git(lifecycle_vault,'status','--porcelain')
    p=s.plan_repair(scope,actor='owner')
    assert git(lifecycle_vault,'status','--porcelain')==before
    assert [e['path'] for e in p['manifest']]==['sample/01-work/active']
    review=s.propose_repair(scope,actor='owner')
    assert review.sha256==hashlib.sha256(review.contents).hexdigest()
    assert not (lifecycle_vault/'sample/01-work/active').exists()

@pytest.mark.parametrize('actor',['worker','hermes','',None])
def test_worker_cannot_propose(lifecycle_vault,actor):
    from app.scope import Scope
    s=service();before=git(lifecycle_vault,'status','--porcelain')
    with pytest.raises(s.LifecycleError):s.propose_repair(Scope(lifecycle_vault,'sample'),actor=actor)
    assert git(lifecycle_vault,'status','--porcelain')==before


def test_absent_policy_denies(lifecycle_vault):
    from app.scope import Scope
    s=service();(lifecycle_vault/'_system/scripts/action-policy.yaml').write_text('{}')
    git(lifecycle_vault,'add','.');git(lifecycle_vault,'commit','-qm','policy disabled')
    with pytest.raises(s.LifecycleError):s.propose_repair(Scope(lifecycle_vault,'sample'),actor='owner')


def journal_for(v,tmp_path):
    from app.lifecycle_journal import LifecycleJournal
    store=tmp_path/'evidence';store.mkdir()
    return LifecycleJournal.create(store,v)


def unrelated(v):
    from tools.gate3_audit import collect_gate3_evidence
    evidence=collect_gate3_evidence(v)
    # Independently project every unrelated index entry with real Git, rather
    # than trusting the journal's in-memory tree hash implementation.
    import os
    import tempfile
    entries=git_bytes(v,'ls-files','--stage','-z')
    kept=[]
    for record in entries.split(b'\0'):
        if not record:continue
        path=record.split(b'\t',1)[1]
        if path==b'sample/01-work/active/.gitkeep' or path.startswith(b'sample/outbox/.receipts/'):
            continue
        kept.append(record)
    with tempfile.TemporaryDirectory(prefix='oneos-index-proof-') as folder:
        environment=dict(os.environ,GIT_INDEX_FILE=str(Path(folder)/'index'))
        subprocess.run(['git','read-tree','--empty'],cwd=v,env=environment,check=True,capture_output=True)
        subprocess.run(['git','update-index','-z','--index-info'],cwd=v,env=environment,
                       input=b'\0'.join(kept)+b'\0',check=True,capture_output=True)
        projected=subprocess.run(['git','write-tree'],cwd=v,env=environment,check=True,capture_output=True).stdout
    return (tuple(kept),projected,
            (v/'unrelated.txt').read_bytes(), (v/'ignored.bin').read_bytes(),
            (v/'untracked.bin').read_bytes(),
            {p:f for p,f in evidence.dirty.items() if p in {'unrelated.txt','ignored.bin','untracked.bin'}})


def test_repair_rollback_retains_receipts_and_all_unrelated_state(lifecycle_vault,tmp_path):
    from app.scope import Scope
    from app.action_receipts import resolve_head_receipt
    from tools.gate3_audit import audit_lifecycle_session
    v=lifecycle_vault;s=service();scope=Scope(v,'sample')
    (v/'unrelated.txt').write_text('committed\n');(v/'.gitignore').write_text('ignored.bin\n')
    git(v,'add','.');git(v,'commit','-qm','unrelated fixture')
    (v/'unrelated.txt').write_text('staged\n');git(v,'add','unrelated.txt')
    (v/'unrelated.txt').write_text('unstaged\n');(v/'ignored.bin').write_bytes(b'protected ignored')
    (v/'untracked.bin').write_bytes(b'protected untracked')
    original=unrelated(v);initial=git(v,'rev-parse','HEAD');journal=journal_for(v,tmp_path)
    review=s.propose_repair(scope,actor='owner')
    repaired=s.approve_lifecycle(scope,review.value['id'],review.sha256,actor='owner',journal=journal)
    assert (v/'sample/01-work/active/.gitkeep').read_bytes()==b''
    assert unrelated(v)==original
    first=resolve_head_receipt(v,'sample',review.value['id']).receipt
    assert first is not None and first.action_kind=='lifecycle_repair'
    rollback=s.propose_rollback(scope,repaired.commit_oid,actor='owner',journal=journal)
    assert unrelated(v)==original
    s.approve_lifecycle(scope,rollback.value['id'],rollback.sha256,actor='owner',journal=journal)
    assert not (v/'sample/01-work/active').exists()
    assert unrelated(v)==original
    assert git(v,'rev-list','--count',initial+'..HEAD')=='2'
    for r in (review,rollback):assert resolve_head_receipt(v,'sample',r.value['id']).receipt is not None
    final_paths=git(v,'diff','--name-only',initial,'HEAD').splitlines()
    assert final_paths==sorted('sample/outbox/.receipts/'+r.value['id']+'.yaml' for r in (review,rollback))
    audit=audit_lifecycle_session(v,journal.session_path,journal.anchor)
    assert audit.ok and len(audit.sanctioned_commits)==2
    assert not audit.violating_commits and not audit.violating_writes

@pytest.mark.parametrize('change',['proposal','head','registry','occupied','symlink','parent','mode'])
def test_changed_review_refuses_before_mutation(lifecycle_vault,tmp_path,change):
    from app.scope import Scope
    v=lifecycle_vault;s=service();scope=Scope(v,'sample');journal=journal_for(v,tmp_path)
    review=s.propose_repair(scope,actor='owner');target=v/'sample/01-work/active'
    if change=='proposal': (v/('sample/outbox/'+review.value['id']+'.yaml')).write_bytes(review.contents+b' ')
    if change=='head':git(v,'commit','--allow-empty','-qm','new head')
    if change=='registry':(v/'_system/archetypes.yaml').write_text('changed')
    if change=='occupied':target.mkdir();(target/'keep').write_bytes(b'keep')
    if change=='symlink':target.symlink_to(v/'sample/01-work/archive',target_is_directory=True)
    if change=='parent':
        (v/'sample/01-work').rename(v/'sample/original');
        import shutil
        shutil.copytree(v/'sample/original',v/'sample/01-work')
    if change=='mode':(v/'sample/01-work').chmod(0o700)
    head=git(v,'rev-parse','HEAD');index=git_bytes(v,'ls-files','--stage','-z');status=git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')
    from app.git_transaction import GitTransactionError
    from app.action_receipts import InvalidActionReceipt
    from app.review_tokens import ReviewTokenError
    from app.lifecycle_shape import LifecycleShapeError
    with pytest.raises((s.LifecycleError, GitTransactionError, InvalidActionReceipt, ReviewTokenError, LifecycleShapeError)):s.approve_lifecycle(scope,review.value['id'],review.sha256,actor='owner',journal=journal)
    assert git(v,'rev-parse','HEAD')==head
    assert git_bytes(v,'ls-files','--stage','-z')==index
    assert git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')==status


def test_rollback_refuses_same_bytes_replacement_directory(lifecycle_vault,tmp_path):
    from app.scope import Scope
    v=lifecycle_vault;s=service();scope=Scope(v,'sample');journal=journal_for(v,tmp_path)
    review=s.propose_repair(scope,actor='owner');done=s.approve_lifecycle(scope,review.value['id'],review.sha256,actor='owner',journal=journal)
    target=v/'sample/01-work/active';target.rename(v/'sample/retained-original')
    target.mkdir();(target/'.gitkeep').write_bytes(b'')
    head=git(v,'rev-parse','HEAD')
    with pytest.raises(s.LifecycleError):s.propose_rollback(scope,done.commit_oid,actor='owner',journal=journal)
    assert git(v,'rev-parse','HEAD')==head and target.is_dir()

@pytest.mark.parametrize('key',['GIT_INDEX_FILE','GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR'])
def test_git_environment_redirect_cannot_change_action_authority(lifecycle_vault,monkeypatch,key):
    from app.scope import Scope
    s=service();scope=Scope(lifecycle_vault,'sample')
    monkeypatch.setenv(key,'untrusted-location')
    with pytest.raises(s.LifecycleError):s.plan_repair(scope,actor='owner')


def test_untracked_required_content_cannot_authorize_unauditable_repair(lifecycle_vault):
    from app.scope import Scope
    s=service();v=lifecycle_vault
    git(v,'rm','--cached','sample/01-work/status.md');git(v,'commit','-qm','untracked content fixture')
    head=git(v,'rev-parse','HEAD');before=git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')
    with pytest.raises(s.LifecycleError):s.propose_repair(Scope(v,'sample'),actor='owner')
    assert git(v,'rev-parse','HEAD')==head
    assert git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')==before


def test_git_invisible_required_root_cannot_authorize_repair(lifecycle_vault):
    from app.scope import Scope
    s=service();v=lifecycle_vault
    path=v/'_system/archetypes.yaml'
    path.write_text(path.read_text().replace('submodules: {}','  02-extra: {block: build, lifecycle_pattern: false}\nsubmodules: {}'))
    (v/'sample/02-extra').mkdir()
    git(v,'add','_system/archetypes.yaml');git(v,'commit','-qm','untracked root fixture')
    before=git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')
    with pytest.raises(s.LifecycleError):s.propose_repair(Scope(v,'sample'),actor='owner')
    assert git_bytes(v,'status','--porcelain=v2','-z','--untracked-files=all')==before


def test_changed_proposal_mode_refused_before_repair(lifecycle_vault,tmp_path):
    from app.scope import Scope
    s=service();v=lifecycle_vault;scope=Scope(v,'sample');journal=journal_for(v,tmp_path)
    review=s.propose_repair(scope,actor='owner')
    (v/('sample/outbox/'+review.value['id']+'.yaml')).chmod(0o644)
    head=git(v,'rev-parse','HEAD')
    with pytest.raises(s.LifecycleError):s.approve_lifecycle(scope,review.value['id'],review.sha256,actor='owner',journal=journal)
    assert git(v,'rev-parse','HEAD')==head and not (v/'sample/01-work/active').exists()


def test_executable_committed_registry_remains_auditable(lifecycle_vault,tmp_path):
    from app.scope import Scope
    from tools.gate3_audit import audit_lifecycle_session
    s=service();v=lifecycle_vault
    (v/'_system/archetypes.yaml').chmod(0o755)
    git(v,'add','_system/archetypes.yaml');git(v,'commit','-qm','registry mode fixture')
    scope=Scope(v,'sample');journal=journal_for(v,tmp_path)
    review=s.propose_repair(scope,actor='owner')
    s.approve_lifecycle(scope,review.value['id'],review.sha256,actor='owner',journal=journal)
    assert audit_lifecycle_session(v,journal.session_path,journal.anchor).ok
