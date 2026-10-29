"""Master view reader over a synthetic vault. No live registry, no instance names."""
import hashlib
import importlib
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

from app.master import load_master
from app.scope import CrossScopeError
from tests.conftest import entities_yaml, scaffold_modules, write_vault


def _vault(tmp_path, monkeypatch, archetypes=None):
    if archetypes is None:
        write_vault(tmp_path, entities_yaml("alpha", "beta1"))
    else:
        write_vault(tmp_path, entities_yaml("alpha", "beta1"), archetypes)
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    scaffold_modules(tmp_path, "beta1", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    main = importlib.reload(main)
    return main, main.Vault(main.current_catalog())


def _note(root: Path, entity: str, module: str, name: str, body: str = "Body", **fields) -> Path:
    front = {
        "type": "note",
        "title": name,
        "entity": entity,
        "product": "null",
        "status": "open",
        "created": "2026-01-01",
        "updated": "2026-01-02",
        "sub": "records",
    }
    front.update(fields)
    lines = ["---"]
    for key, value in front.items():
        lines.append(f"{key}: {value}")
    lines.extend(("---", body))
    path = root / entity / module / "active" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# The default synthetic registry has no 02-work submodule; a classify proposal
# needs a registered destination, so the proposal test adds one.
_ARCHETYPES_WITH_SUB = textwrap.dedent(
    """
    version: "2.0"
    flags:
      special: "Activates the extra module"
      other:   "Some other capability"
    modules:
      00-intake:  { block: system, core: true }
      01-core:    { block: govern, core: true }
      02-work:    { block: build }
      zz-extra:   { block: self, core: true, requires_flag: special }
    submodules:
      00-intake:
        triage: { name: "Triage" }
      02-work:
        general: { name: "General" }
    archetypes:
      plain:   { }
      special: { special: true }
    """
).strip()


def _git_commit_all(root: Path) -> None:
    (root / ".gitignore").write_text("*/outbox/*.yaml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True)


def _inbox_with_proposal(root: Path, entity: str, name: str) -> None:
    """One inbox note and one pending classify proposal for it (as in test_app's client)."""
    (root / entity / "02-work" / "active").mkdir(parents=True, exist_ok=True)
    path = root / entity / "00-inbox" / "active" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: inbox-item\ntitle: {entity} proposed\nsub: triage\n---\nbody\n",
        encoding="utf-8",
    )
    outbox = root / entity / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    proposal_id = "20260815T090703-" + "11" * 16
    (outbox / f"{proposal_id}.yaml").write_text(
        "\n".join(
            (
                f"id: {proposal_id}",
                "action: classify",
                f"entity: {entity}",
                f"src: {entity}/00-inbox/active/{name}",
                f"source_sha256: {hashlib.sha256(path.read_bytes()).hexdigest()}",
                f"dst: {entity}/02-work/active/{name}",
                "module: 02-work",
                "sub: general",
                "block: build",
                "status: pending",
                "",
            )
        ),
        encoding="utf-8",
    )
    _git_commit_all(root)


def test_cards_land_in_their_stage_across_entities(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "beta1", "02-work", "Filing", status=" Waiting ")
    _note(tmp_path, "alpha", "01-core", "Old", status="active")
    _note(tmp_path, "beta1", "01-core", "Closed", status="DONE")
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    _note(tmp_path, "alpha", "00-inbox", "New mail", sub="triage", title="New mail")
    page = load_master(vault, main.build_scope, entities=(), sort="updated",
                       done_all=False, now=datetime(2026, 10, 29, 9, 12))
    titles = {c.key: [m.card.title for m in c.cards] for c in page.columns}
    assert [c.key for c in page.columns] == ["draft", "waiting", "review", "done"]
    assert [c.label for c in page.columns] == ["Draft", "Waiting", "To review", "Done"]
    assert titles["draft"] == ["Spec"] and titles["waiting"] == ["Filing"]
    assert titles["review"] == ["New mail"]
    assert sorted(titles["done"]) == ["Closed", "Old"]
    assert [m.card.title for m in page.other] == ["Odd"]
    assert {m.entity_label for m in page.columns[1].cards} == {"Beta1"}


def test_greeting_and_date(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 16, 59))
    assert page.greeting == "Good afternoon"
    assert page.date_label == "Thursday, 29 October 2026"
    assert page.time_label == "16:59"


def test_entity_filter_and_done_limit(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    _note(tmp_path, "beta1", "02-work", "Beta done", status="done")
    page = load_master(vault, main.build_scope, entities=("alpha",), sort="updated",
                       done_all=False, now=datetime(2026, 10, 29, 9))
    done = page.columns[3]
    assert done.total == 12 and len(done.cards) == 10
    assert done.cards[0].card.title == "Done 11"
    assert all(m.entity == "alpha" for m in done.cards)
    assert [c.active for c in page.chips] == [True, False]
    assert page.done_all_href == "/?entity=alpha&sort=updated&done=all"


def test_proposed_inbox_item_appears_once(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md")
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
    review = page.columns[2].cards
    assert len(review) == 1 and review[0].card.lane == "proposed"


def test_unreadable_entity_is_reported_not_fatal(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "beta1", "02-work", "Still here", status="draft")

    def scope_for(slug):
        if slug == "alpha":
            raise main.CrossScopeError("synthetic")
        return main.build_scope(slug)

    page = load_master(vault, scope_for, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
    assert [m.card.title for m in page.columns[0].cards] == ["Still here"]
    assert [f.entity_label for f in page.failures] == ["Alpha"]
    assert isinstance(page.failures[0].error, CrossScopeError)


def test_sort_by_title_and_done_stays_newest_first(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Zed", status="draft", updated="2026-10-01")
    _note(tmp_path, "beta1", "02-work", "Aye", status="draft", updated="2026-10-02")
    _note(tmp_path, "alpha", "02-work", "Old done", status="done", updated="2026-01-01")
    _note(tmp_path, "beta1", "02-work", "New done", status="done", updated="2026-09-01")
    by_title = load_master(vault, main.build_scope, entities=(), sort="title",
                           done_all=False, now=datetime(2026, 10, 29, 9))
    assert [m.card.title for m in by_title.columns[0].cards] == ["Aye", "Zed"]
    assert [m.card.title for m in by_title.columns[3].cards] == ["New done", "Old done"]
    by_updated = load_master(vault, main.build_scope, entities=(), sort="bogus",
                             done_all=False, now=datetime(2026, 10, 29, 9))
    assert by_updated.sort == "updated"
    assert [m.card.title for m in by_updated.columns[0].cards] == ["Aye", "Zed"]
    assert [(link.label, link.active) for link in by_title.sort_links] == [
        ("Updated", False), ("Title", True)
    ]


def test_done_all_shows_every_card_and_links_keep_other_params(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    page = load_master(vault, main.build_scope, entities=("alpha", "nope"), sort="title",
                       done_all=True, now=datetime(2026, 10, 29, 9))
    assert page.columns[3].total == 12 and len(page.columns[3].cards) == 12
    assert page.done_all is True
    assert [c.slug for c in page.chips] == ["alpha", "beta1"]
    assert [c.tone for c in page.chips] == [1, 2]
    assert page.chips[0].href == "/?sort=title&done=all"
    assert page.chips[1].href == "/?entity=alpha&entity=beta1&sort=title&done=all"
    assert [link.href for link in page.sort_links] == [
        "/?entity=alpha&sort=updated&done=all",
        "/?entity=alpha&sort=title&done=all",
    ]


def test_master_reads_without_writing(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _inbox_with_proposal(tmp_path, "beta1", "marker.md")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=True,
                       now=datetime(2026, 10, 29, 9))
    assert page.failures == () and len(page.columns[2].cards) == 1
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
