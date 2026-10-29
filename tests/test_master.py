"""Master view reader over a synthetic vault. No live registry, no instance names."""
import hashlib
import re
import importlib
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

from starlette.testclient import TestClient

from app.board import NoteUnreadable
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


def test_an_unmatched_selection_shows_every_entity(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "beta1", "02-work", "Filing", status="draft")
    page = load_master(vault, main.build_scope, entities=("nope",), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
    assert {m.entity for m in page.columns[0].cards} == {"alpha", "beta1"}
    assert [c.active for c in page.chips] == [False, False]


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


def _break_note_encoding(path: Path) -> None:
    path.write_bytes(b"---\ntitle: \xff\xfe broken\nstatus: draft\n---\n\xc3\x28\n")


def test_note_with_invalid_utf8_fails_only_its_entity(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _break_note_encoding(_note(tmp_path, "alpha", "02-work", "Garbled", status="draft"))
    _note(tmp_path, "beta1", "02-work", "Still here", status="draft")
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
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
                       now=datetime(2026, 10, 29, 9))
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
                       sort="title", done_all=True, now=datetime(2026, 10, 29, 9))
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
        assert "sr-only\">Close<" in response.text and "dialog" in response.text
        for leaked in (str(tmp_path), "Permission denied", "codec", "0xff", "Traceback"):
            assert leaked not in response.text


def test_popup_not_found_and_error_views_both_close(tmp_path, monkeypatch):
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
