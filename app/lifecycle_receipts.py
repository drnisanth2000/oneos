"""Closed, exact-byte lifecycle proposals and retained version-two receipts."""
from __future__ import annotations

from .console_routing import structured_reader

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import yaml

from .action_receipts import ActionReceipt, InvalidActionReceipt, _UniqueKeyLoader
from .proposal_identity import require_proposal_identity, require_proposal_id

ACTIONS = frozenset({'lifecycle_repair', 'lifecycle_rollback'})
REGISTRIES = ('_system/entities.yaml', '_system/archetypes.yaml', '_system/scripts/action-policy.yaml')
BOUND = frozenset({'entity','baseline_head','vault_identity','registry_sha256','shape_sha256','manifest','repair'})
PROPOSAL_FIELDS = BOUND | {'version','id','action','created'}
RECEIPT_FIELDS = BOUND | {'version','proposal_id','action_kind','review_sha256','proposal_b64'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
EMPTY_SHA = hashlib.sha256(b'').hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',',':'), ensure_ascii=True).encode('ascii')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidActionReceipt('invalid lifecycle receipt evidence')


def _hash(value: object, *, oid: bool = False) -> None:
    _require(isinstance(value,str) and (OID if oid else HEX).fullmatch(value) is not None)


def _identity(value: object) -> None:
    _require(isinstance(value,list) and len(value)==3)
    _require(all(type(x) is int and x>=0 for x in value))
    _require(value[2] <= 0o7777)


@structured_reader(category="proposal")
def load_mapping(contents: bytes) -> dict:
    try:
        _require(type(contents) is bytes and len(contents)<=4*1024*1024)
        value=yaml.load(contents.decode('utf-8'),Loader=_UniqueKeyLoader)
        _require(isinstance(value,dict))
        return value
    except (UnicodeError,yaml.YAMLError,TypeError,ValueError) as exc:
        raise InvalidActionReceipt('invalid lifecycle receipt encoding') from exc


def parse_proposal(contents: bytes, path: Path | None = None) -> dict:
    p=load_mapping(contents)
    _require(set(p)==PROPOSAL_FIELDS and type(p['version']) is int and p['version']==1)
    try:
        require_proposal_id(p['id'])
        if path is not None: require_proposal_identity(path,p['id'])
    except ValueError as exc:
        raise InvalidActionReceipt('invalid lifecycle proposal identity') from exc
    _require(isinstance(p['action'],str) and p['action'] in ACTIONS)
    _require(isinstance(p['entity'],str) and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',p['entity']) is not None)
    _require(isinstance(p['created'],str) and p['created'].replace('-','').replace(':','')==p['id'].split('-')[0])
    _hash(p['baseline_head'],oid=True); _hash(p['vault_identity']); _hash(p['shape_sha256'])
    regs=p['registry_sha256']; _require(isinstance(regs,dict) and set(regs)==set(REGISTRIES))
    for h in regs.values(): _hash(h)
    manifest=p['manifest']; _require(isinstance(manifest,list) and 0<len(manifest)<=1024)
    paths=[]
    for entry in manifest:
        _require(isinstance(entry,dict) and set(entry)=={'path','declaration','parent_identity','before_identity','after_mode','placeholder_sha256'})
        path_value=entry['path']; _require(isinstance(path_value,str))
        parts=PurePosixPath(path_value).parts
        _require(len(parts)==3 and parts[0]==p['entity'] and PurePosixPath(path_value).as_posix()==path_value)
        _require(all(re.fullmatch(r'[a-z0-9_]+(?:-[a-z0-9_]+)*',part) is not None and part not in {'.git','outbox','staging','.sensitive'} for part in parts))
        _identity(entry['parent_identity'])
        _require(isinstance(entry['declaration'],str) and 0<len(entry['declaration'])<256)
        _require(entry['placeholder_sha256']==EMPTY_SHA)
        if p['action']=='lifecycle_repair':
            _require(entry['before_identity'] is None and type(entry['after_mode']) is int and entry['after_mode']==0o755)
        else:
            _identity(entry['before_identity']); _require(entry['after_mode'] is None)
        paths.append(path_value)
    _require(paths==sorted(set(paths)))
    if p['action']=='lifecycle_repair':
        _require(p['repair'] is None)
    else:
        ref=p['repair']; _require(isinstance(ref,dict) and set(ref)=={'commit','receipt_path','receipt_blob'})
        _hash(ref['commit'],oid=True); _hash(ref['receipt_blob'],oid=True)
        _require(isinstance(ref['receipt_path'],str))
        parts=PurePosixPath(ref['receipt_path']).parts
        _require(PurePosixPath(ref['receipt_path']).as_posix()==ref['receipt_path'])
        _require(len(parts)==4 and parts[0]==p['entity'] and parts[1:3]==('outbox','.receipts'))
        try: require_proposal_identity(Path(parts[-1]),Path(parts[-1]).stem)
        except ValueError as exc: raise InvalidActionReceipt('invalid original receipt identity') from exc
    return p


@dataclass(frozen=True)
class LifecycleReceipt(ActionReceipt):
    proposal_bytes: bytes

    @property
    def proposal(self) -> dict:
        return parse_proposal(self.proposal_bytes)


def render_lifecycle_receipt(proposal_bytes: bytes) -> bytes:
    p=parse_proposal(proposal_bytes)
    r={k:p[k] for k in BOUND}
    r.update(version=2,proposal_id=p['id'],action_kind=p['action'],review_sha256=digest(proposal_bytes),
             proposal_b64=base64.b64encode(proposal_bytes).decode('ascii'))
    return canonical(r)+b'\n'


def parse_lifecycle_receipt(path: Path, contents: bytes) -> LifecycleReceipt:
    r=load_mapping(contents)
    _require(set(r)==RECEIPT_FIELDS and type(r['version']) is int and r['version']==2)
    try:
        raw=base64.b64decode(r['proposal_b64'],validate=True)
        p=parse_proposal(raw,path)
        # Python equality conflates bool/int/float; canonical evidence does not.
        _require(canonical({k:r[k] for k in BOUND}) == canonical({k:p[k] for k in BOUND}))
        if len(path.parts) > 1:
            _require(path.parts[-4:] == (p['entity'], 'outbox', '.receipts', p['id'] + '.yaml'))
        _require(r['proposal_id']==p['id'] and r['action_kind']==p['action'] and r['review_sha256']==digest(raw))
    except (TypeError,ValueError) as exc:
        raise InvalidActionReceipt('invalid lifecycle receipt correlation') from exc
    return LifecycleReceipt(2,p['id'],r['review_sha256'],p['action'],raw)
