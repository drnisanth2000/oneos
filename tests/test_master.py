"""Master view reader over a synthetic vault. No live registry, no instance names."""
import hashlib
import re
import importlib
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.board import NoteUnreadable
from app.master import due_label, load_master
from app.reminders import Reminder, ReminderStore, ReviewFlag
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


def _inbox_with_proposal(
    root: Path, entity: str, name: str, status: str | None = None, *, full: bool = False
) -> None:
    """One inbox note and one pending classify proposal for it (as in test_app's client).
    `status` adds the front-matter status real ingest writes (`active`); `full`
    writes every field the vault's schema requires, so the note can be edited."""
    (root / entity / "02-work" / "active").mkdir(parents=True, exist_ok=True)
    path = root / entity / "00-inbox" / "active" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    required = (
        f"entity: {entity}\nproduct: null\ncreated: 2026-01-01\nupdated: 2026-01-02\n" if full else ""
    )
    path.write_text(
        f"---\ntype: inbox-item\ntitle: {entity} proposed\nsub: triage\n"
        + (f"status: {status}\n" if status else "")
        + required
        + "---\nbody\n",
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
                       done_all=False, now=datetime(2026, 10, 29, 9, 12, tzinfo=timezone.utc), reminders={})
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
                       now=datetime(2026, 10, 29, 16, 59, tzinfo=timezone.utc), reminders={})
    assert page.greeting == "Good afternoon"
    assert page.date_label == "Thursday, 29 October 2026"
    assert page.time_label == "16:59"


def test_entity_filter_and_done_limit(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    _note(tmp_path, "beta1", "02-work", "Beta done", status="done")
    page = load_master(vault, main.build_scope, entities=("alpha",), sort="updated",
                       done_all=False, now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    done = page.columns[3]
    assert done.total == 12 and len(done.cards) == 10
    assert done.cards[0].card.title == "Done 11"
    assert all(m.entity == "alpha" for m in done.cards)
    assert [c.active for c in page.chips] == [True, False]
    assert page.done_all_href == "/?entity=alpha&sort=updated&done=all"


def test_an_unmatched_selection_shows_every_entity(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "beta1", "02-work", "Filing", status="draft")
    page = load_master(vault, main.build_scope, entities=("nope",), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert {m.entity for m in page.columns[0].cards} == {"alpha", "beta1"}
    assert [c.active for c in page.chips] == [False, False]


def test_proposed_inbox_item_appears_once(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md")
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
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
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert [m.card.title for m in page.columns[0].cards] == ["Still here"]
    assert [f.entity_label for f in page.failures] == ["Alpha"]
    assert isinstance(page.failures[0].error, CrossScopeError)


def _break_note_encoding(path: Path) -> None:
    path.write_bytes(b"---\ntitle: \xff\xfe broken\nstatus: draft\n---\n\xc3\x28\n")


def test_note_with_invalid_utf8_fails_only_its_entity(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _break_note_encoding(_note(tmp_path, "alpha", "02-work", "Garbled", status="draft"))
    _note(tmp_path, "beta1", "02-work", "Still here", status="draft")
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert [f.entity_label for f in page.failures] == ["Alpha"]
    error = page.failures[0].error
    assert isinstance(error, NoteUnreadable)
    assert isinstance(error.__cause__, UnicodeDecodeError)
    assert [m.card.title for m in page.columns[0].cards] == ["Still here"]


def test_oserror_reading_a_note_fails_only_its_entity(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    locked = _note(tmp_path, "alpha", "02-work", "Locked", status="draft")
    _note(tmp_path, "beta1", "02-work", "Still here", status="draft")
    real_read = Path.read_bytes  # the card reader reads bytes so it can hash what it parsed

    def read_bytes(self, *args, **kwargs):
        if self == locked or self.resolve() == locked.resolve():
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert [f.entity_label for f in page.failures] == ["Alpha"]
    error = page.failures[0].error
    assert isinstance(error, NoteUnreadable)
    assert isinstance(error.__cause__, PermissionError)
    assert [m.card.title for m in page.columns[0].cards] == ["Still here"]


def test_home_survives_an_unreadable_note_and_describes_it(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _break_note_encoding(_note(tmp_path, "alpha", "02-work", "Garbled", status="draft"))
    _note(tmp_path, "beta1", "02-work", "Filing", status="waiting")
    response = client.get("/")
    assert response.status_code == 200
    assert "Filing" in response.text
    blocks = re.findall(r'<section class="master-failure".*?</section>', response.text, re.S)
    assert len(blocks) == 1
    assert "Alpha" in blocks[0] and 'class="alert' in blocks[0] and "E-UNAVAILABLE" in blocks[0]
    assert "E-UNKNOWN" not in blocks[0] and "could not be read" in blocks[0]
    # Described by class only: no path, byte value or exception text reaches the page.
    for leaked in (str(tmp_path), "Garbled.md", "codec", "0xff", "Traceback"):
        assert leaked not in response.text


def test_sort_by_title_and_done_stays_newest_first(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Zed", status="draft", updated="2026-10-01")
    _note(tmp_path, "beta1", "02-work", "Aye", status="draft", updated="2026-10-02")
    _note(tmp_path, "alpha", "02-work", "Old done", status="done", updated="2026-01-01")
    _note(tmp_path, "beta1", "02-work", "New done", status="done", updated="2026-09-01")
    by_title = load_master(vault, main.build_scope, entities=(), sort="title",
                           done_all=False, now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert [m.card.title for m in by_title.columns[0].cards] == ["Aye", "Zed"]
    assert [m.card.title for m in by_title.columns[3].cards] == ["New done", "Old done"]
    by_updated = load_master(vault, main.build_scope, entities=(), sort="bogus",
                             done_all=False, now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
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
                       done_all=True, now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
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


def test_master_reads_without_writing(tmp_path, tmp_path_factory, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _note(tmp_path, "alpha", "00-inbox", "bare", sub="triage", status="active")
    _inbox_with_proposal(tmp_path, "beta1", "marker.md")
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)))
    ReminderStore(directory).flag(_flag("alpha", "Spec"))

    def snapshot():
        return {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for root in (tmp_path, directory)
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts
        }

    before = snapshot()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=True,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert page.failures == () and len(page.columns[2].cards) == 2
    # Page load, filter, sort, refresh, and opening (then closing) every popup.
    client = TestClient(main.app)
    for url in ("/", "/?entity=alpha", "/?sort=title", "/?done=all", "/?entity=beta1&sort=updated"):
        assert client.get(url).status_code == 200
    for entity in ("alpha", "beta1"):
        cards = main.entity_cards(main.build_scope(entity), main.Vault(main.current_catalog()))
        for card in (*cards.proposed, *cards.inbox, *cards.filed):
            popup = client.get(f"/master/card/{entity}", params={"path": card.relative_path})
            assert popup.status_code == 200 and "stage-form" in popup.text
    assert snapshot() == before
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout == head


def _client(tmp_path, monkeypatch):
    main, _vault_ = _vault(tmp_path, monkeypatch)
    return main, TestClient(main.app)


def _card_path(main, entity, title):
    cards = main.entity_cards(main.build_scope(entity), main.Vault(main.current_catalog()))
    return next(c.relative_path for c in (*cards.inbox, *cards.proposed, *cards.filed) if c.title == title)


def test_home_is_the_master_view(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    assert "Wiring check" not in html
    for label in ("Draft", "Waiting", "To review", "Done"):
        assert f">{label}<" in html
    assert html.index(">Draft<") < html.index(">Waiting<") < html.index(">To review<") < html.index(">Done<")
    assert 'hx-swap="morph"' in html


def test_home_lists_cards_and_survives_a_failing_entity(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "beta1", "02-work", "Filing", status="waiting")
    html = client.get("/").text
    assert "Spec" in html and "Filing" in html
    assert "<form" not in html and "hx-post" not in html
    real = main.build_scope

    def scope_for(slug):
        if slug == "alpha":
            raise main.CrossScopeError("synthetic")
        return real(slug)

    monkeypatch.setattr(main, "build_scope", scope_for)
    degraded = client.get("/")
    assert degraded.status_code == 200
    assert "Filing" in degraded.text
    blocks = re.findall(r'<section class="master-failure".*?</section>', degraded.text, re.S)
    assert len(blocks) == 1
    # The entity label and the described code sit in the same failure block.
    assert "Alpha" in blocks[0] and 'class="alert' in blocks[0]
    assert "E-UNKNOWN" in blocks[0]


def test_popup_shows_the_note_body_escaped_in_its_editor(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", body="Decision <b>text</b>", status="draft")
    path = _card_path(main, "alpha", "Spec")
    response = client.get("/master/card/alpha", params={"path": path})
    assert response.status_code == 200
    assert "Decision &lt;b&gt;text&lt;/b&gt;" in response.text and ">Draft<" in response.text
    assert ">Alpha<" in response.text and "ent-1" in response.text
    assert "<b>text</b>" not in response.text
    # Master view 3 made the popup editable (spec decision 8): its one form
    # posts the stage buttons to /master/stage, and the body is a textarea.
    # Opening it still writes nothing (test_master_reads_without_writing).
    assert response.text.count("<form") == 1 and response.text.count("<textarea") == 1
    assert re.findall(r'hx-post="([^"]*)"', response.text) == ["/master/stage/alpha"]


def test_popup_labels_other_statuses(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    odd = client.get("/master/card/alpha", params={"path": _card_path(main, "alpha", "Odd")}).text
    assert ">Another status<" in odd and ">Draft<" not in odd


def test_popup_refuses_paths_outside_the_entity(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    beta = _note(tmp_path, "beta1", "02-work", "Beta")
    old = tmp_path / "alpha" / "02-work" / "archive" / "old.md"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("---\ntitle: Old\nstatus: draft\n---\nArchived body\n", encoding="utf-8")
    beta_path = _card_path(main, "beta1", "Beta")
    refused = (f"../{beta_path}", f"../beta1/{beta_path}", beta_path,
               "beta1/02-work/active/Beta.md", "alpha/02-work/archive/old.md",
               "02-work/archive/old.md", "")
    assert beta.is_file() and old.is_file()
    read = []

    def no_body(scope, path):
        read.append(path)
        raise AssertionError(f"note_snapshot read a refused path: {path}")

    monkeypatch.setattr(main, "note_snapshot", no_body)
    for path in refused:
        html = client.get("/master/card/alpha", params={"path": path}).text
        assert "no longer here" in html, path
        assert "Body" not in html and "Archived body" not in html
    assert read == []


def test_home_survives_a_deleted_manifest(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    (tmp_path / "_system/entities.yaml").unlink()
    assert client.get("/").status_code == 200


def test_home_cards_open_the_popup_and_filters_are_links(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    html = client.get("/").text
    assert 'hx-get="/master/card/alpha?path=' in html
    assert 'id="master-popup-dialog"' in html
    assert 'href="/?entity=alpha' in html          # entity chip
    assert "<form" not in html.split('id="master"')[1].split("</main>")[0]  # nothing posts


def test_other_statuses_and_failures_render(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    outside = tmp_path / "outside"
    _note(outside, "other", "02-work", "Secretnote")
    (tmp_path / "beta1" / "02-work" / "active").symlink_to(
        outside / "other" / "02-work" / "active", target_is_directory=True)
    response = client.get("/")
    assert response.status_code == 200                      # one bad entity never blanks home
    assert "1 note with another status" in response.text
    assert "E-TAMPER" in response.text and "Beta1" in response.text
    assert "Secretnote" not in response.text


def test_sidebar_has_home_row_marked_current(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    assert 'href="/"' in html and 'aria-current="page"' in html.split("Workspaces")[1][:600]


def test_home_row_is_not_current_off_home(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/board/alpha").text
    row = html.split("Workspaces")[1].split("</li>")[0]
    assert 'href="/"' in row and "aria-current" not in row and "is-current" not in row


def test_refresh_wrapper_uses_a_relative_url(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    for url, expected in (("/", "/"), ("/?entity=alpha&sort=title", "/?entity=alpha&amp;sort=title")):
        html = client.get(url).text
        tag = re.search(r'<div id="master"[^>]*>', html).group(0)
        hx_get = re.search(r'hx-get="([^"]*)"', tag).group(1)
        assert hx_get.startswith("/") and hx_get == expected
        assert 'hx-trigger="every 60s, master-refresh from:body"' in tag and 'hx-select="#master"' in tag
        assert 'hx-swap="morph"' in tag
    # The popup dialog sits outside the refreshed wrapper, so a refresh never closes it.
    html = client.get("/").text
    master = html.split('<div id="master"')[1]
    assert html.index('id="master-popup-dialog"') > html.index('<div id="master"')
    assert 'id="master-popup-dialog"' not in master.split("<!-- /master -->")[0]


def test_home_columns_placeholders_chips_and_show_all(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    for text in ("No drafts", "Nothing waiting", "Nothing to review", "Nothing done yet"):
        assert text in html
    assert "another status" not in html                      # no Other block when none
    assert 'class="segmented' in html and ">Updated<" in html and ">Title<" in html
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    html = client.get("/").text
    assert "Show all 12" in html and 'href="/?sort=updated&amp;done=all"' in html
    assert 'class="ent ent-1"' in html                       # alpha's tone on its cards
    all_chip = re.search(r'<a[^>]*>\s*All\s*</a>', html).group(0)
    assert "is-active" in all_chip
    filtered = client.get("/?entity=alpha&done=all").text
    assert "Show all" not in filtered
    all_chip = re.search(r'<a[^>]*>\s*All\s*</a>', filtered).group(0)
    assert "is-active" not in all_chip and 'href="/?sort=updated&amp;done=all"' in all_chip


def test_home_icons_all_exist_in_the_sprite(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    html = client.get("/").text
    defined = set(re.findall(r'<symbol id="(i-[a-z-]+)"', html))
    used = set(re.findall(r'<use href="#(i-[a-z-]+)"', html))
    assert "i-home" in used and used <= defined, used - defined


def test_popup_of_a_proposed_card_shows_its_destination(tmp_path, monkeypatch):
    main, _vault_ = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    client = TestClient(main.app)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md")
    path = _card_path(main, "alpha", "alpha proposed")
    html = client.get("/master/card/alpha", params={"path": path}).text
    assert "proposed for 02-work / general" in html
    assert ">To review<" in html


def test_greeting_date_and_clock_are_set_in_the_browser(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    head = re.search(r'<header class="master-head".*?</header>', html, re.S).group(0)
    # Server strings stay as the first paint ...
    assert re.search(r"<h1[^>]*>Good (morning|afternoon|evening)</h1>", head)
    assert re.search(r"\w+day, \d{1,2} \w+ \d{4}", head)
    assert re.search(r"<time[^>]*>\d\d:\d\d</time>", head)
    # ... and the browser's own clock replaces them.
    assert 'x-text="greeting"' in head and 'x-text="date"' in head and 'x-text="clock"' in head
    assert "toLocaleDateString('en-GB'" in html
    assert "Good morning" in html and "Good afternoon" in html and "Good evening" in html
    # x-data is static, so the minute morph never rewrites the attribute.
    assert re.search(r'<header class="master-head"\s+x-data="masterHead"', head)


def test_refresh_failure_status_is_hidden_until_needed(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    status = re.search(r'<p[^>]*id="master-stale"[^>]*>', html).group(0)
    assert "hidden" in status or "x-show" in status
    assert 'role="status"' in status
    assert "htmx:before-swap" in html or "htmx:beforeSwap" in html
    assert "shouldSwap = false" in html


def test_done_all_offers_a_way_back_to_recent(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    recent = client.get("/").text
    assert "Show recent" not in recent
    html = client.get("/?entity=alpha&sort=title&done=all").text
    assert "Show recent" in html
    assert 'href="/?entity=alpha&amp;sort=title"' in re.search(
        r'<a[^>]*class="master-show-all"[^>]*>\s*Show recent', html).group(0)
    assert "Show all" not in html
    page = load_master(main.Vault(main.current_catalog()), main.build_scope, entities=("alpha",),
                       sort="title", done_all=True, now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert page.done_recent_href == "/?entity=alpha&sort=title"


def _arm_body_failure(main, monkeypatch, error):
    """Let the listing read the note, then make only the popup's one capture of
    it fail: unreadable (permission) or not UTF-8, on the real file."""
    import app.board as board

    state = {"armed": False}
    real_cards, real_capture = main.entity_cards, board._capture_source_state

    def cards(*args, **kwargs):
        try:
            return real_cards(*args, **kwargs)
        finally:
            state["armed"] = True

    def capture(root, relative):
        if not state["armed"]:
            return real_capture(root, relative)
        target = Path(root) / relative
        if isinstance(error, PermissionError):
            mode = target.stat().st_mode
            target.chmod(0)
            try:
                return real_capture(root, relative)
            finally:
                target.chmod(mode)
        original = target.read_bytes()
        target.write_bytes(original + b"\xff")
        try:
            return real_capture(root, relative)
        finally:
            target.write_bytes(original)

    monkeypatch.setattr(main, "entity_cards", cards)
    monkeypatch.setattr(board, "_capture_source_state", capture)


def test_popup_for_an_unreadable_note_is_described_with_a_close_button(tmp_path, monkeypatch):
    for error in (
        PermissionError(13, "Permission denied"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ):
        main, client = _client(tmp_path, monkeypatch)
        _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
        path = _card_path(main, "alpha", "Spec")
        with monkeypatch.context() as patch:
            _arm_body_failure(main, patch, error)
            response = client.get("/master/card/alpha", params={"path": path})
        assert response.status_code == 200
        assert "E-UNAVAILABLE" in response.text and "could not be read" in response.text
        assert "E-UNKNOWN" not in response.text
        assert re.search(
            r"<button type=\"button\" class=\"icon-button\" "
            r"onclick=\"this\.closest\('dialog'\)\.close\(\)\">.*?"
            r"<span class=\"sr-only\">Close</span>\s*</button>",
            response.text, re.S,
        )
        for leaked in (str(tmp_path), "Permission denied", "codec", "0xff", "Traceback"):
            assert leaked not in response.text


def test_popup_for_a_missing_card_has_a_close_button(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    html = client.get("/master/card/alpha", params={"path": "nope.md"}).text
    assert "sr-only\">Close<" in html


def test_card_link_encodes_the_entity_slug(tmp_path, monkeypatch):
    from types import SimpleNamespace

    main, _vault_ = _vault(tmp_path, monkeypatch)
    card = SimpleNamespace(title="T", relative_path="a b/c.md", lane="filed", updated="")
    item = SimpleNamespace(card=card, entity="we ird/slug", entity_label="W", tone=1)
    html = main.templates.env.get_template("blocks/master_card.html").render(item=item)
    assert 'hx-get="/master/card/we%20ird/slug?path=a%20b/c.md"' in html


_CSS = (Path(__file__).resolve().parent.parent / "static" / "app.css").read_text(encoding="utf-8")


def test_phone_stage_switcher_sizes_each_segment_to_its_label():
    # Equal minmax(0, 1fr) tracks cut "To review" at 375px; tracks must not shrink below content.
    rule = re.search(r"  \.master-stages \{[^}]*\}", _CSS).group(0)  # the phone rule
    assert "minmax(max-content, 1fr)" in rule and "minmax(0, 1fr)" not in rule


def test_master_card_has_hover_and_keyboard_focus_feedback():
    rule = re.search(
        r"\.review-surface \.master-card:hover,\s*\.review-surface \.master-card:focus-visible \{[^}]*\}",
        _CSS,
    )
    assert rule and "var(--shadow-md)" in rule.group(0)


def test_popup_for_a_good_note_survives_another_garbled_note(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Good", status="draft")
    path = _card_path(main, "alpha", "Good")
    _break_note_encoding(_note(tmp_path, "alpha", "02-work", "Garbled", status="draft"))
    response = client.get("/master/card/alpha", params={"path": path})
    assert response.status_code == 200
    assert "E-UNAVAILABLE" in response.text and "E-UNKNOWN" not in response.text
    assert re.search(r"this\.closest\('dialog'\)\.close\(\).*?sr-only\">Close<", response.text, re.S)
    for leaked in (str(tmp_path), "Garbled.md", "codec", "0xff", "Traceback"):
        assert leaked not in response.text


def test_board_page_with_a_garbled_filed_note_is_described(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _break_note_encoding(_note(tmp_path, "alpha", "02-work", "Garbled", status="draft"))
    response = client.get("/board/alpha")
    assert "E-UNAVAILABLE" in response.text and "E-UNKNOWN" not in response.text
    for leaked in (str(tmp_path), "Garbled.md", "codec", "0xff", "Traceback"):
        assert leaked not in response.text


def test_board_page_with_an_unreadable_inbox_note_is_described(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _break_note_encoding(_note(tmp_path, "alpha", "00-inbox", "Mail", sub="triage"))
    response = client.get("/board/alpha")
    assert "E-UNAVAILABLE" in response.text and "E-UNKNOWN" not in response.text


# --- reminders -------------------------------------------------------------

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=2)))


def _path(entity: str, name: str) -> str:
    return f"{entity}/02-work/active/{name}.md"


def _reminder(entity: str, name: str, due: datetime, subject: str = "Chase it") -> Reminder:
    key = _path(entity, name)
    return Reminder(key, entity, subject, due)


def _load(vault, main, reminders, **kwargs):
    args = dict(entities=(), sort="updated", done_all=False, now=NOW, reminders=reminders)
    args.update(kwargs)
    return load_master(vault, main.build_scope, **args)


def _by_key(*reminders: Reminder) -> dict[str, Reminder]:
    return {r.key: r for r in reminders}


def test_strip_orders_overdue_then_soon_then_later(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for name in ("A", "B", "C"):
        _note(tmp_path, "alpha", "02-work", name, status="waiting")
    page = _load(vault, main, _by_key(
        _reminder("alpha", "C", NOW + timedelta(days=3)),
        _reminder("alpha", "A", NOW - timedelta(hours=1)),
        _reminder("alpha", "B", NOW + timedelta(hours=2)),
    ))
    assert [a.level for a in page.attention] == ["overdue", "soon", "later"]
    assert [a.card.card.title for a in page.attention] == ["A", "B", "C"]
    assert page.reminders_available is True
    assert page.attention[0].entity_label == "Alpha" and page.attention[0].tone == 1
    assert page.attention[0].unreadable is False


def test_strip_orders_within_a_level_soonest_first(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for name in ("A", "B", "C"):
        _note(tmp_path, "alpha", "02-work", name, status="waiting")
    page = _load(vault, main, _by_key(
        _reminder("alpha", "A", NOW + timedelta(days=5)),
        _reminder("alpha", "B", NOW + timedelta(days=2)),
        _reminder("alpha", "C", NOW + timedelta(days=9)),
    ))
    assert [a.reminder.key for a in page.attention] == [
        _path("alpha", "B"), _path("alpha", "A"), _path("alpha", "C")]


def test_strip_boundaries(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for name in ("Now", "Day", "Past"):
        _note(tmp_path, "alpha", "02-work", name, status="waiting")
    page = _load(vault, main, _by_key(
        _reminder("alpha", "Now", NOW),
        _reminder("alpha", "Day", NOW + timedelta(hours=24)),
        _reminder("alpha", "Past", NOW - timedelta(seconds=1)),
    ))
    levels = {a.reminder.key: a.level for a in page.attention}
    assert levels == {
        _path("alpha", "Now"): "soon",
        _path("alpha", "Day"): "later",
        _path("alpha", "Past"): "overdue",
    }


def test_levels_use_the_absolute_instant_not_the_wall_clock(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Far", status="waiting")
    # 09:30 at -05:00 is 14:30 UTC, after NOW (08:00 UTC) although its wall time is earlier.
    due = datetime(2026, 11, 3, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
    page = _load(vault, main, _by_key(_reminder("alpha", "Far", due)))
    assert page.attention[0].level == "soon"


def test_done_card_resolves_its_reminder(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Closed", status="done")
    _note(tmp_path, "alpha", "02-work", "Filed", status="active")
    _note(tmp_path, "alpha", "02-work", "Open", status="waiting")
    page = _load(vault, main, _by_key(
        _reminder("alpha", "Closed", NOW - timedelta(days=1)),
        _reminder("alpha", "Filed", NOW - timedelta(days=1)),
        _reminder("alpha", "Open", NOW + timedelta(hours=1)),
    ))
    assert [a.card.card.title for a in page.attention] == ["Open"]


def test_moved_card_reminder_stays_visible(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Elsewhere", status="waiting")
    gone = _reminder("alpha", "Gone", NOW - timedelta(hours=1), "Call the bank")
    page = _load(vault, main, _by_key(gone))
    assert len(page.attention) == 1
    entry = page.attention[0]
    assert entry.card is None and entry.unreadable is False
    assert entry.reminder == gone and entry.level == "overdue"
    assert entry.entity_label == "Alpha"


def test_reminder_for_an_entity_not_in_the_registry_is_moved_or_removed(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    ghost = Reminder("ghost/02-work/active/X.md", "ghost", "Old", NOW + timedelta(days=2))
    page = _load(vault, main, _by_key(ghost))
    assert len(page.attention) == 1
    entry = page.attention[0]
    assert entry.card is None and entry.unreadable is False
    assert entry.entity_label == "ghost"


def test_reminder_whose_entity_differs_from_the_card_entity_is_not_attached(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="waiting")
    stray = Reminder(_path("alpha", "Spec"), "beta1", "Stray", NOW + timedelta(hours=1))
    page = _load(vault, main, _by_key(stray))
    assert page.columns[1].cards[0].reminder is None
    assert page.attention[0].card is None


def test_unreadable_entity_reminders_are_not_called_moved(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "beta1", "02-work", "Still here", status="waiting")

    def scope_for(slug):
        if slug == "alpha":
            raise main.CrossScopeError("synthetic")
        return main.build_scope(slug)

    page = load_master(vault, scope_for, entities=(), sort="updated", done_all=False,
                       now=NOW, reminders=_by_key(
                           _reminder("alpha", "Hidden", NOW - timedelta(hours=1)),
                           _reminder("beta1", "Still here", NOW + timedelta(hours=1))))
    by_entity = {a.reminder.entity: a for a in page.attention}
    assert by_entity["alpha"].card is None and by_entity["alpha"].unreadable is True
    assert by_entity["beta1"].card is not None and by_entity["beta1"].unreadable is False


def test_strip_follows_the_entity_filter(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "A", status="waiting")
    _note(tmp_path, "beta1", "02-work", "B", status="waiting")
    both = _by_key(_reminder("alpha", "A", NOW + timedelta(hours=1)),
                   _reminder("beta1", "B", NOW + timedelta(hours=1)))
    page = _load(vault, main, both, entities=("alpha",))
    assert [a.reminder.entity for a in page.attention] == ["alpha"]
    everything = _load(vault, main, both)
    assert {a.reminder.entity for a in everything.attention} == {"alpha", "beta1"}


def test_waiting_and_review_put_reminders_first_soonest_first(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Newest", status="waiting", updated="2026-10-30")
    _note(tmp_path, "alpha", "02-work", "Middle", status="waiting", updated="2026-10-20")
    _note(tmp_path, "alpha", "02-work", "Oldest", status="waiting", updated="2026-10-10")
    _note(tmp_path, "alpha", "02-work", "Zed", status="draft", updated="2026-10-01")
    _note(tmp_path, "alpha", "02-work", "Aye", status="draft", updated="2026-10-02")
    reminders = _by_key(
        _reminder("alpha", "Oldest", NOW + timedelta(days=2)),
        _reminder("alpha", "Middle", NOW + timedelta(days=1)),
        _reminder("alpha", "Zed", NOW - timedelta(days=1)),
    )
    page = _load(vault, main, reminders)
    waiting = [m.card.title for m in page.columns[1].cards]
    assert waiting == ["Middle", "Oldest", "Newest"]
    # Draft is not reordered by reminders.
    assert [m.card.title for m in page.columns[0].cards] == ["Aye", "Zed"]
    by_title = _load(vault, main, reminders, sort="title")
    assert [m.card.title for m in by_title.columns[1].cards] == ["Middle", "Newest", "Oldest"]


def test_review_column_puts_reminders_first(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "Fresh", sub="triage", updated="2026-10-30")
    _note(tmp_path, "alpha", "00-inbox", "Stale", sub="triage", updated="2026-10-01")
    inbox = {m.card.title: m.card.relative_path for m in _load(vault, main, {}).columns[2].cards}
    stale = Reminder(inbox["Stale"], "alpha", "Review soon", NOW + timedelta(hours=3))
    page = _load(vault, main, {stale.key: stale})
    assert [m.card.title for m in page.columns[2].cards] == ["Stale", "Fresh"]
    assert page.attention[0].card.card.title == "Stale"


def test_card_carries_its_reminder(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "A", status="waiting")
    _note(tmp_path, "alpha", "02-work", "B", status="waiting")
    mine = _reminder("alpha", "A", NOW + timedelta(hours=1))
    page = _load(vault, main, _by_key(mine))
    cards = {m.card.title: m for m in page.columns[1].cards}
    assert cards["A"].reminder == mine and cards["B"].reminder is None
    assert page.attention[0].card is cards["A"]


def test_store_unavailable_gives_an_empty_strip(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "A", status="waiting")
    page = _load(vault, main, None)
    assert page.reminders_available is False and page.attention == ()
    assert page.columns[1].cards[0].reminder is None


def test_now_must_be_timezone_aware(tmp_path, monkeypatch):
    import pytest

    main, vault = _vault(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _load(vault, main, {}, now=datetime(2026, 11, 3, 10, 0))


def test_due_label():
    due = datetime(2026, 11, 4, 11, 0, tzinfo=timezone(timedelta(hours=2)))
    assert due_label(due) == "Wed 4 Nov 11:00"


# --- the Needs attention strip on the home page ---------------------------------


def _auth_dir(tmp_path_factory, monkeypatch, mode=0o700):
    directory = tmp_path_factory.mktemp("auth")
    directory.chmod(mode)
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(directory))
    return directory


def _seed(directory, *reminders):
    store = ReminderStore(directory)
    for reminder in reminders:
        store.save(reminder)
    return store


def _strip(html):
    """The Needs attention section's markup, or "" when there is none."""
    if 'id="master-attention"' not in html:
        return ""
    return html.split('id="master-attention"')[1].split("</section>")[0]


def test_home_shows_needs_attention_in_order_with_icons(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Late", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Soonish", status="waiting")
    _note(tmp_path, "beta1", "02-work", "Eventually", status="waiting")
    now = datetime.now(timezone.utc)
    zone = timezone(timedelta(hours=2))
    overdue = (now - timedelta(hours=3)).astimezone(zone).replace(microsecond=0)
    soon = (now + timedelta(hours=3)).astimezone(zone).replace(microsecond=0)
    later = (now + timedelta(days=5)).astimezone(zone).replace(microsecond=0)
    # Saved in reverse of display order, so the order is the strip's own.
    _seed(
        directory,
        _reminder("beta1", "Eventually", later, "Third subject"),
        _reminder("alpha", "Soonish", soon, "Second subject"),
        _reminder("alpha", "Late", overdue, "First subject"),
    )
    strip = _strip(client.get("/").text)
    assert strip.index("First subject") < strip.index("Second subject") < strip.index("Third subject")
    assert strip.index("is-overdue") < strip.index("is-soon") < strip.index("is-later")
    assert strip.index("#i-alarm") < strip.index("#i-bell") < strip.index("#i-calendar")
    for due in (overdue, soon, later):
        assert due_label(due) in strip
    assert "Alpha" in strip and "Beta1" in strip
    assert strip.count("<button") == 3 and strip.count('class="attention-card is-') == 3
    assert strip.count('hx-target="#master-popup"') == 3
    assert "Needs attention" in strip


def test_strip_sits_between_the_header_and_the_filters_inside_the_morph_wrapper(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Late", status="waiting")
    _seed(directory, _reminder("alpha", "Late", datetime.now(timezone.utc) + timedelta(days=2)))
    html = client.get("/").text
    assert html.index('id="master"') < html.index('class="master-head"') < html.index('id="master-attention"')
    assert html.index('id="master-attention"') < html.index('class="master-filters"')
    assert html.index('id="master-attention"') < html.index("<!-- /master -->")


def test_home_with_no_reminders_has_no_strip(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Quiet", status="waiting")
    html = client.get("/").text
    assert 'id="master-attention"' not in html
    assert "Reminders are unavailable" not in html


def test_home_without_a_store_says_reminders_are_unavailable(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    response = client.get("/")
    assert response.status_code == 200
    assert "Reminders are unavailable" in _strip(response.text)


def test_home_with_an_unsafe_store_still_renders(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch, mode=0o755)
    response = client.get("/")
    assert response.status_code == 200
    assert "Reminders are unavailable" in _strip(response.text)
    assert list(directory.iterdir()) == []


def test_waiting_card_shows_due_and_review_card_shows_review_by(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _note(tmp_path, "alpha", "00-inbox", "New mail", sub="triage", title="New mail")
    due = datetime(2099, 11, 4, 11, 0, tzinfo=timezone(timedelta(hours=2)))
    review_path = _card_path(main, "alpha", "New mail")
    _seed(
        directory,
        _reminder("alpha", "Chase", due),
        Reminder(review_path, "alpha", "Look at it", due.replace(day=5)),
    )
    html = client.get("/").text
    board = html.split('class="master-cols"')[1]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", board))
    assert f"Due {due_label(due)}" in text
    assert f"Review by {due_label(due.replace(day=5))}" in text


def test_orphan_attention_card_is_not_a_link(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _seed(directory, _reminder("alpha", "Gone", datetime.now(timezone.utc) + timedelta(days=2), "Lost one"))
    strip = _strip(client.get("/").text)
    assert "Card moved or removed" in strip and "Lost one" in strip and "Alpha" in strip
    # Static, with only its own Remove: no link opens a popup for a missing card.
    assert "hx-get" not in strip and strip.count("<button") == 1
    assert 'class="attention-card is-later' in strip


def test_orphan_for_an_unknown_entity_gets_a_neutral_chip(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _seed(directory, _reminder("ghost", "Gone", datetime.now(timezone.utc) + timedelta(days=2)))
    strip = _strip(client.get("/").text)
    assert "ghost" in strip and "ent-1" not in strip


def test_unreadable_entity_reminder_is_not_called_moved_and_is_not_a_link(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    outside = tmp_path / "outside"
    _note(outside, "other", "02-work", "Secretnote")
    (tmp_path / "beta1" / "02-work" / "active").symlink_to(
        outside / "other" / "02-work" / "active", target_is_directory=True)
    _seed(directory, _reminder("beta1", "Filing", datetime.now(timezone.utc) + timedelta(days=2), "Cannot see"))
    response = client.get("/")
    assert response.status_code == 200
    strip = _strip(response.text)
    assert "Cannot see" in strip and "Card moved or removed" not in strip
    assert "Can't read this entity right now" in strip or "Can&#39;t read this entity right now" in strip
    assert "hx-get" not in strip and strip.count("<button") == 1


def test_attention_card_shows_the_stored_wall_time_not_a_converted_one(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    due = datetime(2099, 11, 4, 23, 30, tzinfo=timezone(timedelta(hours=-11)))
    _seed(directory, _reminder("alpha", "Chase", due))
    strip = _strip(client.get("/").text)
    assert "23:30" in strip and due_label(due) in strip


def test_page_load_writes_nothing(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=2)))
    path = _card_path(main, "alpha", "Chase")

    def snapshot():
        return {
            p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
            for root in (tmp_path, directory)
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts[len(root.parts):]
        }

    before = snapshot()
    names = sorted(p.name for p in directory.iterdir())
    assert client.get("/").status_code == 200
    assert client.get("/?entity=alpha&sort=title").status_code == 200
    assert client.get(f"/master/card/alpha?path={path}").status_code == 200
    assert snapshot() == before
    assert sorted(p.name for p in directory.iterdir()) == names


def test_page_load_with_an_empty_store_directory_creates_no_file(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    assert client.get("/").status_code == 200
    assert list(directory.iterdir()) == []


def test_attention_css_uses_existing_tokens_and_scrolls_inside_itself():
    rules = _CSS.split("/* --- needs attention --- */")[1].split("/* --- end needs attention --- */")[0]
    assert "overflow-x: auto" in rules
    for token in ("--err-ink", "--err-bg", "--warn", "--ink-dim"):
        assert f"var({token})" in rules


def test_reminder_date_shows_only_on_waiting_and_review_cards(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Finished", status="done")
    _note(tmp_path, "alpha", "02-work", "Active one", status="active")
    _note(tmp_path, "alpha", "02-work", "Sketch", status="draft")
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    due = datetime(2099, 11, 4, 11, 0, tzinfo=timezone(timedelta(hours=2)))
    _seed(directory, *(_reminder("alpha", n, due) for n in ("Finished", "Active one", "Sketch", "Odd")))
    html = client.get("/?done=all").text
    board = html.split('class="master-cols"')[1]
    assert "master-reminder" not in board
    assert due_label(due) not in board


# --- setting and removing a reminder from the popup -------------------------------

_FORM = {"subject": "Chase the supplier", "date": "2099-11-04", "time": "11:00", "offset": "120"}


def _vault_state(root):
    """What a vault write would change: the work tree status and the commit."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout

    return git("status", "--porcelain"), git("rev-parse", "HEAD")


def _save(client, entity, path, **overrides):
    return client.post(f"/master/reminder/{entity}", data={**_FORM, "path": path, **overrides})


def _remove(client, entity, path):
    return client.post(f"/master/reminder/{entity}/remove", data={"path": path})


def _setup(tmp_path, tmp_path_factory, monkeypatch, status="waiting"):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status=status)
    _git_commit_all(tmp_path)
    return main, client, directory, _card_path(main, "alpha", "Chase")


def _refused(response):
    assert response.status_code == 200
    assert 'class="alert' in response.text and "sr-only\">Close<" in response.text
    assert "Traceback" not in response.text and "E-UNKNOWN" not in response.text


def _reminder_refused(response):
    """A refused reminder save or remove is shown inside the reminder box only,
    so the stage form (and any draft typed into it) is left as it is."""
    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="master-reminder"'), response.text
    assert 'class="alert' in response.text and "stage-form" not in response.text
    assert "<textarea" not in response.text and "HX-Trigger" not in response.headers
    assert "Traceback" not in response.text and "E-UNKNOWN" not in response.text


def test_save_reminder_from_the_popup(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    before = _vault_state(tmp_path)
    response = _save(client, "alpha", path)
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "master-refresh"
    saved = ReminderStore(directory).all()
    assert list(saved) == [path]
    assert saved[path].entity == "alpha" and saved[path].subject == "Chase the supplier"
    # The typed wall time and the browser's offset are stored as they came.
    assert saved[path].due_at.isoformat() == "2099-11-04T11:00:00+02:00"
    assert _vault_state(tmp_path) == before
    # The popup comes back showing the reminder, with a way to remove it.
    assert due_label(saved[path].due_at) in response.text
    assert "Remove" in response.text and "Save reminder" in response.text


def test_save_again_replaces_the_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path)
    _save(client, "alpha", path, subject="Second thoughts", date="2099-12-01", time="08:30", offset="-300")
    saved = ReminderStore(directory).all()
    assert len(saved) == 1
    assert saved[path].subject == "Second thoughts"
    assert saved[path].due_at.isoformat() == "2099-12-01T08:30:00-05:00"


def test_save_for_a_card_to_review(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "New mail", sub="triage", title="New mail")
    path = _card_path(main, "alpha", "New mail")
    assert _save(client, "alpha", path).headers["HX-Trigger"] == "master-refresh"
    assert list(ReminderStore(directory).all()) == [path]


def test_remove_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path)
    before = _vault_state(tmp_path)
    response = _remove(client, "alpha", path)
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "master-refresh"
    assert ReminderStore(directory).all() == {}
    assert _vault_state(tmp_path) == before
    # The card is still there, so the popup comes back, now with no Remove.
    assert "Save reminder" in response.text and ">Remove<" not in response.text


def test_remove_orphan_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    gone = _reminder("alpha", "Gone", datetime.now(timezone.utc) + timedelta(days=2))
    ghost = Reminder("ghost/02-work/active/X.md", "ghost", "Old", datetime.now(timezone.utc) + timedelta(days=2))
    other = _reminder("alpha", "Kept", datetime.now(timezone.utc) + timedelta(days=2))
    _seed(directory, gone, ghost, other)
    response = _remove(client, "alpha", gone.key)
    assert response.status_code == 200 and response.text == ""
    assert response.headers["HX-Trigger"] == "master-refresh"
    # An entity that left the registry can still be cleaned up.
    response = _remove(client, "ghost", ghost.key)
    assert response.status_code == 200 and response.text == ""
    assert response.headers["HX-Trigger"] == "master-refresh"
    assert list(ReminderStore(directory).all()) == [other.key]


def test_remove_of_an_absent_reminder_is_a_quiet_success(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    response = _remove(client, "alpha", path)
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "master-refresh"
    assert list(directory.iterdir()) == []


def test_remove_refuses_another_entitys_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    row = Reminder("alpha/02-work/active/Odd.md", "beta1", "Not yours", datetime.now(timezone.utc) + timedelta(days=2))
    _seed(directory, row)
    response = _remove(client, "alpha", row.key)
    _reminder_refused(response)
    assert "HX-Trigger" not in response.headers
    assert ReminderStore(directory).all() == {row.key: row}


def test_remove_without_a_store_is_refused_not_500(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    response = _remove(client, "alpha", "alpha/02-work/active/X.md")
    _reminder_refused(response)
    assert "E-UNAVAILABLE" in response.text


def test_save_is_refused_for_a_draft_or_done_card(tmp_path, tmp_path_factory, monkeypatch):
    for status in ("draft", "done", "active", "open"):
        main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch, status=status)
        response = _save(client, "alpha", path)
        _reminder_refused(response)
        assert "HX-Trigger" not in response.headers
        assert list(directory.iterdir()) == [], status


def test_save_is_refused_for_a_path_that_is_not_a_card(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    for forged in ("alpha/02-work/active/Nope.md", "../alpha/02-work/active/Chase.md", "", "alpha/outbox"):
        _reminder_refused(_save(client, "alpha", forged))
    assert list(directory.iterdir()) == []


def test_save_is_refused_for_another_entitys_path(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "beta1", "02-work", "Theirs", status="waiting")
    theirs = _card_path(main, "beta1", "Theirs")
    _reminder_refused(_save(client, "alpha", theirs))
    _reminder_refused(_save(client, "beta1", path))
    # An entity outside the registry is refused by the scope dependency, described.
    ghost = _save(client, "ghost", path)
    assert ghost.status_code == 200 and "E-ENTITY" in ghost.text
    assert list(directory.iterdir()) == []


def test_save_is_refused_for_an_empty_or_multiline_subject(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    for subject in ("", "   ", "one\ntwo", "one\rtwo", "x" * 121):
        _reminder_refused(_save(client, "alpha", path, subject=subject))
    assert list(directory.iterdir()) == []
    assert ReminderStore(directory).all() == {}


def test_save_is_refused_for_a_bad_date_or_offset(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    for bad in (
        {"date": "2026-02-30"}, {"date": ""}, {"date": "not a date"}, {"time": "25:00"},
        {"time": ""}, {"offset": ""}, {"offset": "x"}, {"offset": "9999"},
    ):
        _reminder_refused(_save(client, "alpha", path, **bad))
    assert list(directory.iterdir()) == []


def test_a_refused_save_keeps_the_existing_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path)
    before = ReminderStore(directory).all()
    _reminder_refused(_save(client, "alpha", path, subject=""))
    assert ReminderStore(directory).all() == before


def test_save_without_a_store_is_refused_not_500(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    response = _save(client, "alpha", _card_path(main, "alpha", "Chase"))
    _reminder_refused(response)
    assert "E-UNAVAILABLE" in response.text


def test_save_with_an_unsafe_store_is_refused_not_500(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _auth_dir(tmp_path_factory, monkeypatch, mode=0o755)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _reminder_refused(_save(client, "alpha", _card_path(main, "alpha", "Chase")))


def test_save_stores_the_wall_time_without_converting_it(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    # 23:30 at UTC-11 is already the next day in UTC; the label must keep the typed day.
    _save(client, "alpha", path, date="2099-11-04", time="23:30", offset="-660")
    due = ReminderStore(directory).all()[path].due_at
    assert due.isoformat() == "2099-11-04T23:30:00-11:00"
    page = client.get(f"/master/card/alpha?path={path}").text
    assert "23:30" in page


def test_popup_shows_reminder_box_only_for_waiting_and_review(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _auth_dir(tmp_path_factory, monkeypatch)
    for title, status in (("Waits", "waiting"), ("Sketch", "draft"), ("Finished", "done"),
                          ("Live", "active"), ("Odd", "open")):
        _note(tmp_path, "alpha", "02-work", title, status=status)
    _note(tmp_path, "alpha", "00-inbox", "New mail", sub="triage", title="New mail")
    for title, shown in (("Waits", True), ("New mail", True), ("Sketch", False),
                         ("Finished", False), ("Live", False), ("Odd", False)):
        html = client.get("/master/card/alpha", params={"path": _card_path(main, "alpha", title)}).text
        assert ("Save reminder" in html) is shown, title
        assert ('hx-post="/master/reminder/alpha"' in html) is shown, title
        assert (">Remove<" in html) is False, title


def test_popup_box_prefills_the_card_title_and_has_the_form_fields(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    html = client.get("/master/card/alpha", params={"path": path}).text
    for fragment in ('name="path"', 'name="subject"', 'name="date"', 'name="time"', 'name="offset"',
                     'type="date"', 'type="time"', 'maxlength="120"', 'value="Chase"'):
        assert fragment in html, fragment
    # The stage form has its own disabled controls (the Enter guard, hidden subs);
    # the reminder box itself is enabled.
    box = html.split('class="reminder-box"')[1]
    assert "Reminders are unavailable" not in box and "disabled" not in box


def test_popup_prefills_existing_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path, subject="Call <b>them</b>", date="2099-11-04", time="09:15", offset="-480")
    html = client.get("/master/card/alpha", params={"path": path}).text
    assert 'value="Call &lt;b&gt;them&lt;/b&gt;"' in html and "<b>them</b>" not in html
    assert 'value="2099-11-04"' in html and 'value="09:15"' in html
    assert ">Remove<" in html and "/master/reminder/alpha/remove" in html
    assert "Due" in re.sub(r"<[^>]+>|\s+", " ", html)


def test_popup_ignores_a_reminder_stored_for_another_entity(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _seed(directory, Reminder(path, "beta1", "Stray", datetime.now(timezone.utc) + timedelta(days=2)))
    html = client.get("/master/card/alpha", params={"path": path}).text
    assert "Stray" not in html and 'value="Chase"' in html and ">Remove<" not in html


def test_popup_without_a_store_disables_the_box(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    response = client.get("/master/card/alpha", params={"path": _card_path(main, "alpha", "Chase")})
    assert response.status_code == 200
    assert "Reminders are unavailable" in response.text
    assert re.search(r"<fieldset[^>]*\bdisabled\b", response.text)


def test_popup_sends_the_browsers_offset_for_the_chosen_date(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    home = client.get("/").text
    script = home.split("Alpine.data('reminderBox'")[1].split("Alpine.data('masterHead'")[0]
    assert "getTimezoneOffset" in script and "new Date(y, m - 1, d" in script
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    popup = client.get("/master/card/alpha", params={"path": _card_path(main, "alpha", "Chase")}).text
    assert 'x-data="reminderBox"' in popup and 'x-ref="offset"' in popup


def test_orphan_strip_card_has_an_inline_remove(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    gone = _reminder("alpha", "Gone", datetime.now(timezone.utc) + timedelta(days=2), "Lost one")
    _seed(directory, gone)
    strip = _strip(client.get("/").text)
    assert 'hx-post="/master/reminder/alpha/remove"' in strip
    assert f'name="path" value="{gone.key}"' in strip
    assert 'hx-swap="none"' in strip and re.search(r"<button[^>]*>Remove<span", strip)
    assert "hx-get" not in strip
    # Posting what the button posts removes the reminder.
    assert _remove(client, "alpha", gone.key).headers["HX-Trigger"] == "master-refresh"
    assert ReminderStore(directory).all() == {}
    assert 'id="master-attention"' not in client.get("/").text


def test_unreadable_entity_strip_card_has_an_inline_remove(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    outside = tmp_path / "outside"
    _note(outside, "other", "02-work", "Secretnote")
    (tmp_path / "beta1" / "02-work" / "active").symlink_to(
        outside / "other" / "02-work" / "active", target_is_directory=True)
    row = _reminder("beta1", "Filing", datetime.now(timezone.utc) + timedelta(days=2), "Cannot see")
    _seed(directory, row)
    strip = _strip(client.get("/").text)
    assert 'hx-post="/master/reminder/beta1/remove"' in strip and f'value="{row.key}"' in strip
    assert _remove(client, "beta1", row.key).status_code == 200
    assert ReminderStore(directory).all() == {}


def test_linked_strip_cards_have_no_remove_button(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path)
    strip = _strip(client.get("/").text)
    assert "hx-post" not in strip and strip.count("<button") == 1


def _restage(tmp_path, name, status):
    """Edit a card's `status:` by hand, as the owner would."""
    path = tmp_path / "alpha" / "02-work" / "active" / f"{name}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: waiting", f"status: {status}"), encoding="utf-8")


@pytest.mark.parametrize("status, label", [("draft", "Draft"), ("wating", "Another status")])
def test_a_reminder_on_a_card_that_left_waiting_can_still_be_removed(
    tmp_path, tmp_path_factory, monkeypatch, status, label
):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path, subject="Chase the supplier")
    _restage(tmp_path, "Chase", status)
    # The strip still shows it, as a linked card with no inline Remove.
    assert "Chase the supplier" in _strip(client.get("/").text)
    html = client.get("/master/card/alpha", params={"path": path}).text
    assert label in html
    # Read-only: the subject and due label, and a Remove button; nothing to save.
    stored = ReminderStore(directory).all()[path]
    assert "Chase the supplier" in html and due_label(stored.due_at) in html
    assert ">Remove<" in html and 'hx-post="/master/reminder/alpha/remove"' in html
    assert "Save reminder" not in html and 'type="date"' not in html
    assert 'type="time"' not in html and 'name="subject"' not in html
    assert 'hx-post="/master/reminder/alpha"' not in html
    # Removing it (what the button posts) empties the strip.
    removed = _remove(client, "alpha", path)
    assert removed.status_code == 200 and removed.headers["HX-Trigger"] == "master-refresh"
    assert "Save reminder" not in removed.text and ">Remove<" not in removed.text
    assert ReminderStore(directory).all() == {}
    assert _strip(client.get("/").text) == ""


def test_saving_for_a_card_that_left_waiting_is_still_refused(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    _save(client, "alpha", path)
    _restage(tmp_path, "Chase", "draft")
    before = ReminderStore(directory).all()
    _reminder_refused(_save(client, "alpha", path, subject="Changed"))
    assert ReminderStore(directory).all() == before


def test_the_master_view_refreshes_when_a_reminder_changes():
    main_template = (Path(__file__).resolve().parent.parent / "templates" / "home.html").read_text(encoding="utf-8")
    tag = main_template.split('<div id="master"')[1].split(">")[0]
    assert "master-refresh from:body" in tag and "every 60s" in tag
    assert 'hx-get="{{ request.url.path }}' in tag  # still relative


def test_reminder_box_css_uses_existing_tokens():
    assert ".reminder-box" in _CSS
    rules = _CSS.split("/* --- reminder box --- */")[1].split("/* --- end reminder box --- */")[0]
    assert "var(--" in rules and "#" not in re.sub(r"/\*.*?\*/", "", rules, flags=re.S).replace("#master", "")


def test_save_requires_csrf(tmp_path, monkeypatch):
    import pyotp
    from app.auth import AuthStore

    main, _client_ = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    path = _card_path(main, "alpha", "Chase")
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    store = AuthStore(directory)
    store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(directory))
    monkeypatch.setenv("ONEOS_AUTH_MODE", "required")
    monkeypatch.setenv("ONEOS_ORIGIN", "https://localhost:8443")
    origin = {"Origin": "https://localhost:8443", "HX-Request": "true"}
    with TestClient(main.app, base_url="https://localhost:8443", follow_redirects=False) as client:
        # Signed out: refused outright.
        assert client.post(f"/master/reminder/alpha", data={**_FORM, "path": path}, headers=origin).status_code == 401
        token = store.login("synthetic owner password", pyotp.TOTP(secret).now())
        client.cookies.set("__Host-oneos", token)
        csrf = store.session(token)
        for headers in (origin, {**origin, "X-CSRF-Token": "wrong"}):
            assert client.post("/master/reminder/alpha", data={**_FORM, "path": path}, headers=headers).status_code == 403
            assert client.post("/master/reminder/alpha/remove", data={"path": path}, headers=headers).status_code == 403
        assert ReminderStore(directory).all() == {}
        ok = client.post("/master/reminder/alpha", data={**_FORM, "path": path}, headers={**origin, "X-CSRF-Token": csrf})
        assert ok.status_code == 200 and ok.headers["HX-Trigger"] == "master-refresh"
        assert list(ReminderStore(directory).all()) == [path]
        # The popup form is a POST that htmx sends the token with, from the page meta.
        assert 'name="csrf-token"' in client.get("/").text
        assert client.post("/master/reminder/alpha/remove", data={"path": path}, headers=origin).status_code == 403
        assert list(ReminderStore(directory).all()) == [path]
        removed = client.post("/master/reminder/alpha/remove", data={"path": path}, headers={**origin, "X-CSRF-Token": csrf})
        assert removed.status_code == 200 and ReminderStore(directory).all() == {}


def test_default_subject_of_a_long_or_multiline_title_saves_unedited(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Longwinded", status="waiting", title="L" * 130)
    # A YAML double-quoted \n is a real line break in the title, as in a mail subject.
    _note(tmp_path, "alpha", "00-inbox", "Mail", sub="triage", title='"Re: split\\nsubject"')
    cards = main.entity_cards(main.build_scope("alpha"), main.Vault(main.current_catalog()))
    chosen = [c for c in (*cards.inbox, *cards.filed) if c.title.startswith(("LLL", "Re:"))]
    assert len(chosen) == 2 and any("\n" in c.title for c in chosen)
    for card in chosen:
        html = client.get("/master/card/alpha", params={"path": card.relative_path}).text
        value = re.search(r'id="reminder-subject"[^>]*value="([^"]*)"', html).group(1)
        assert 0 < len(value) <= 120 and "\n" not in value
        # What the browser submits for the untouched form is accepted and stored.
        response = _save(client, "alpha", card.relative_path, subject=value)
        assert response.headers["HX-Trigger"] == "master-refresh", response.text
        assert ReminderStore(directory).all()[card.relative_path].subject == value
    assert len(ReminderStore(directory).all()) == 2


def test_popup_not_found_context_is_fresh_each_render(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    for _ in range(2):
        assert "no longer here" in client.get("/master/card/alpha", params={"path": "nope.md"}).text
    assert main._empty_popup() is not main._empty_popup()
    assert "request" not in main._empty_popup()


# --- a reminder follows its card through Outbox approval ------------------------

_PROPOSAL_ID = "20260815T090703-" + "11" * 16
_SRC = "alpha/00-inbox/active/marker.md"
_DST = "alpha/02-work/active/marker.md"


def _approval_setup(tmp_path, tmp_path_factory, monkeypatch, *, store=True):
    """A committed vault with one inbox note and its pending classify proposal,
    and (unless `store` is false) a reminder on the inbox card."""
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active")
    client = TestClient(main.app)
    if not store:
        monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
        return main, vault, client, None
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _seed(directory, Reminder(_SRC, "alpha", "Review it", datetime.now(timezone.utc) + timedelta(days=2)))
    return main, vault, client, directory


def _approve(client, tmp_path, proposal_id=_PROPOSAL_ID):
    from app.outbox import get_proposal_review
    from app.scope import Scope

    sha = get_proposal_review(Scope(tmp_path, "alpha"), proposal_id).sha256
    return client.post("/outbox/alpha/approve", data={"id": proposal_id, "review_sha256": sha})


def _page(main, vault, directory):
    return load_master(vault, main.build_scope, entities=(), sort="updated", done_all=True,
                       now=datetime.now(timezone.utc), reminders=ReminderStore(directory).all())


def test_approving_a_proposal_moves_its_reminder(tmp_path, tmp_path_factory, monkeypatch):
    main, vault, client, directory = _approval_setup(tmp_path, tmp_path_factory, monkeypatch)
    assert list(ReminderStore(directory).all()) == [_SRC]
    response = _approve(client, tmp_path)
    assert response.status_code == 200
    assert (tmp_path / _DST).is_file() and not (tmp_path / _SRC).exists()
    found = ReminderStore(directory).all()
    assert list(found) == [_DST]
    assert (found[_DST].entity, found[_DST].subject) == ("alpha", "Review it")
    # The reminder is on the filed card, in Done.
    done = _page(main, vault, directory).columns[3].cards
    assert [(m.card.relative_path, m.reminder.subject) for m in done] == [(_DST, "Review it")]


def test_approved_review_reminder_is_resolved(tmp_path, tmp_path_factory, monkeypatch):
    main, vault, client, directory = _approval_setup(tmp_path, tmp_path_factory, monkeypatch)
    before = _page(main, vault, directory)
    assert [e.reminder.key for e in before.attention] == [_SRC]
    _approve(client, tmp_path)
    after = _page(main, vault, directory)
    assert after.attention == ()
    assert [m.card.relative_path for m in after.columns[3].cards] == [_DST]
    assert after.columns[3].cards[0].card.status == "active"
    assert list(ReminderStore(directory).all()) == [_DST]  # resolved, not deleted
    assert "Needs attention" not in client.get("/").text


def test_rekey_failure_does_not_fail_the_approval(tmp_path, tmp_path_factory, monkeypatch):
    main, vault, client, directory = _approval_setup(tmp_path, tmp_path_factory, monkeypatch)
    directory.chmod(0o755)  # an unsafe store: the re-key cannot run
    response = _approve(client, tmp_path)
    assert response.status_code == 200
    assert (tmp_path / _DST).is_file() and not (tmp_path / _SRC).exists()
    assert "outbox: approve" in subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    directory.chmod(0o700)
    assert list(ReminderStore(directory).all()) == [_SRC]  # nothing lost
    page = _page(main, vault, directory)
    assert [(e.reminder.key, e.card) for e in page.attention] == [(_SRC, None)]
    assert "Card moved or removed" in client.get("/").text


def test_approval_without_a_store_still_files_the_item(tmp_path, tmp_path_factory, monkeypatch):
    _main, _vault_, client, _ = _approval_setup(tmp_path, tmp_path_factory, monkeypatch, store=False)
    assert _approve(client, tmp_path).status_code == 200
    assert (tmp_path / _DST).is_file()


def test_a_spent_replay_does_not_move_a_reminder(tmp_path, tmp_path_factory, monkeypatch):
    _main, _vault_, client, directory = _approval_setup(tmp_path, tmp_path_factory, monkeypatch)
    from app.outbox import get_proposal_review
    from app.scope import Scope

    sha = get_proposal_review(Scope(tmp_path, "alpha"), _PROPOSAL_ID).sha256
    data = {"id": _PROPOSAL_ID, "review_sha256": sha}
    assert client.post("/outbox/alpha/approve", data=data).status_code == 200
    assert list(ReminderStore(directory).all()) == [_DST]
    # The owner sets a new reminder under the old path, then replays the stale button.
    _seed(directory, Reminder(_SRC, "alpha", "Unrelated", datetime.now(timezone.utc) + timedelta(days=1)))
    replay = client.post("/outbox/alpha/approve", data=data)
    assert replay.status_code == 200
    assert "An approval has already completed" in replay.text
    assert set(ReminderStore(directory).all()) == {_SRC, _DST}


def _approval_commit(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True).stdout

    files = git("show", "--name-status", "--format=", "HEAD").decode().splitlines()
    receipt = next(line.split("\t")[-1] for line in files if "/.receipts/" in line)
    return {
        "count": git("rev-list", "--count", "HEAD").strip(),
        "subject": git("log", "-1", "--format=%s%n%b").decode(),
        "files": files,
        "receipt": git("show", f"HEAD:{receipt}"),
        "tree": git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines(),
    }


def test_approval_commit_is_unchanged(tmp_path, tmp_path_factory, monkeypatch):
    with_reminder = tmp_path / "with"
    without = tmp_path / "without"
    outcomes = []
    for root, store in ((with_reminder, True), (without, False)):
        root.mkdir()
        _main, _vault_, client, directory = _approval_setup(root, tmp_path_factory, monkeypatch, store=store)
        assert _approve(client, root).status_code == 200
        outcomes.append(_approval_commit(root))
        if store:
            assert list(ReminderStore(directory).all()) == [_DST]
    assert outcomes[0] == outcomes[1]
    # The move and its receipt, nothing else; the proposal is not in the commit.
    assert [line.split("\t")[0][0] for line in outcomes[0]["files"]] == ["R", "A"]
    assert outcomes[0]["receipt"].startswith(b"version: 1")
    # Neither the reminder store nor a reminder trace is in the vault.
    assert not any("reminder" in name for name in outcomes[0]["tree"])


# --- review flags ----------------------------------------------------------


def _flag(entity: str, name: str, comment: str | None = "Look again", **kw) -> ReviewFlag:
    return ReviewFlag(kw.get("key", _path(entity, name)), kw.get("entity", entity), comment, NOW)


def _flags(*flags: ReviewFlag) -> dict[str, ReviewFlag]:
    return {f.key: f for f in flags}


def _titles(page, key):
    return [m.card.title for c in page.columns if c.key == key for m in c.cards]


def test_a_flagged_filed_card_is_in_to_review_only_and_keeps_its_status(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Shut", status="done")
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    _note(tmp_path, "alpha", "00-inbox", "Fresh", sub="triage")
    page = _load(vault, main, {}, flags=_flags(
        _flag("alpha", "Spec"), _flag("alpha", "Chase", None), _flag("alpha", "Shut"), _flag("alpha", "Odd"),
    ))
    assert sorted(_titles(page, "review")) == ["Chase", "Fresh", "Odd", "Shut", "Spec"]
    for key in ("draft", "waiting", "done"):
        assert _titles(page, key) == []
    assert page.other == ()
    review = {m.card.title: m for c in page.columns if c.key == "review" for m in c.cards}
    assert review["Spec"].card.status == "draft" and review["Chase"].card.status == "waiting"
    assert review["Odd"].card.status == "open"
    assert review["Spec"].flag.comment == "Look again" and review["Chase"].flag.comment is None
    assert review["Fresh"].flag is None
    assert next(c for c in page.columns if c.key == "review").total == 5


def test_unflagging_returns_the_card_to_its_stage_column(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    flagged = _load(vault, main, {}, flags=_flags(_flag("alpha", "Spec")))
    assert _titles(flagged, "draft") == [] and _titles(flagged, "review") == ["Spec"]
    for flags in ({}, None):
        page = _load(vault, main, {}, flags=flags)
        assert _titles(page, "draft") == ["Spec"] and _titles(page, "review") == []
        assert page.columns[0].cards[0].flag is None


def test_a_flagged_card_shows_its_review_by_reminder_in_to_review(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "alpha", "02-work", "Other", status="draft")
    _note(tmp_path, "alpha", "00-inbox", "Fresh", sub="triage", updated="2026-10-30")
    mine = _reminder("alpha", "Spec", NOW + timedelta(hours=3), "Review by Thursday")
    page = _load(vault, main, _by_key(mine), flags=_flags(_flag("alpha", "Spec"), _flag("alpha", "Other")))
    cards = next(c for c in page.columns if c.key == "review").cards
    assert cards[0].card.title == "Spec"  # a reminder puts it first
    assert cards[0].reminder == mine and cards[0].flag is not None
    assert page.attention[0].card is cards[0]  # strip links to the To review card
    assert "Spec" not in _titles(page, "draft")


def test_a_flag_on_an_inbox_or_proposed_card_is_ignored(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _note(tmp_path, "alpha", "00-inbox", "Fresh", sub="triage")
    _inbox_with_proposal(tmp_path, "beta1", "Proposed.md")
    paths = {m.card.title: m.card.relative_path
             for c in _load(vault, main, {}).columns if c.key == "review" for m in c.cards}
    flags = {path: ReviewFlag(path, path.split("/")[0], "ignored", NOW) for path in paths.values()}
    page = _load(vault, main, {}, flags=flags)
    review = [m for c in page.columns if c.key == "review" for m in c.cards]
    assert len(review) == len(paths) == 2
    assert all(m.flag is None for m in review)


def test_a_flag_matching_no_card_is_ignored_and_is_not_an_attention_entry(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    stray = _flag("alpha", "Gone")
    wrong_entity = ReviewFlag(_path("alpha", "Spec"), "beta1", "x", NOW)
    page = _load(vault, main, {}, flags=_flags(stray, wrong_entity))
    assert _titles(page, "draft") == ["Spec"] and _titles(page, "review") == []
    assert page.attention == ()


def test_store_unavailable_flags_are_ignored_and_the_page_renders(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch, mode=0o755)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    response = client.get("/")
    assert response.status_code == 200 and "Spec" in response.text
    assert list(directory.iterdir()) == []
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    assert client.get("/").status_code == 200


def test_the_home_page_reads_flags_from_the_store_without_writing(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    path = _card_path(main, "alpha", "Spec")
    before = client.get("/").text
    assert list(directory.iterdir()) == []  # a read creates nothing
    ReminderStore(directory).flag(ReviewFlag(path, "alpha", "Look <b>again</b>", NOW))
    flagged = client.get("/").text
    assert flagged != before
    page_flags = main._page_flags()
    assert set(page_flags) == {path}
    ReminderStore(directory).unflag(path)
    assert client.get("/").text == before


# --- stage buttons, draft editing and comments in the popup ------------------
#
# Every press is one approved Outbox action: one commit holding the note change
# and an "approval" receipt, revertible with `git revert`. A filed note moved
# into To review is a review flag in the private store instead (no commit).

import html as _html  # noqa: E402

from app.action_receipts import parse_action_receipt  # noqa: E402

_STALE = "This note changed since you opened it. Here is the newer version."
_UNCHANGED = "Nothing changed."
_CARD_CHANGED = "This card changed since you opened it. Here is the newer version."
_HEADER = "This note's header can't be edited safely here. Edit it in the vault, then try again."


def _today(days=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _commits(root):
    return int(_git(root, "rev-list", "--count", "HEAD"))


def _head_files(root):
    """`(status, path)` for every path the HEAD commit changed."""
    lines = _git(root, "show", "--name-status", "--format=", "-M", "HEAD").splitlines()
    return sorted((line.split("\t")[0][0], line.split("\t")[-1]) for line in lines)


def _stage_setup(tmp_path, tmp_path_factory, monkeypatch):
    main, _vault_ = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    (tmp_path / "alpha" / "02-work" / "active").mkdir(parents=True, exist_ok=True)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    return main, TestClient(main.app), directory


def _popup_form(client, entity, path):
    """What the popup's stage form would post: its hidden fields and textarea."""
    page = client.get(f"/master/card/{entity}", params={"path": path}).text
    form = re.search(r'<form class="stage-form".*?</form>', page, re.S)
    assert form, page
    fields = {
        name: _html.unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([a-z0-9_]+)" value="([^"]*)"', form.group(0))
    }
    body = re.search(r'<textarea[^>]*name="body"[^>]*>(.*?)</textarea>', form.group(0), re.S)
    fields["body"] = _html.unescape(body.group(1))
    return fields


def _press(client, entity, path, stage, form=None, **overrides):
    form = dict(form if form is not None else _popup_form(client, entity, path))
    form.update(stage=stage, today=_today())
    form.update(overrides)
    return client.post(f"/master/stage/{entity}", data={k: v for k, v in form.items() if v is not None})


def _notice(response):
    found = re.search(r'class="master-notice[^"]*"[^>]*>(.*?)</p>', response.text, re.S)
    return _html.unescape(found.group(1).strip()) if found else None


def _stage_receipt(root, entity="alpha"):
    receipt = next(path for status, path in _head_files(root) if "/.receipts/" in path)
    return parse_action_receipt(Path(receipt), (root / receipt).read_bytes())


def _assert_one_stage_commit(root, before, paths):
    """One new commit: exactly `paths` and the approval receipt, revertible."""
    assert _commits(root) == before + 1
    files = _head_files(root)
    receipts = [path for _status, path in files if "/outbox/.receipts/" in path]
    assert len(receipts) == 1
    assert sorted(path for _status, path in files if path not in receipts) == sorted(paths)
    assert _stage_receipt(root).action_kind == "approval"
    assert _git(root, "log", "-1", "--format=%s").startswith("outbox: stage ")
    # No stage record is ever committed; the consumed one is quarantined.
    assert not any("/outbox/" in path and "/.receipts/" not in path for _s, path in files)


def _revert_restores(root, rel, original: bytes, gone: str | None = None):
    _git(root, "revert", "--no-edit", "HEAD")
    assert (root / rel).read_bytes() == original
    if gone:
        assert not (root / gone).exists()


def _nothing_written(root, head, directory=None, store_before=None):
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
    if directory is not None:
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == store_before


@pytest.mark.parametrize("start, press, label", [
    ("draft", "waiting", "Waiting"),
    ("waiting", "done", "Done"),
    ("done", "draft", "Draft"),
    ("waiting", "draft", "Draft"),
    ("open", "done", "Done"),
])
def test_filed_note_moves_by_button_in_one_revertible_commit(
    tmp_path, tmp_path_factory, monkeypatch, start, press, label
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status=start)
    _git_commit_all(tmp_path)
    path, original, before = _path("alpha", "Plan"), note.read_bytes(), _commits(tmp_path)
    response = _press(client, "alpha", path, press)
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "master-refresh"
    assert f'class="master-stage">{label}<' in response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    text = note.read_text(encoding="utf-8")
    assert f"\nstatus: {press}\n" in text and f"\nupdated: {_today()}\n" in text
    # Only the two header lines changed.
    assert text.replace(f"status: {press}", f"status: {start}").replace(
        f"updated: {_today()}", "updated: 2026-01-02") == original.decode()
    _revert_restores(tmp_path, path, original)


def test_done_resolves_the_cards_reminder_after_the_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Other", status="waiting")
    _git_commit_all(tmp_path)
    path, other = _path("alpha", "Chase"), _path("alpha", "Other")
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)),
          _reminder("alpha", "Other", datetime.now(timezone.utc) + timedelta(days=1)))
    before = _commits(tmp_path)
    assert _press(client, "alpha", path, "done").status_code == 200
    _assert_one_stage_commit(tmp_path, before, [path])
    assert set(ReminderStore(directory).all()) == {other}


def test_waiting_keeps_the_reminder_and_the_reminder_box(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="draft")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Chase")
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)))
    response = _press(client, "alpha", path, "waiting")
    assert set(ReminderStore(directory).all()) == {path}
    assert "Save reminder" in response.text and 'hx-post="/master/reminder/alpha"' in response.text


def test_a_press_with_no_change_writes_nothing_and_says_so(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Live", status="active")
    _git_commit_all(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    for name, stage in (("Plan", "waiting"), ("Live", "done")):
        response = _press(client, "alpha", _path("alpha", name), stage)
        assert response.status_code == 200 and _notice(response) == _UNCHANGED
        assert "HX-Trigger" not in response.headers
    # A blank comment is no comment.
    assert _notice(_press(client, "alpha", _path("alpha", "Plan"), "waiting", comment="   ")) == _UNCHANGED
    _nothing_written(tmp_path, head)
    assert list(directory.iterdir()) == []


def test_body_comment_and_stage_are_one_commit_with_the_posted_date(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="First line.\n\nSecond line.")
    _git_commit_all(tmp_path)
    path, original, before = _path("alpha", "Plan"), note.read_bytes(), _commits(tmp_path)
    form = _popup_form(client, "alpha", path)
    assert form["body"] == "First line.\n\nSecond line.\n"
    # Browsers post textareas with CRLF; the vault keeps LF.
    edited = "First line, edited.\r\n\r\nSecond line.\r\n"
    yesterday = _today(-1)
    response = _press(client, "alpha", path, "waiting", form, body=edited,
                      comment="  Pay from savings  ", today=yesterday)
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    text = note.read_text(encoding="utf-8")
    header, body = text.split("---\n", 2)[1:]
    assert f"\nupdated: {yesterday}\n" in "\n" + header and "\nstatus: waiting\n" in "\n" + header
    # The comment is its own dated line, after one blank line.
    assert body == f"First line, edited.\n\nSecond line.\n\nComment ({yesterday}): Pay from savings\n"
    assert "\r" not in text
    _revert_restores(tmp_path, path, original)


def test_clearing_the_draft_saves_an_empty_body(tmp_path, tmp_path_factory, monkeypatch):
    """The browser posts an emptied textarea as `body=`; that is an edit to an
    empty body, not "no body sent"."""
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Old draft text.")
    _git_commit_all(tmp_path)
    path, original, before = _path("alpha", "Plan"), note.read_text(encoding="utf-8"), _commits(tmp_path)
    form = _popup_form(client, "alpha", path)
    assert form["body"] == "Old draft text.\n"
    response = _press(client, "alpha", path, "draft", form, body="")
    assert response.status_code == 200, response.text
    assert _notice(response) != _UNCHANGED
    _assert_one_stage_commit(tmp_path, before, [path])
    text = note.read_text(encoding="utf-8")
    header, body = text.split("---\n", 2)[1:]
    assert body == ""
    assert header == original.split("---\n", 2)[1].replace("updated: 2026-01-02", f"updated: {_today()}")


def test_a_post_without_a_body_field_keeps_the_body(tmp_path, tmp_path_factory, monkeypatch):
    """A drag (or a read-only body) sends no body at all: the body stays."""
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Keep me.")
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Plan"), _commits(tmp_path)
    sha = hashlib.sha256(note.read_bytes()).hexdigest()
    response = client.post("/master/stage/alpha", data={
        "path": path, "source_sha256": sha, "today": _today(), "stage": "waiting",
    })
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    text = note.read_text(encoding="utf-8")
    assert text.endswith("---\nKeep me.\n") and "\nstatus: waiting\n" in text


def test_a_comment_alone_is_a_commit_and_keeps_the_stage(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Body")
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Plan"), _commits(tmp_path)
    response = _press(client, "alpha", path, "draft", comment="Check totals")
    assert response.status_code == 200
    _assert_one_stage_commit(tmp_path, before, [path])
    assert note.read_text(encoding="utf-8").endswith(f"\nBody\n\nComment ({_today()}): Check totals\n")
    assert "\nstatus: draft\n" in note.read_text(encoding="utf-8")


def test_filed_note_to_review_without_an_edit_is_a_flag_and_no_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="waiting")
    _git_commit_all(tmp_path)
    path, original, head = _path("alpha", "Plan"), note.read_bytes(), _git(tmp_path, "rev-parse", "HEAD")
    response = _press(client, "alpha", path, "review", comment="Ask about the totals")
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "master-refresh"
    _nothing_written(tmp_path, head)
    assert note.read_bytes() == original
    flags = ReminderStore(directory).flags()
    assert list(flags) == [path] and flags[path].comment == "Ask about the totals"
    assert flags[path].entity == "alpha"
    # The popup now shows the card in To review, with its reminder box.
    assert 'class="master-stage">To review<' in response.text and "Save reminder" in response.text
    # And a reminder can be saved on it there.
    saved = _save(client, "alpha", path)
    assert saved.headers.get("HX-Trigger") == "master-refresh", saved.text
    # Pressing To review again with nothing new changes nothing.
    assert _notice(_press(client, "alpha", path, "review")) == _UNCHANGED


def test_filed_note_to_review_with_an_edit_is_one_commit_keeping_status_then_a_flag(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Old body")
    _git_commit_all(tmp_path)
    path, original, before = _path("alpha", "Plan"), note.read_bytes(), _commits(tmp_path)
    response = _press(client, "alpha", path, "review", body="New body\n")
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    text = note.read_text(encoding="utf-8")
    assert "\nstatus: draft\n" in text and f"\nupdated: {_today()}\n" in text
    assert text.endswith("---\nNew body\n")
    assert list(ReminderStore(directory).flags()) == [path]
    _revert_restores(tmp_path, path, original)


def test_moving_a_flagged_note_clears_its_flag(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Spec", status="waiting")
    _git_commit_all(tmp_path)
    plan, spec = _path("alpha", "Plan"), _path("alpha", "Spec")
    store = ReminderStore(directory)
    store.flag(_flag("alpha", "Plan"))
    store.flag(_flag("alpha", "Spec"))
    # Back to its own stage: only the flag goes, no commit.
    head = _git(tmp_path, "rev-parse", "HEAD")
    response = _press(client, "alpha", plan, "waiting")
    assert response.status_code == 200 and 'class="master-stage">Waiting<' in response.text
    _nothing_written(tmp_path, head)
    assert list(store.flags()) == [spec]
    # On to another stage: one commit, then the flag goes.
    before = _commits(tmp_path)
    _press(client, "alpha", spec, "done")
    _assert_one_stage_commit(tmp_path, before, [spec])
    assert store.flags() == {}


def test_bare_inbox_item_needs_a_destination_then_files_in_one_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    src = "alpha/00-inbox/active/mail.md"
    note = _note(tmp_path, "alpha", "00-inbox", "mail", sub="triage", status="active")
    _git_commit_all(tmp_path)
    _seed(directory, Reminder(src, "alpha", "Answer it", datetime.now(timezone.utc) + timedelta(days=1)))
    page = client.get("/master/card/alpha", params={"path": src}).text
    assert "File to" in page and '<option value="02-work"' in page and '<option value="general"' in page
    # Alpine's x-show owns the sub list's visibility; a `hidden` attribute would
    # keep it hidden after a module is chosen (found in the browser check).
    subs = re.findall(r'<label class="stage-field" x-show=[^>]*>', page)
    assert subs and not any(re.search(r"\shidden[\s>=]", label) for label in subs)
    original, before, head = note.read_bytes(), _commits(tmp_path), _git(tmp_path, "rev-parse", "HEAD")
    # Without a choice nothing is written.
    refused = _press(client, "alpha", src, "waiting")
    assert _notice(refused) == "Choose where to file it first."
    _nothing_written(tmp_path, head)
    response = _press(client, "alpha", src, "waiting", module="02-work", sub="general")
    assert response.status_code == 200, response.text
    dst = "alpha/02-work/active/mail.md"
    _assert_one_stage_commit(tmp_path, before, [dst])
    assert ("R", dst) in _head_files(tmp_path)
    text = (tmp_path / dst).read_text(encoding="utf-8")
    assert "\nstatus: waiting\n" in text and "\nsub: general\n" in text
    assert list(ReminderStore(directory).all()) == [dst]
    # The popup now shows the filed card.
    assert 'class="master-stage">Waiting<' in response.text and "02-work / general" in response.text
    _revert_restores(tmp_path, src, original, gone=dst)


def test_bare_inbox_item_with_a_forged_destination_is_refused(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    src = "alpha/00-inbox/active/mail.md"
    _note(tmp_path, "alpha", "00-inbox", "mail", sub="triage", status="active")
    _git_commit_all(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    for module, sub in (("zz-nowhere", ""), ("02-work", "nosuch"), ("../beta1", "")):
        _refused(_press(client, "alpha", src, "draft", module=module, sub=sub))
    _nothing_written(tmp_path, head)


def test_proposed_inbox_item_files_by_its_proposal_in_one_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    _seed(directory, Reminder(_SRC, "alpha", "Review it", datetime.now(timezone.utc) + timedelta(days=1)))
    original, before = (tmp_path / _SRC).read_bytes(), _commits(tmp_path)
    form = _popup_form(client, "alpha", _SRC)
    assert form["proposal_id"] == _PROPOSAL_ID and re.fullmatch(r"[0-9a-f]{64}", form["proposal_review_sha256"])
    assert "File to" not in client.get("/master/card/alpha", params={"path": _SRC}).text
    response = _press(client, "alpha", _SRC, "done", form)
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [_DST])
    assert "\nstatus: done\n" in (tmp_path / _DST).read_text(encoding="utf-8")
    # The classify record was consumed in the same transaction, never committed.
    assert not (tmp_path / "alpha" / "outbox" / f"{_PROPOSAL_ID}.yaml").exists()
    # Done resolves the reminder that followed the card.
    assert ReminderStore(directory).all() == {}
    _revert_restores(tmp_path, _SRC, original, gone=_DST)


def test_inbox_item_to_review_is_a_no_op_and_never_flags(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    src = "alpha/00-inbox/active/bare.md"
    _note(tmp_path, "alpha", "00-inbox", "bare", sub="triage", status="active")
    _git_commit_all(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    for path in (_SRC, src):
        response = _press(client, "alpha", path, "review", comment="Later")
        assert _notice(response).startswith(_UNCHANGED)
    _nothing_written(tmp_path, head)
    assert list(directory.iterdir()) == []


def test_a_stale_edit_is_refused_shows_the_newer_text_and_writes_nothing(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Opened text")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Plan")
    form = _popup_form(client, "alpha", path)
    # Someone edits the note elsewhere after the popup opened.
    note.write_text(note.read_text(encoding="utf-8").replace("Opened text", "Newer text"), encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "edited elsewhere")
    head, newer = _git(tmp_path, "rev-parse", "HEAD"), note.read_bytes()
    for stage in ("waiting", "review", "draft"):
        response = _press(client, "alpha", path, stage, form, body="My edit\n", comment="Mine")
        assert response.status_code == 200
        assert _notice(response) == _STALE
        textarea = re.search(r'<textarea[^>]*>(.*?)</textarea>', response.text, re.S).group(1)
        assert _html.unescape(textarea) == "Newer text\n"
        # The refreshed form binds the newer bytes.
        assert f'value="{hashlib.sha256(newer).hexdigest()}"' in response.text
    _nothing_written(tmp_path, head)
    assert note.read_bytes() == newer
    assert list(directory.iterdir()) == []
    assert not (tmp_path / "alpha" / "outbox").exists()


def test_forged_path_or_source_hash_is_refused_and_writes_nothing(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _note(tmp_path, "beta1", "02-work", "Beta", status="draft")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Plan")
    form = _popup_form(client, "alpha", path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    for forged in (_path("beta1", "Beta"), f"../{_path('beta1', 'Beta')}", "alpha/02-work/active/nope.md", ""):
        response = _press(client, "alpha", forged, "done", {**form, "path": forged})
        assert "no longer here" in response.text, forged
    for forged_hash in ("0" * 64, "not-a-hash"):
        response = _press(client, "alpha", path, "done", form, source_sha256=forged_hash)
        assert _notice(response) == _STALE
    # The hash is required (S7): an empty or missing one is a form refusal.
    for missing in ("", None):
        response = _press(client, "alpha", path, "done", form, source_sha256=missing)
        assert "E-REQUEST" in response.text and 'class="alert' in response.text
    _refused(_press(client, "alpha", path, "sideways", form))
    _nothing_written(tmp_path, head)
    assert list(directory.iterdir()) == []


def test_forged_proposal_tokens_are_refused_and_write_nothing(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    form = _popup_form(client, "alpha", _SRC)
    head = _git(tmp_path, "rev-parse", "HEAD")
    record = (tmp_path / "alpha" / "outbox" / f"{_PROPOSAL_ID}.yaml").read_bytes()
    # A well-formed digest that does not match is the same as a proposal that
    # changed: the newer card, bound to the record as it is, and no write.
    assert _notice(_press(client, "alpha", _SRC, "done", form, proposal_review_sha256="0" * 64)) == _CARD_CHANGED
    for overrides in (
        {"proposal_review_sha256": "nope"},
        {"proposal_review_sha256": None},
        {"proposal_id": "20260815T090703-" + "22" * 16},
        {"proposal_id": None},
    ):
        _refused(_press(client, "alpha", _SRC, "done", form, **overrides))
    _nothing_written(tmp_path, head)
    assert (tmp_path / "alpha" / "outbox" / f"{_PROPOSAL_ID}.yaml").read_bytes() == record
    assert sorted(p.name for p in (tmp_path / "alpha" / "outbox").iterdir()) == [f"{_PROPOSAL_ID}.yaml"]


@pytest.mark.parametrize("today", [_today(2), _today(-2), "2026-13-01", "tomorrow", ""])
def test_a_date_more_than_a_day_from_the_server_is_refused(tmp_path, tmp_path_factory, monkeypatch, today):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    response = _press(client, "alpha", _path("alpha", "Plan"), "done", body="Kept\n", comment="Kept too", today=today)
    assert response.status_code == 200
    assert "date" in _notice(response).lower()
    # What the owner typed is still there to send again.
    assert ">Kept\n</textarea>" in response.text and 'value="Kept too"' in response.text
    _nothing_written(tmp_path, head)


def test_a_header_the_editor_cannot_place_is_refused_in_plain_words(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft", alias="&anchor value")
    _git_commit_all(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    response = _press(client, "alpha", _path("alpha", "Plan"), "done")
    assert _notice(response) == _HEADER
    assert "form could not be read" not in response.text.lower() and "bad request" not in response.text.lower()
    _nothing_written(tmp_path, head)


def test_a_double_submit_makes_one_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _note(tmp_path, "alpha", "00-inbox", "mail", sub="triage", status="active")
    _git_commit_all(tmp_path)
    before = _commits(tmp_path)
    form = _popup_form(client, "alpha", _path("alpha", "Plan"))
    first = _press(client, "alpha", _path("alpha", "Plan"), "done", form)
    second = _press(client, "alpha", _path("alpha", "Plan"), "done", form)
    assert _commits(tmp_path) == before + 1
    assert _notice(first) != _STALE and _notice(second) == _STALE
    src = "alpha/00-inbox/active/mail.md"
    form = _popup_form(client, "alpha", src)
    _press(client, "alpha", src, "draft", form, module="02-work")
    again = _press(client, "alpha", src, "draft", form, module="02-work")
    assert _commits(tmp_path) == before + 2
    assert "no longer here" in again.text


def test_stage_requires_csrf(tmp_path, monkeypatch):
    import pyotp
    from app.auth import AuthStore

    main, _client_ = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Plan")
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    store = AuthStore(directory)
    store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(directory))
    monkeypatch.setenv("ONEOS_AUTH_MODE", "required")
    monkeypatch.setenv("ONEOS_ORIGIN", "https://localhost:8443")
    origin = {"Origin": "https://localhost:8443", "HX-Request": "true"}
    sha = hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()
    data = {"path": path, "source_sha256": sha, "stage": "done", "today": _today()}
    before = _commits(tmp_path)
    with TestClient(main.app, base_url="https://localhost:8443", follow_redirects=False) as client:
        assert client.post("/master/stage/alpha", data=data, headers=origin).status_code == 401
        token = store.login("synthetic owner password", pyotp.TOTP(secret).now())
        client.cookies.set("__Host-oneos", token)
        csrf = store.session(token)
        for headers in (origin, {**origin, "X-CSRF-Token": "wrong"}):
            assert client.post("/master/stage/alpha", data=data, headers=headers).status_code == 403
        assert _commits(tmp_path) == before
        ok = client.post("/master/stage/alpha", data=data, headers={**origin, "X-CSRF-Token": csrf})
        assert ok.status_code == 200 and ok.headers["HX-Trigger"] == "master-refresh", ok.text
    assert _commits(tmp_path) == before + 1


def test_popup_form_binds_the_shown_body_to_the_hash_of_the_same_read(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="\n\nDecision <b>text</b>")
    path = _path("alpha", "Plan")
    reads = []
    real = main.note_snapshot

    def counted(scope, relative):
        reads.append(relative)
        return real(scope, relative)

    monkeypatch.setattr(main, "note_snapshot", counted)
    page = client.get("/master/card/alpha", params={"path": path}).text
    assert reads == [path]
    form = _popup_form(client, "alpha", path)
    assert form["source_sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert form["body"] == "Decision <b>text</b>\n"
    assert "<b>text</b>" not in page
    for key, label in (("draft", "Save as Draft"), ("waiting", "Waiting"), ("review", "To review"), ("done", "Done")):
        assert re.search(rf'<button type="submit" name="stage" value="{key}"[^>]*>{label}</button>', page), label
    assert 'name="comment"' in page and 'maxlength="500"' in page
    # The current stage is marked; an Enter in the comment box cannot submit a stage.
    assert re.search(r'<button type="submit" name="stage" value="draft"[^>]*aria-current="true"', page)
    assert re.search(r'<form class="stage-form"[^>]*>\s*<button type="submit" disabled', page)


def test_stage_moves_and_comment_css_use_existing_tokens():
    rules = _CSS.split("/* --- stage form --- */")[1].split("/* --- end stage form --- */")[0]
    assert "var(--" in rules
    assert "#" not in re.sub(r"/\*.*?\*/", "", rules, flags=re.S).replace("#master", "")


def test_a_header_the_editor_cannot_split_is_shown_read_only(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Odd", status="draft", alias="x---y", body="Kept <body>")
    page = client.get("/master/card/alpha", params={"path": _path("alpha", "Odd")}).text
    assert "stage-form" not in page and "<textarea" not in page
    assert '<pre class="master-body">Kept &lt;body&gt;' in page
    assert "This note&#39;s header can&#39;t be edited safely here. Edit it in the vault." in page


# --- review follow-ups (Task 4, fix round 1) ----------------------------------


def test_reminder_save_and_remove_swap_only_the_reminder_box(tmp_path, tmp_path_factory, monkeypatch):
    """Saving or removing a reminder must not re-render the stage form: an
    unsaved draft edit and comment stay in the browser untouched."""
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)
    page = client.get("/master/card/alpha", params={"path": path}).text
    box = re.search(r'<div id="master-reminder".*?</div>\s*</article>', page, re.S)
    assert box, page
    # Every reminder control (Save, Enter in a field, Remove) targets the box only.
    targets = re.findall(r'hx-post="/master/reminder/[^"]*"\s+hx-target="([^"]*)" hx-swap="([^"]*)"', page)
    assert targets and set(targets) == {("#master-reminder", "outerHTML")}, targets
    assert "stage-form" not in box.group(0)
    saved = _save(client, "alpha", path)
    assert saved.headers["HX-Trigger"] == "master-refresh"  # the strip still redraws
    assert saved.text.lstrip().startswith('<div id="master-reminder"')
    assert "stage-form" not in saved.text and "<textarea" not in saved.text
    assert "Remove" in saved.text and "Save reminder" in saved.text
    # A refusal keeps what was typed in the box.
    refused = _save(client, "alpha", path, subject="Second\nline", date="2099-12-01")
    _reminder_refused(refused)
    assert 'value="2099-12-01"' in refused.text and "Save reminder" in refused.text
    removed = _remove(client, "alpha", path)
    assert removed.headers["HX-Trigger"] == "master-refresh"
    assert removed.text.lstrip().startswith('<div id="master-reminder"')
    assert "stage-form" not in removed.text and ReminderStore(directory).all() == {}


def test_reminder_routes_do_not_read_the_note(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory, path = _setup(tmp_path, tmp_path_factory, monkeypatch)

    def no_read(scope, relative):
        raise AssertionError("a reminder route read the note")

    monkeypatch.setattr(main, "note_snapshot", no_read)
    assert _save(client, "alpha", path).headers["HX-Trigger"] == "master-refresh"
    assert _remove(client, "alpha", path).headers["HX-Trigger"] == "master-refresh"


@pytest.mark.parametrize("raw", ["A\rB\n", "A\x00B\n"])
def test_a_body_the_browser_would_rewrite_is_not_offered_for_editing(
    tmp_path, tmp_path_factory, monkeypatch, raw
):
    """A lone CR or a NUL cannot survive a round trip through a textarea (the
    HTML parser turns them into LF and U+FFFD). A press must not rewrite them."""
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Raw", status="draft", body="")
    note.write_bytes(note.read_bytes() + raw.encode())
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Raw"), _commits(tmp_path)
    page = client.get("/master/card/alpha", params={"path": path}).text
    assert "stage-form" in page and "<textarea" not in page
    # No body is sent, so none can be applied (not even an empty one).
    assert 'name="body_sent"' not in page
    assert "Edit it in the vault" in page
    sha = hashlib.sha256(note.read_bytes()).hexdigest()
    base = {"path": path, "source_sha256": sha, "today": _today()}
    # What a browser would post had it shown the body in a textarea.
    browser = raw.replace("\r", "\r\n").replace("\x00", "�")
    response = client.post("/master/stage/alpha", data={**base, "stage": "waiting", "body": browser})
    assert response.status_code == 200, response.text
    assert _commits(tmp_path) == before + 1
    text = note.read_bytes().decode()
    assert text.endswith(raw) and "\nstatus: waiting\n" in text
    # A real edit of such a body is refused, never saved with the characters changed.
    sha = hashlib.sha256(note.read_bytes()).hexdigest()
    head, status = _git(tmp_path, "rev-parse", "HEAD"), _git(tmp_path, "status", "--porcelain")
    refused = client.post("/master/stage/alpha", data={**base, "source_sha256": sha, "stage": "waiting",
                                                         "body": "Rewritten\n"})
    assert "Edit it in the vault" in _notice(refused)
    assert _git(tmp_path, "rev-parse", "HEAD") == head
    assert _git(tmp_path, "status", "--porcelain") == status
    assert note.read_bytes().decode().endswith(raw)


def test_a_read_failure_after_the_commit_still_reports_the_move(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Plan"), _commits(tmp_path)
    form = _popup_form(client, "alpha", path)
    real = main.entity_cards
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:  # the listing after the commit
            raise main.NoteUnreadable("synthetic")
        return real(*args, **kwargs)

    monkeypatch.setattr(main, "entity_cards", flaky)
    response = _press(client, "alpha", path, "done", form)
    assert _commits(tmp_path) == before + 1
    assert response.headers.get("HX-Trigger") == "master-refresh"
    assert "Moved. Refresh to see the note." in response.text
    assert 'class="alert' not in response.text


def test_proposed_destination_is_shown_from_the_reviewed_record(tmp_path, tmp_path_factory, monkeypatch):
    import dataclasses

    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    real = main.get_proposal_review

    def replaced(scope, proposal_id):
        review = real(scope, proposal_id)
        return dataclasses.replace(review, value=dataclasses.replace(review.value, module="01-core", sub=None))

    monkeypatch.setattr(main, "get_proposal_review", replaced)
    page = client.get("/master/card/alpha", params={"path": _SRC}).text
    # The digest the buttons carry and the destination shown come from one read.
    assert "proposed for 01-core" in page and "02-work" not in page


def test_flag_store_failure_after_the_commit_keeps_the_edit_and_says_so(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft", body="Old body")
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Plan"), _commits(tmp_path)

    def unavailable(self, flag):
        raise main.ReminderStoreUnavailable()

    monkeypatch.setattr(ReminderStore, "flag", unavailable)
    response = _press(client, "alpha", path, "review", body="New body\n")
    _assert_one_stage_commit(tmp_path, before, [path])
    assert note.read_text(encoding="utf-8").endswith("---\nNew body\n")
    assert _notice(response) == (
        "The edit was saved, but the To review flag could not be stored. Press To review again."
    )
    assert response.headers["HX-Trigger"] == "master-refresh"
    assert ReminderStore(directory).flags() == {}


def test_done_with_an_unsafe_store_still_commits_and_keeps_the_reminder(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _git_commit_all(tmp_path)
    path, before = _path("alpha", "Chase"), _commits(tmp_path)
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)))
    form = _popup_form(client, "alpha", path)
    directory.chmod(0o755)
    try:
        response = _press(client, "alpha", path, "done", form)
    finally:
        directory.chmod(0o700)
    _assert_one_stage_commit(tmp_path, before, [path])
    assert _notice(response) == "Moved to Done."
    assert set(ReminderStore(directory).all()) == {path}  # nothing lost; shown as moved or removed


def test_rekey_with_an_unsafe_store_still_files_and_keeps_the_reminder(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    src = "alpha/00-inbox/active/mail.md"
    _note(tmp_path, "alpha", "00-inbox", "mail", sub="triage", status="active")
    _git_commit_all(tmp_path)
    _seed(directory, Reminder(src, "alpha", "Answer it", datetime.now(timezone.utc) + timedelta(days=1)))
    form, before = _popup_form(client, "alpha", src), _commits(tmp_path)
    directory.chmod(0o755)
    try:
        response = _press(client, "alpha", src, "waiting", form, module="02-work")
    finally:
        directory.chmod(0o700)
    _assert_one_stage_commit(tmp_path, before, ["alpha/02-work/active/mail.md"])
    assert _notice(response) == "Moved to Waiting."
    assert set(ReminderStore(directory).all()) == {src}


def test_proposed_card_with_an_edited_body_is_one_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    original, before = (tmp_path / _SRC).read_bytes(), _commits(tmp_path)
    response = _press(client, "alpha", _SRC, "waiting", body="Edited before filing\n", comment="Filed by hand")
    assert response.status_code == 200, response.text
    # The edit is large next to this tiny note, so git records a delete and an add.
    _assert_one_stage_commit(tmp_path, before, [_SRC, _DST])
    assert ("D", _SRC) in _head_files(tmp_path) and ("A", _DST) in _head_files(tmp_path)
    text = (tmp_path / _DST).read_text(encoding="utf-8")
    assert "\nstatus: waiting\n" in text and "\nsub: general\n" in text
    assert text.endswith(f"---\nEdited before filing\n\nComment ({_today()}): Filed by hand\n")
    assert not (tmp_path / "alpha" / "outbox" / f"{_PROPOSAL_ID}.yaml").exists()
    _revert_restores(tmp_path, _SRC, original, gone=_DST)


def test_tomorrow_on_the_device_is_accepted(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    before = _commits(tmp_path)
    tomorrow = _today(1)
    response = _press(client, "alpha", _path("alpha", "Plan"), "waiting", comment="Late", today=tomorrow)
    assert response.status_code == 200 and _notice(response) == "Moved to Waiting."
    assert _commits(tmp_path) == before + 1
    text = note.read_text(encoding="utf-8")
    assert f"\nupdated: {tomorrow}\n" in text and f"Comment ({tomorrow}): Late" in text


# --- Drag and drop on the home page (Task 5) ----------------------------------------

_DRAG_JS = Path(__file__).resolve().parents[1] / "static" / "master_drag.js"


def _card_tags(html: str) -> list[dict]:
    """Each master card's opening tag as a dict of its attributes (unescaped)."""
    tags = re.findall(r'<button type="button" class="board-card master-card"([^>]*)>', html, re.S)
    return [
        {name: _html.unescape(value) for name, value in re.findall(r'([a-z0-9-]+)="([^"]*)"', tag)}
        for tag in tags
    ]


def test_cards_carry_what_a_drop_needs(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    cards = _card_tags(client.get("/").text)
    assert len(cards) == 1
    card = cards[0]
    assert card["draggable"] == "true"
    assert card["data-entity"] == "alpha"
    assert card["data-path"] == _card_path(main, "alpha", "Plan")
    assert card["data-lane"] == "filed"
    assert card["data-source-sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert card["hx-get"].startswith("/master/card/alpha?path=")


def test_board_card_sha256_is_the_hash_of_the_bytes_read(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    filed = _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    crlf = _note(tmp_path, "alpha", "02-work", "Windows", status="waiting")
    crlf.write_bytes(crlf.read_bytes().replace(b"\n", b"\r\n"))
    inbox = tmp_path / "alpha" / "00-inbox" / "active" / "bare.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text("---\ntype: inbox-item\ntitle: Bare\nsub: triage\n---\nbody\n", encoding="utf-8")
    cards = main.entity_cards(main.build_scope("alpha"), main.Vault(main.current_catalog()))
    by_title = {c.title: c for c in (*cards.inbox, *cards.filed)}
    assert by_title["Plan"].sha256 == hashlib.sha256(filed.read_bytes()).hexdigest()
    assert by_title["Windows"].sha256 == hashlib.sha256(crlf.read_bytes()).hexdigest()
    assert by_title["Windows"].status == "waiting"  # CRLF still parses as it always did
    assert by_title["Bare"].sha256 == hashlib.sha256(inbox.read_bytes()).hexdigest()


def test_the_card_hash_is_the_one_the_note_snapshot_gives(tmp_path, monkeypatch):
    """A direct drop posts the card's hash; the route compares it with note_snapshot."""
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    scope = main.build_scope("alpha")
    card = next(c for c in main.entity_cards(scope, main.Vault(main.current_catalog())).filed)
    assert card.sha256 == main.note_snapshot(scope, card.relative_path).sha256


def test_proposed_and_inbox_cards_are_marked_with_their_lane(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    bare = tmp_path / "alpha" / "00-inbox" / "active" / "bare.md"
    bare.parent.mkdir(parents=True, exist_ok=True)
    bare.write_text("---\ntype: inbox-item\ntitle: Bare\nsub: triage\n---\nbody\n", encoding="utf-8")
    _git_commit_all(tmp_path)
    by_lane = {c["data-lane"]: c for c in _card_tags(client.get("/").text)}
    assert by_lane["inbox"]["data-source-sha256"] == hashlib.sha256(bare.read_bytes()).hexdigest()


def test_proposed_card_has_lane_proposed_and_no_invented_hash(tmp_path, monkeypatch):
    main, _v = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md")
    client = TestClient(main.app)
    cards = _card_tags(client.get("/").text)
    assert [c["data-lane"] for c in cards] == ["proposed"]
    assert cards[0]["data-source-sha256"] == ""


def test_each_column_is_a_drop_target(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    assert re.findall(r'<section class="board-column master-col[^"]*"\s+data-stage="([a-z]+)"', html) == [
        "draft", "waiting", "review", "done",
    ]
    assert '<script defer src="/static/master_drag.js"></script>' in html
    # A swap into the popup (a drop's reply included) opens the dialog.
    assert "@htmx:after-swap=\"if (!$el.open) $el.showModal()\"" in html


def test_drag_script_never_writes_markup_and_sends_through_htmx():
    source = _DRAG_JS.read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "fetch(",
                      "localStorage", "sessionStorage", "import ", "require(", "http:", "https:"):
        assert forbidden not in source, forbidden
    # The CSRF header comes from the page head's htmx configRequest handler.
    head = (Path(__file__).resolve().parents[1] / "templates" / "_head.html").read_text(encoding="utf-8")
    assert "htmx:configRequest" in head and "X-CSRF-Token" in head
    assert source.count("htmx.ajax(") == 2


def test_drag_css_reuses_the_boards_drop_style_and_is_pointer_gated():
    css = (Path(__file__).resolve().parents[1] / "static" / "app.css").read_text(encoding="utf-8")
    assert ".board-column.drop-over {" in css  # the master columns are board columns
    assert "@media (min-width: 768px) and (hover: hover) and (pointer: fine)" in css
    assert re.search(r'\.master-card\.is-dragging \{ opacity', css)


def test_board_page_keeps_its_own_drag_behaviour():
    template = (Path(__file__).resolve().parents[1] / "templates" / "board.html").read_text(encoding="utf-8")
    assert "master_drag" not in template
    assert "drop($event, $el.dataset.module, $el.dataset.sub)" in template


def test_a_drop_post_with_a_stale_hash_is_refused_and_writes_nothing(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    card = _card_tags(client.get("/").text)[0]
    # The note is edited elsewhere after the page was drawn.
    note.write_text(note.read_text(encoding="utf-8") + "Added elsewhere\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "edited elsewhere")
    head = _git(tmp_path, "rev-parse", "HEAD")
    response = client.post("/master/stage/alpha", data={
        "path": card["data-path"], "source_sha256": card["data-source-sha256"],
        "stage": "done", "today": _today(),
    })
    assert response.status_code == 200
    assert "This note changed since you opened it" in response.text
    assert "Added elsewhere" in response.text  # the popup shows the newer text
    _nothing_written(tmp_path, head)
    assert "\nstatus: draft\n" in note.read_text(encoding="utf-8")


def test_a_drop_post_with_the_cards_own_values_moves_it_in_one_commit(tmp_path, tmp_path_factory, monkeypatch):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    note = _note(tmp_path, "alpha", "02-work", "Plan", status="draft")
    _git_commit_all(tmp_path)
    card = _card_tags(client.get("/").text)[0]
    before = _commits(tmp_path)
    response = client.post("/master/stage/alpha", data={
        "path": card["data-path"], "source_sha256": card["data-source-sha256"],
        "stage": "done", "today": _today(),
    })
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "master-refresh"
    _assert_one_stage_commit(tmp_path, before, [card["data-path"]])
    assert "\nstatus: done\n" in note.read_text(encoding="utf-8")


def _node_or_skip():
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node


_DRAG_HARNESS = r"""
const assert = require('assert');
const path = process.argv[2];
const state = {canDrag: true, calls: [], listeners: {}, fail: false};

function classList() {
  const set = new Set();
  return {add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c), has: (c) => set.has(c)};
}
function column(stage) {
  const col = {dataset: {stage}, classList: classList(), parent: null, children: []};
  col.closest = (sel) => (sel === '[data-stage]' ? col : null);
  col.contains = (node) => node === col || (node && node.parent === col);
  return col;
}
function card(col, data, extra) {
  const attrs = {draggable: 'true'};
  const c = {
    dataset: Object.assign({entity: 'alpha', path: 'alpha/02-work/active/Plan.md', sourceSha256: 'a'.repeat(64), lane: 'filed'}, data),
    classList: classList(), parent: col,
    getAttribute: (n) => (n === 'hx-get' ? '/master/card/alpha?path=alpha%2F02-work%2Factive%2FPlan.md' : attrs[n]),
    setAttribute: (n, v) => { attrs[n] = v; },
    removeAttribute: (n) => { delete attrs[n]; },
    attrs,
  };
  c.closest = (sel) => (sel.startsWith('.master-card') ? c : (col ? col.closest(sel) : null));
  return c;
}
const cols = {draft: column('draft'), waiting: column('waiting'), review: column('review'), done: column('done')};
let cards = [];

global.window = {matchMedia: () => ({matches: state.canDrag, addEventListener() {}})};
global.document = {
  readyState: 'complete',
  addEventListener: (type, fn) => { state.listeners[type] = fn; },
  querySelectorAll: (sel) => {
    if (sel === '.drop-over') return Object.values(cols).filter((c) => c.classList.has('drop-over'));
    if (sel === '.is-dragging') return cards.filter((c) => c.classList.has('is-dragging'));
    if (sel.startsWith('.master-card')) return cards;
    return [];
  },
};
global.htmx = {ajax: (verb, url, opts) => { state.calls.push({verb, url, opts}); return state.fail && verb === 'POST' ? Promise.reject(new Error('down')) : Promise.resolve(); }};
require(path);

function newTransfer() {
  return {data: {}, setData(k, v) { this.data[k] = v; }, get types() { return Object.keys(this.data); }};
}
// One transfer object per drag, shared by the events of that drag (as in a browser).
state.dt = newTransfer();
function ev(target, extra) {
  const e = Object.assign({target, prevented: false, dataTransfer: state.dt}, extra);
  e.preventDefault = () => { e.prevented = true; };
  return e;
}
function start(c) {
  state.dt = newTransfer();
  state.listeners.dragstart(ev(c));
}
async function drag(c, stage) {
  start(c);
  const over = ev(cols[stage]);
  state.listeners.dragover(over);
  const drop = ev(cols[stage]);
  state.listeners.drop(drop);
  state.listeners.dragend(ev(c));
  await new Promise((r) => setImmediate(r));
  return {over, drop};
}
function reset() { state.calls.length = 0; state.canDrag = true; state.fail = false; }
const POPUP = '/master/card/alpha?path=alpha%2F02-work%2Factive%2FPlan.md';

(async () => {
  // A filed card dropped on Draft or Done posts its own path and hash with the browser's date.
  for (const [from, to] of [['draft', 'done'], ['done', 'draft'], ['review', 'done'], ['waiting', 'draft']]) {
    reset();
    const c = card(cols[from]); cards = [c];
    const {over} = await drag(c, to);
    assert.strictEqual(over.prevented, true, 'a legal target accepts the drop');
    assert.strictEqual(state.calls.length, 1);
    const call = state.calls[0];
    assert.strictEqual(call.verb, 'POST');
    assert.strictEqual(call.url, '/master/stage/alpha');
    assert.strictEqual(call.opts.target, '#master-popup');
    const d = new Date();
    const today = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    assert.deepStrictEqual(call.opts.values, {path: 'alpha/02-work/active/Plan.md', source_sha256: 'a'.repeat(64), stage: to, today});
  }
  // Waiting and To review open the popup for any card and never post.
  for (const lane of ['filed', 'inbox', 'proposed']) {
    for (const to of ['waiting', 'review']) {
      reset();
      const c = card(cols.draft, {lane}); cards = [c];
      await drag(c, to);
      assert.deepStrictEqual(state.calls.map((x) => [x.verb, x.url]), [['GET', POPUP]], lane + ' -> ' + to);
      assert.strictEqual(state.calls[0].opts.target, '#master-popup');
    }
  }
  // A bare inbox card, or one with a pending classify proposal, opens the popup instead of moving.
  for (const lane of ['inbox', 'proposed']) {
    for (const to of ['draft', 'done']) {
      reset();
      const c = card(cols.waiting, {lane, sourceSha256: lane === 'proposed' ? '' : 'b'.repeat(64)}); cards = [c];
      await drag(c, to);
      assert.deepStrictEqual(state.calls.map((x) => [x.verb, x.url]), [['GET', POPUP]], lane + ' -> ' + to);
    }
  }
  // A filed card without a hash cannot post: the popup opens.
  reset();
  let c = card(cols.draft, {sourceSha256: ''}); cards = [c];
  await drag(c, 'done');
  assert.deepStrictEqual(state.calls.map((x) => x.verb), ['GET']);
  // A card from the "another status" list has no column and can post to Draft or Done.
  reset();
  c = card(null); cards = [c];
  await drag(c, 'done');
  assert.deepStrictEqual(state.calls.map((x) => x.verb), ['POST']);
  // A drop on the card's own column does nothing, and is not offered as a target.
  for (const lane of ['filed', 'inbox', 'proposed']) {
    for (const stage of ['draft', 'waiting', 'review', 'done']) {
      reset();
      c = card(cols[stage], {lane}); cards = [c];
      const {over, drop} = await drag(c, stage);
      assert.deepStrictEqual(state.calls, [], lane + ' on ' + stage);
      assert.strictEqual(over.prevented, false);
      assert.strictEqual(cols[stage].classList.has('drop-over'), false);
    }
  }
  // The drop style shows over another column while dragging and is gone afterwards.
  reset();
  c = card(cols.draft); cards = [c];
  start(c);
  assert.strictEqual(c.classList.has('is-dragging'), true);
  state.listeners.dragover(ev(cols.done));
  assert.strictEqual(cols.done.classList.has('drop-over'), true);
  state.listeners.dragover(ev(cols.draft));
  assert.strictEqual(cols.draft.classList.has('drop-over'), false);
  state.listeners.dragleave(ev(cols.done, {relatedTarget: null}));
  assert.strictEqual(cols.done.classList.has('drop-over'), false);
  state.listeners.dragover(ev(cols.waiting));
  state.listeners.dragend(ev(c));
  assert.strictEqual(cols.waiting.classList.has('drop-over'), false);
  assert.strictEqual(c.classList.has('is-dragging'), false);
  assert.deepStrictEqual(state.calls, []);
  // A drop that did not start from a card (a file, text) does nothing.
  reset();
  const stray = ev(cols.done, {dataTransfer: newTransfer()});
  state.listeners.dragover(stray);
  state.listeners.drop(stray);
  assert.strictEqual(stray.prevented, false);
  assert.deepStrictEqual(state.calls, []);
  // The drag uses the card as it was when picked up: a morph that rewrites the node mid-drag changes nothing.
  for (const to of ['done', 'waiting']) {
    reset();
    c = card(cols.draft); cards = [c];
    start(c);
    Object.assign(c.dataset, {entity: 'beta', path: 'beta/02-work/active/Other.md', sourceSha256: 'c'.repeat(64), lane: 'inbox'});
    c.getAttribute = (n) => (n === 'hx-get' ? '/master/card/beta?path=other' : c.attrs[n]);
    state.listeners.dragover(ev(cols[to]));
    state.listeners.drop(ev(cols[to]));
    state.listeners.dragend(ev(c));
    await new Promise((r) => setImmediate(r));
    assert.strictEqual(state.calls.length, 1, to);
    if (to === 'done') {
      assert.strictEqual(state.calls[0].verb, 'POST');
      assert.strictEqual(state.calls[0].url, '/master/stage/alpha');
      assert.strictEqual(state.calls[0].opts.values.path, 'alpha/02-work/active/Plan.md');
      assert.strictEqual(state.calls[0].opts.values.source_sha256, 'a'.repeat(64));
    } else {
      assert.deepStrictEqual([state.calls[0].verb, state.calls[0].url], ['GET', POPUP]);
    }
  }
  // The drag is tagged: a drop that does not carry the origin tag does nothing, even with a leftover snapshot.
  reset();
  c = card(cols.draft); cards = [c];
  start(c);
  assert.deepStrictEqual(Object.keys(state.dt.data), ['application/x-oneos-card']);
  assert.strictEqual(state.dt.data['application/x-oneos-card'], 'alpha');
  const foreignOver = ev(cols.done, {dataTransfer: newTransfer()});
  state.listeners.dragover(foreignOver);
  assert.strictEqual(foreignOver.prevented, false);
  assert.strictEqual(cols.done.classList.has('drop-over'), false);
  const foreignDrop = ev(cols.done, {dataTransfer: newTransfer()});
  state.listeners.drop(foreignDrop);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(foreignDrop.prevented, false);
  assert.deepStrictEqual(state.calls, []);
  state.listeners.dragend(ev(c));
  // The snapshot is gone after dragend and after every drop.
  state.listeners.drop(ev(cols.done, {dataTransfer: state.dt}));
  assert.deepStrictEqual(state.calls, []);
  start(c); state.listeners.drop(ev(cols.draft)); // own column: nothing, and the snapshot is cleared
  state.listeners.drop(ev(cols.done, {dataTransfer: state.dt}));
  assert.deepStrictEqual(state.calls, []);
  // Phone / touch / no hover: no drag handles, no drag, and a stray drop does nothing.
  reset(); state.canDrag = false;
  c = card(cols.draft); cards = [c];
  window.OneOSMasterDrag.sync();
  assert.strictEqual('draggable' in c.attrs, false);
  const refused = ev(c);
  state.listeners.dragstart(refused);
  assert.strictEqual(refused.prevented, true);
  assert.strictEqual(c.classList.has('is-dragging'), false);
  state.listeners.drop(ev(cols.done));
  assert.deepStrictEqual(state.calls, []);
  state.canDrag = true;
  window.OneOSMasterDrag.sync();
  assert.strictEqual(c.attrs.draggable, 'true');
  // A failed send falls back to the popup (the note as it now stands).
  reset(); state.fail = true;
  c = card(cols.draft); cards = [c];
  await drag(c, 'done');
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(state.calls.map((x) => x.verb), ['POST', 'GET']);
  // Two drops in a row: the second waits until the first settles.
  reset();
  let release;
  global.htmx.ajax = (verb, url, opts) => { state.calls.push({verb, url}); return new Promise((r) => { release = r; }); };
  c = card(cols.draft); cards = [c];
  start(c); state.listeners.drop(ev(cols.done));
  start(c); state.listeners.drop(ev(cols.done));
  assert.strictEqual(state.calls.length, 1);
  release();
  await new Promise((r) => setImmediate(r));
  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_drop_script_decides_where_a_card_goes(tmp_path):
    node = _node_or_skip()
    harness = tmp_path / "harness.js"
    harness.write_text(_DRAG_HARNESS)
    result = subprocess.run([node, str(harness), str(_DRAG_JS)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


# --- a stranded stage record approved on the Outbox screen ----------------------


def _strand_stage(root, src, **kwargs):
    """A pending stage record, as left when a press's reject failed after a
    refusal. Returns the POST /outbox approve form for it."""
    from app.scope import Scope
    from app.stage import propose_stage

    source_sha256 = hashlib.sha256((root / src).read_bytes()).hexdigest()
    proposal_id, digest = propose_stage(
        Scope(root, "alpha"), src=src, source_sha256=source_sha256, updated=_today(), **kwargs
    )
    return {"id": proposal_id, "review_sha256": digest}


def test_outbox_approval_of_a_stage_record_to_done_clears_the_flag_and_resolves(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _note(tmp_path, "alpha", "02-work", "Other", status="waiting")
    _git_commit_all(tmp_path)
    path, other = _path("alpha", "Chase"), _path("alpha", "Other")
    due = datetime.now(timezone.utc) + timedelta(days=1)
    _seed(directory, _reminder("alpha", "Chase", due), _reminder("alpha", "Other", due))
    store = ReminderStore(directory)
    store.flag(_flag("alpha", "Chase"))
    store.flag(_flag("alpha", "Other"))
    form = _strand_stage(tmp_path, path, set_status="done")
    before = _commits(tmp_path)
    response = client.post("/outbox/alpha/approve", data=form)
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    assert "\nstatus: done\n" in (tmp_path / path).read_text(encoding="utf-8")
    # As after a Done press: the flag is cleared and the reminder resolved.
    assert set(store.flags()) == {other}
    assert set(store.all()) == {other}


def test_outbox_approval_of_a_stage_record_moving_status_clears_the_flag_keeps_the_reminder(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="draft")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Chase")
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)))
    store = ReminderStore(directory)
    store.flag(_flag("alpha", "Chase"))
    response = client.post("/outbox/alpha/approve", data=_strand_stage(tmp_path, path, set_status="waiting"))
    assert response.status_code == 200, response.text
    assert store.flags() == {}
    assert set(store.all()) == {path}


def test_outbox_approval_of_an_inbox_stage_record_moves_the_reminder(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    src, dst = "alpha/00-inbox/active/mail.md", "alpha/02-work/active/mail.md"
    _note(tmp_path, "alpha", "00-inbox", "mail", sub="triage", status="active")
    _git_commit_all(tmp_path)
    _seed(directory, Reminder(src, "alpha", "Answer it", datetime.now(timezone.utc) + timedelta(days=1)))
    form = _strand_stage(tmp_path, src, set_status="waiting", module="02-work", sub="general")
    before = _commits(tmp_path)
    response = client.post("/outbox/alpha/approve", data=form)
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [dst])
    found = ReminderStore(directory).all()
    assert list(found) == [dst] and found[dst].subject == "Answer it"


def test_outbox_approval_of_a_stage_record_survives_an_unsafe_store(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Chase", status="waiting")
    _git_commit_all(tmp_path)
    path = _path("alpha", "Chase")
    _seed(directory, _reminder("alpha", "Chase", datetime.now(timezone.utc) + timedelta(days=1)))
    form = _strand_stage(tmp_path, path, set_status="done")
    before = _commits(tmp_path)
    directory.chmod(0o755)  # an unsafe store: the follow-up cannot run
    response = client.post("/outbox/alpha/approve", data=form)
    assert response.status_code == 200, response.text
    _assert_one_stage_commit(tmp_path, before, [path])
    directory.chmod(0o700)
    assert set(ReminderStore(directory).all()) == {path}  # nothing lost


# --- a classify record that changed after the popup opened ---------------------


def test_a_changed_proposal_shows_the_newer_card_and_writes_nothing(
    tmp_path, tmp_path_factory, monkeypatch
):
    main, client, directory = _stage_setup(tmp_path, tmp_path_factory, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md", status="active", full=True)
    form = _popup_form(client, "alpha", _SRC)
    # The pending classification is rewritten after the popup opened.
    record = tmp_path / "alpha" / "outbox" / f"{_PROPOSAL_ID}.yaml"
    record.write_text(record.read_text(encoding="utf-8").replace("sub: general\n", "sub:\n"), encoding="utf-8")
    head, newer = _git(tmp_path, "rev-parse", "HEAD"), record.read_bytes()
    response = _press(client, "alpha", _SRC, "done", form, comment="Mine")
    assert response.status_code == 200
    assert _notice(response) == _CARD_CHANGED
    assert "E-REVIEW" not in response.text and 'class="alert' not in response.text
    # The refreshed form binds the newer record, and keeps what was typed.
    again = {
        name: _html.unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([a-z0-9_]+)" value="([^"]*)"', response.text)
    }
    assert again["proposal_review_sha256"] == hashlib.sha256(newer).hexdigest()
    assert 'value="Mine"' in response.text
    _nothing_written(tmp_path, head)
    assert record.read_bytes() == newer
    assert sorted(p.name for p in record.parent.iterdir()) == [record.name]
    assert list(directory.iterdir()) == []
