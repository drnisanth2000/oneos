"""Triage core (spec §10 step 6, §8.5): rule-based classification + inbox reader.

Classification is deterministic (no LLM in the request path). Corrections
persist as rules, so the next similar item arrives pre-classified. Instance-
agnostic: synthetic vault + invented slugs.
"""
import textwrap

import pytest

from app.entities import EntityCatalog
from app.scope import CrossScopeError, Scope
from app.vault import Vault
from app.classifier import Classifier
from app.inbox import read_inbox
from tests.conftest import write_vault

ARCH = textwrap.dedent(
    """
    version: "2.0"
    flags: {personal: "p"}
    modules:
      00-inbox:     { block: system, core: true }
      11-knowledge: { block: govern }
      07-finance:   { block: finance, core: true }
    submodules:
      11-knowledge: { kb: { name: KB } }
      07-finance:   { ar: { name: AR } }
    archetypes: { personal: { personal: true } }
    """
).strip()

RULES = textwrap.dedent(
    """
    version: "1.0"
    rules:
      - id: invoices
        match: { any: [invoice, receipt], source: folder }
        route: { module: 07-finance, sub: ar }
      - id: papers
        match: { any: [study, protocol] }
        route: { module: 11-knowledge, sub: kb }
    default: { module: 00-inbox, sub: triage }
    """
).strip()


def _vault(tmp_path, with_rules=True):
    root = write_vault(tmp_path, 'version: "1.0"\nentities:\n  acme: {label: Acme, flags: [personal]}\n', ARCH)
    if with_rules:
        (root / "_system" / "classifier").mkdir(parents=True, exist_ok=True)
        (root / "_system" / "classifier" / "rules.yaml").write_text(RULES)
    return root


def _inbox_note(root, name, title, summary, source="folder"):
    d = root / "acme" / "00-inbox" / "active"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"---\ntype: inbox-item\ntitle: {title}\nentity: acme\nproduct: null\n"
        f"status: active\ncreated: 2026-01-01\nupdated: 2026-01-01\nsub: triage\n"
        f"source: {source}\n---\n{summary}\n"
    )


def test_classify_matches_rule_and_derives_block(tmp_path):
    root = _vault(tmp_path)
    clf = Classifier(Vault(EntityCatalog.load(root)))
    c = clf.classify(title="March invoice", summary="amount due", source="folder")
    assert c.module == "07-finance" and c.sub == "ar"
    assert c.block == "finance"          # derived from the module, not stored
    assert c.rule_id == "invoices"
    assert c.confident is True


def test_source_constraint_respected(tmp_path):
    root = _vault(tmp_path)
    clf = Classifier(Vault(EntityCatalog.load(root)))
    # 'invoice' keyword but wrong source -> the invoices rule must not fire
    c = clf.classify(title="invoice idea", summary="", source="email")
    assert c.rule_id != "invoices"


def test_unmatched_item_is_unconfident_default(tmp_path):
    root = _vault(tmp_path)
    clf = Classifier(Vault(EntityCatalog.load(root)))
    c = clf.classify(title="random musing", summary="hello", source="folder")
    assert c.module == "00-inbox" and c.sub == "triage"
    assert c.confident is False


def test_no_rules_file_defaults_everything(tmp_path):
    root = _vault(tmp_path, with_rules=False)
    clf = Classifier(Vault(EntityCatalog.load(root)))
    c = clf.classify(title="invoice", summary="", source="folder")
    assert c.confident is False and c.module == "00-inbox"


def test_add_rule_persists_and_next_item_matches(tmp_path):
    root = _vault(tmp_path, with_rules=False)
    clf = Classifier(Vault(EntityCatalog.load(root)))
    clf.add_rule(keywords=["recipe"], module="11-knowledge", sub="kb", source="folder")
    # a fresh classifier (reloading from disk) now classifies it
    clf2 = Classifier(Vault(EntityCatalog.load(root)))
    c = clf2.classify(title="dinner recipe", summary="", source="folder")
    assert c.module == "11-knowledge" and c.sub == "kb" and c.confident is True


def test_read_inbox_returns_triage_items_with_proposals(tmp_path):
    root = _vault(tmp_path)
    _inbox_note(root, "a.md", "March invoice", "amount due", "folder")
    _inbox_note(root, "b.md", "random musing", "hello", "folder")
    items = read_inbox(Scope(root, "acme"))
    assert {i.title for i in items} == {"March invoice", "random musing"}
    assert all(i.fm.get("sub") == "triage" for i in items)


def test_read_inbox_rejects_cross_scope_leaf_symlink(tmp_path):
    root = _vault(tmp_path)
    outside = root / "outside.md"
    outside.write_text(
        "---\ntype: inbox-item\ntitle: outside marker\nsub: triage\n---\noutside body\n",
        encoding="utf-8",
    )
    inbox = root / "acme/00-inbox/active"
    inbox.mkdir(parents=True)
    (inbox / "linked.md").symlink_to(outside)

    with pytest.raises(CrossScopeError):
        read_inbox(Scope(root, "acme"))
