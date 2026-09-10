"""Synthetic lifecycle receipt parsing: altered evidence must never spend an id."""
import base64
import hashlib
import json
from pathlib import Path
import pytest
from app.action_receipts import parse_action_receipt, InvalidActionReceipt

ID = '20260910T120000-' + 'a' * 32

def proposal():
    return dict(version=1, id=ID, action='lifecycle_repair', entity='sample',
        created='2026-09-10T12:00:00', baseline_head='a'*40, vault_identity='b'*64,
        registry_sha256={'_system/entities.yaml':'c'*64, '_system/archetypes.yaml':'d'*64,
                         '_system/scripts/action-policy.yaml':'e'*64},
        shape_sha256='f'*64, manifest=[dict(path='sample/01-work/active',
            declaration='lifecycle:active', parent_identity=[1,2,493],
            before_identity=None, after_mode=493, placeholder_sha256=hashlib.sha256(b'').hexdigest())], repair=None)

def receipt_bytes():
    p=proposal(); raw=json.dumps(p,sort_keys=True,separators=(',',':')).encode()
    return json.dumps(dict(version=2, proposal_id=ID, action_kind=p['action'],
        review_sha256=hashlib.sha256(raw).hexdigest(), proposal_b64=base64.b64encode(raw).decode(),
        **{k:p[k] for k in ('entity','baseline_head','vault_identity','registry_sha256','shape_sha256','manifest','repair')})).encode()

def test_v2_receipt_recognized_and_binds_original_bytes():
    r=parse_action_receipt(Path(ID+'.yaml'),receipt_bytes())
    assert r.version==2 and r.action_kind=='lifecycle_repair'
    assert json.loads(r.proposal_bytes)['manifest'][0]['path']=='sample/01-work/active'

@pytest.mark.parametrize('mutation', ['hash','manifest','extra','action','filename','bytes'])
def test_tampered_receipt_refused(mutation):
    r=json.loads(receipt_bytes()); path=Path(ID+'.yaml')
    if mutation=='hash': r['review_sha256']='0'*64
    if mutation=='manifest': r['manifest'][0]['path']='sample/01-work/arbitrary'
    if mutation=='extra': r['extra']=True
    if mutation=='action': r['action_kind']='approval'
    if mutation=='filename': path=Path('20260910T120000-'+'b'*32+'.yaml')
    if mutation=='bytes': r['proposal_b64']='!'
    with pytest.raises(InvalidActionReceipt): parse_action_receipt(path,json.dumps(r).encode())

@pytest.mark.parametrize('value',[True,1.0])
def test_outer_manifest_types_are_closed(value):
    r=json.loads(receipt_bytes());r['manifest'][0]['parent_identity'][0]=value
    with pytest.raises(InvalidActionReceipt):parse_action_receipt(Path(ID+'.yaml'),json.dumps(r).encode())


@pytest.mark.parametrize('prefix', ['sample//outbox/.receipts/', './sample/outbox/.receipts/',
                                   'sample/outbox/./.receipts/'])
def test_rollback_reference_requires_canonical_path(prefix):
    from app.lifecycle_receipts import parse_proposal
    p = proposal()
    p['action'] = 'lifecycle_rollback'
    p['manifest'][0]['before_identity'] = [1, 3, 493]
    p['manifest'][0]['after_mode'] = None
    p['repair'] = {'commit': 'a' * 40, 'receipt_blob': 'b' * 40,
                   'receipt_path': prefix + ID + '.yaml'}
    with pytest.raises(InvalidActionReceipt):
        parse_proposal(json.dumps(p).encode())


@pytest.mark.parametrize('field,value', [('manifest', {1: 'integer', 'mixed': 'key'}),
                                        ('registry_sha256', {'unexpected': {1, 2}}),
                                        ('proposal_b64', {}), ('proposal_b64', True)])
def test_malformed_structured_receipt_fields_raise_domain_error(field, value):
    import yaml
    r = json.loads(receipt_bytes())
    r[field] = value
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(ID + '.yaml'), yaml.safe_dump(r).encode())


def test_full_receipt_path_binds_embedded_entity():
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path('other/outbox/.receipts') / (ID + '.yaml'), receipt_bytes())


@pytest.mark.parametrize('operation', ['lookup', 'validate'])
def test_committed_wrong_entity_receipt_is_invalid(tmp_path, operation):
    import subprocess
    from app.action_receipts import resolve_head_receipt, validate_head_receipt_store
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init', '-q')
    git('config', 'user.name', 'Synthetic Test')
    git('config', 'user.email', 'test@example.invalid')
    target = tmp_path / 'other/outbox/.receipts' / (ID + '.yaml')
    target.parent.mkdir(parents=True)
    target.write_bytes(receipt_bytes())
    git('add', '.')
    git('commit', '-qm', 'Synthetic receipt fixture')
    if operation == 'lookup':
        result = resolve_head_receipt(tmp_path, 'other', ID)
        assert result.receipt is None
        assert isinstance(result.error, InvalidActionReceipt)
    else:
        with pytest.raises(InvalidActionReceipt):
            validate_head_receipt_store(tmp_path, 'other')
