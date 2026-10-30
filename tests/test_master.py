"""Master view reader over a synthetic vault. No live registry, no instance names."""
import hashlib
import re
import importlib
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from app.board import NoteUnreadable
from app.master import due_label, load_master
from app.reminders import Reminder, ReminderStore
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
    real_read = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == locked or self.resolve() == locked.resolve():
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
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


def test_master_reads_without_writing(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch, _ARCHETYPES_WITH_SUB)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _inbox_with_proposal(tmp_path, "beta1", "marker.md")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=True,
                       now=datetime(2026, 10, 29, 9, tzinfo=timezone.utc), reminders={})
    assert page.failures == () and len(page.columns[2].cards) == 1
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


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


def test_popup_shows_the_note_body_read_only(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", body="Decision <b>text</b>", status="draft")
    path = _card_path(main, "alpha", "Spec")
    response = client.get("/master/card/alpha", params={"path": path})
    assert response.status_code == 200
    assert "Decision &lt;b&gt;text&lt;/b&gt;" in response.text and ">Draft<" in response.text
    assert ">Alpha<" in response.text and "ent-1" in response.text
    assert "<b>text</b>" not in response.text
    assert "<form" not in response.text and "hx-post" not in response.text and "<textarea" not in response.text


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
        raise AssertionError(f"note_body read a refused path: {path}")

    monkeypatch.setattr(main, "note_body", no_body)
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
        assert 'hx-trigger="every 60s"' in tag and 'hx-select="#master"' in tag
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
    """Let the listing read the note, then make only the popup's body read fail."""
    state = {"armed": False}
    real_cards, real_read = main.entity_cards, Path.read_text

    def cards(*args, **kwargs):
        try:
            return real_cards(*args, **kwargs)
        finally:
            state["armed"] = True

    def read_text(self, *args, **kwargs):
        if state["armed"] and self.suffix == ".md":
            raise error
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(main, "entity_cards", cards)
    monkeypatch.setattr(Path, "read_text", read_text)


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


def test_orphan_attention_card_is_not_a_link_or_button(tmp_path, tmp_path_factory, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    directory = _auth_dir(tmp_path_factory, monkeypatch)
    _seed(directory, _reminder("alpha", "Gone", datetime.now(timezone.utc) + timedelta(days=2), "Lost one"))
    strip = _strip(client.get("/").text)
    assert "Card moved or removed" in strip and "Lost one" in strip and "Alpha" in strip
    assert "hx-get" not in strip and "<button" not in strip and "hx-post" not in strip
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
    assert "hx-get" not in strip and "<button" not in strip


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
