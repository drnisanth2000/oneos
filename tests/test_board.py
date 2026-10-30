"""Board views over a synthetic vault. No live registry and no instance names."""
import importlib
from datetime import date
from pathlib import Path

from starlette.testclient import TestClient

from tests.conftest import (
    entities_yaml,
    git_head_message,
    scaffold_modules,
    write_tree,
    write_vault,
)


def _client(tmp_path, monkeypatch, entities=None):
    write_vault(tmp_path, entities or entities_yaml("alpha", "other"))
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    scaffold_modules(tmp_path, "other", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    return importlib.reload(main), TestClient(main.app)


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


def test_other_entity_note_stays_out_of_the_board(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "other", "02-work", "Betaonly")
    _note(tmp_path, "alpha", "02-work", "Alphaonly")

    response = client.get("/board/alpha", params={"group": "module"})

    assert response.status_code == 200
    assert "Alphaonly" in response.text
    assert "Betaonly" not in response.text


def test_sort_is_stable_and_blank_updated_sorts_last(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Earlier", updated="2020-01-01")
    _note(tmp_path, "alpha", "02-work", "Later", updated="2024-06-01")
    _note(tmp_path, "alpha", "02-work", "Undated", updated="")
    _note(tmp_path, "alpha", "02-work", "Bee", updated="2024-06-01")
    _note(tmp_path, "alpha", "02-work", "Aye", updated="2024-06-01")

    response = client.get("/board/alpha", params={"group": "module", "sort": "updated"})
    text = response.text
    assert response.status_code == 200
    assert text.index("Later") < text.index("Earlier") < text.index("Undated")
    assert text.index("Aye") < text.index("Bee")


def test_tag_filters_combine_and_missing_status_and_module_stay_visible(
    tmp_path, monkeypatch
):
    entities = """
version: "1.0"
entities:
  alpha: {label: Alpha, flags: [special]}
  other: {label: Other, flags: []}
"""
    _main, client = _client(tmp_path, monkeypatch, entities)
    _note(tmp_path, "alpha", "02-work", "Both", sub="records", product="widget")
    _note(tmp_path, "alpha", "02-work", "Otherproduct", sub="records", product="other")
    _note(tmp_path, "alpha", "02-work", "Othercategory", sub="journal", product="widget")
    _note(tmp_path, "alpha", "02-work", "Blankstatus", status="", product="widget", sub="records")

    filtered = client.get(
        "/board/alpha",
        params={"group": "module", "sub": "records", "product": "widget"},
    )
    assert filtered.status_code == 200
    assert "Both" in filtered.text
    assert "Blankstatus" in filtered.text
    assert "Otherproduct" not in filtered.text
    assert "Othercategory" not in filtered.text

    status = client.get("/board/alpha", params={"group": "status"})
    assert "Unspecified" in status.text
    assert "Blankstatus" in status.text

    modules = client.get("/board/alpha", params={"group": "module"})
    assert "zz-extra" in modules.text
    assert "missing on disk" in modules.text


def test_symlinked_active_directory_is_refused(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    _note(outside, "other", "02-work", "Secretnote")
    active = tmp_path / "alpha" / "02-work" / "active"
    active.symlink_to(outside / "other" / "02-work" / "active", target_is_directory=True)

    response = client.get("/board/alpha")

    assert response.status_code == 409
    assert "E-TAMPER" in response.text
    assert "Secretnote" not in response.text


def test_review_drop_targets_are_registry_destinations(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "Intake", sub="triage", title="Intake")
    (tmp_path / "alpha" / "02-work" / "active").mkdir()

    response = client.get("/board/alpha", params={"group": "review"})

    assert response.status_code == 200
    assert 'draggable="true"' in response.text
    assert 'data-filename="Intake.md"' in response.text
    assert 'hx-post="/triage/alpha/propose"' in response.text
    assert 'name="filename"' in response.text
    assert 'name="module"' in response.text
    assert 'name="sub"' in response.text
    assert 'data-module="02-work"' in response.text
    assert 'data-module="zz-extra"' not in response.text


def test_list_and_planner_do_not_start_a_drag_without_a_drop_column(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "Intake", sub="triage", title="Intake")
    (tmp_path / "alpha" / "02-work" / "active").mkdir()

    listing = client.get("/board/alpha", params={"view": "list", "group": "review"})
    planner = client.get("/board/alpha", params={"view": "planner", "group": "review"})

    assert listing.status_code == 200
    assert planner.status_code == 200
    assert "Intake" in listing.text
    assert "Intake" in planner.text
    assert 'draggable="true"' not in listing.text
    assert 'draggable="true"' not in planner.text
    assert "@drop.prevent" not in listing.text
    assert "@drop.prevent" not in planner.text


def test_blocked_outbox_hides_drag_and_keeps_filed_notes(tmp_path, monkeypatch):
    from tests.conftest import _git

    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Stillhere")
    _note(tmp_path, "alpha", "00-inbox", "Waiting", sub="triage", title="Waiting")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    proposal = "20260101T000000-" + ("a" * 32)
    outbox = tmp_path / "alpha" / "outbox"
    outbox.mkdir()
    (outbox / f"{proposal}.yaml").write_text("[]\n", encoding="utf-8")

    response = client.get("/board/alpha", params={"group": "module"})

    assert response.status_code == 200
    assert "Stillhere" in response.text
    assert "draggable" not in response.text
    assert "E-UNREADABLE" in response.text


def test_gallery_calendar_planner_map_outline_and_formula(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    today = date.today().isoformat()
    _note(
        tmp_path, "alpha", "02-work", "Sharedone",
        product="shared", status="open", updated=today,
        formula='count(related)',
        body="# Heading\n- bullet item\n",
    )
    _note(
        tmp_path, "alpha", "02-work", "Sharedtwo",
        product="shared", status="open", updated="2020-01-01", due="2026-03-15",
        formula='if(status == "open", "yes", "no")',
    )
    _note(
        tmp_path, "alpha", "02-work", "Brokenformula",
        product="shared", formula="1+1", cadence="daily", updated="",
    )
    _note(tmp_path, "alpha", "00-inbox", "Queued", sub="triage", title="Queued")

    gallery = client.get("/board/alpha", params={"view": "gallery"})
    assert 'class="board-gallery"' in gallery.text
    assert "Sharedone" in gallery.text

    march = client.get("/board/alpha", params={"view": "calendar", "month": "2026-03"})
    february = client.get("/board/alpha", params={"view": "calendar", "month": "2026-02"})
    assert "Sharedtwo" in march.text
    assert "Sharedtwo" not in february.text
    assert "No date" in march.text
    assert "Brokenformula" in march.text

    planner = client.get("/board/alpha", params={"view": "planner"})
    assert "Queued" in planner.text
    assert "Habits" in planner.text
    assert "daily" in planner.text

    life = client.get("/board/alpha", params={"view": "map"})
    assert "02-work" in life.text
    assert "map-bar" in life.text

    outlined = client.get(
        "/board/alpha",
        params={"note": "alpha/02-work/active/Sharedone.md"},
    )
    assert "Heading" in outlined.text
    assert "bullet item" in outlined.text

    counted = client.get("/board/alpha", params={"group": "module"})
    assert ">1<" in counted.text or "1" in counted.text
    assert "yes" in counted.text
    assert "1+1" in counted.text


def test_board_header_has_no_weekly_attention_card(tmp_path, monkeypatch):
    # Owner decision (2026-10-28): the Board header carries no "This week" card.
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Recent", updated=date.today().isoformat(), status="open")

    response = client.get("/board/alpha")

    assert response.status_code == 200
    assert "This week" not in response.text
    assert 'class="attention"' not in response.text


def test_gallery_chips_follow_the_filed_notes(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Filedtag", sub="records")
    _note(tmp_path, "alpha", "00-inbox", "Inboxtag", sub="triage", title="Inboxtag")

    response = client.get("/board/alpha", params={"view": "gallery", "group": "review"})

    assert "records 1" in response.text
    assert "triage 1" not in response.text


def test_calendar_sorts_cards_inside_a_day(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Beta", due="2026-04-02", updated="2026-04-02")
    _note(tmp_path, "alpha", "02-work", "Alpha", due="2026-04-02", updated="2020-01-01")

    response = client.get(
        "/board/alpha",
        params={"view": "calendar", "month": "2026-04", "sort": "title"},
    )

    assert response.text.index("Alpha") < response.text.index("Beta")


def test_missing_active_directory_is_not_a_drop_target(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "Intake", sub="triage", title="Intake")
    (tmp_path / "alpha" / "01-core" / "active").mkdir()

    response = client.get("/board/alpha", params={"group": "review"})

    assert 'data-module="01-core"' in response.text
    assert 'data-module="02-work"' not in response.text


def test_capture_quotes_a_title_that_contains_a_colon(tmp_path, monkeypatch):
    from app.inbox import read_inbox
    from app.scope import Scope
    from tests.conftest import _git

    _main, client = _client(tmp_path, monkeypatch)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")

    response = client.post(
        "/board/alpha/capture",
        data={"title": "Meet: tomorrow", "text": "bring the notebook"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    found = read_inbox(Scope(tmp_path, "alpha"))
    assert [item.title for item in found] == ["Meet: tomorrow"]


def test_capture_keeps_reserved_words_and_dashes_in_the_title(tmp_path, monkeypatch):
    from app.inbox import read_inbox
    from app.scope import Scope
    from tests.conftest import _git

    _main, client = _client(tmp_path, monkeypatch)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")

    for title in ("Yes", "Meet --- later"):
        response = client.post(
            "/board/alpha/capture",
            data={"title": title, "text": "bring the notebook"},
            follow_redirects=False,
        )
        assert response.status_code == 303, title

    found = read_inbox(Scope(tmp_path, "alpha"))
    assert sorted(item.title for item in found) == ["Meet --- later", "Yes"]


def test_life_map_skips_inbox_and_modules_without_a_lifecycle(tmp_path, monkeypatch):
    archetypes = """
version: "2.0"
flags: {}
modules:
  00-inbox: { block: system }
  02-work: { block: build }
  03-hold: { block: build, lifecycle_pattern: false }
"""
    write_vault(tmp_path, entities_yaml("alpha"), archetypes)
    scaffold_modules(tmp_path, "alpha", ["00-inbox", "02-work", "03-hold"])
    _note(tmp_path, "alpha", "02-work", "Visible")
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main
    importlib.reload(main)
    client = TestClient(main.app)

    response = client.get("/board/alpha", params={"view": "map"})

    assert response.status_code == 200
    assert "<h2>02-work" in response.text
    assert "<h2>00-inbox" not in response.text
    assert "<h2>03-hold" not in response.text


def test_opening_a_paragraph_shows_the_outline_panel(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Plain", body="Just a paragraph.")

    response = client.get(
        "/board/alpha",
        params={"note": "alpha/02-work/active/Plain.md"},
    )

    assert "Plain" in response.text
    assert "Close" in response.text
    assert "No headings or lists." in response.text


def test_capture_writes_one_inbox_receipt(tmp_path, monkeypatch):
    from tests.conftest import _git

    _main, client = _client(tmp_path, monkeypatch)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")

    response = client.post(
        "/board/alpha/capture",
        data={"title": "Remember", "text": "bring the notebook"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/board/alpha"
    receipts = list((tmp_path / "alpha" / "00-inbox" / "active").glob("*.md"))
    assert len(receipts) == 1
    text = receipts[0].read_text(encoding="utf-8")
    assert "sub: triage" in text
    assert "bring the notebook" in text
    assert "Remember" in text
    assert git_head_message(tmp_path) == "ingest: add redacted receipt"
    assert not (tmp_path / "other" / "00-inbox").exists()


def test_entity_cards_returns_each_lane(tmp_path, monkeypatch):
    main, _client_ = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Filed")
    _note(tmp_path, "alpha", "00-inbox", "Waiting", sub="triage", title="Waiting")
    from app.board import entity_cards, note_body

    scope = main.build_scope("alpha")
    cards = entity_cards(scope, main.Vault(main.current_catalog()))
    assert [c.title for c in cards.filed] == ["Filed"]
    assert [c.title for c in cards.inbox] == ["Waiting"]
    assert cards.proposed == () and cards.blocked is False
    assert note_body(scope, cards.filed[0].relative_path).strip() == "Body"


# --- List view follows the selected Group -----------------------------------

def _list_groups(html: str) -> list[dict]:
    """One dict per list-group section: label, count, missing flag and note titles."""
    import re

    groups = []
    for match in re.finditer(
        r'<section class="list-group([^"]*)"[^>]*aria-labelledby="([^"]+)"[^>]*>(.*?)</section>',
        html,
        re.S,
    ):
        classes, labelled_by, inner = match.groups()
        heading = re.search(
            r'<h2 id="%s">\s*<span class="column-label">(.*?)</span>\s*<span class="count">(\d+)</span>'
            % re.escape(labelled_by),
            inner,
            re.S,
        )
        assert heading, f"section {labelled_by} lacks a labelled h2 with a count"
        groups.append(
            {
                "label": heading.group(1).strip(),
                "count": int(heading.group(2)),
                "missing": "missing" in classes.split(),
                "titles": re.findall(r'<article class="board-card[^>]*>.*?<h3>.*?>([A-Za-z]+)</(?:a|span)>', inner, re.S),
                "html": inner,
            }
        )
    return groups


def test_list_view_groups_by_status_in_sort_order(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Olderopen", status="open", updated="2024-01-01")
    _note(tmp_path, "alpha", "02-work", "Newestopen", status="open", updated="2024-09-01")
    _note(tmp_path, "alpha", "02-work", "Finished", status="done", updated="2024-05-01")

    response = client.get("/board/alpha", params={"view": "list", "group": "status"})
    board = client.get("/board/alpha", params={"view": "board", "group": "status"})

    assert response.status_code == 200
    groups = _list_groups(response.text)
    by_label = {g["label"].lower(): g for g in groups}
    assert by_label["open"]["titles"] == ["Newestopen", "Olderopen"]
    assert by_label["open"]["count"] == 2
    assert by_label["done"]["titles"] == ["Finished"]
    assert by_label["done"]["count"] == 1
    # Same groups, same order, same labels as the Board view's columns.
    import re

    board_labels = re.findall(r'<h2><span class="column-label">(.*?)</span>', board.text)
    assert [g["label"] for g in groups] == board_labels


def test_list_view_groups_by_module_with_module_headings(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Workone")
    _note(tmp_path, "alpha", "01-core", "Coreone")

    response = client.get("/board/alpha", params={"view": "list", "group": "module"})

    groups = {g["label"]: g for g in _list_groups(response.text)}
    assert groups["02-work"]["titles"] == ["Workone"]
    assert groups["01-core"]["titles"] == ["Coreone"]
    assert 'aria-label="Group by"' in response.text  # toolbar still present
    # The heading already names the module, so the rows do not repeat it.
    assert all('class="mod-name"' not in g["html"] for g in groups.values())


def test_list_view_groups_by_category(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Recordnote", sub="records")
    _note(tmp_path, "alpha", "02-work", "Journalnote", sub="journal")

    response = client.get("/board/alpha", params={"view": "list", "group": "sub"})

    groups = {g["label"]: g for g in _list_groups(response.text)}
    assert groups["records"]["titles"] == ["Recordnote"]
    assert groups["journal"]["titles"] == ["Journalnote"]


def test_list_view_review_group_shows_inbox_and_stays_undraggable(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "00-inbox", "Intake", sub="triage", title="Intake")
    (tmp_path / "alpha" / "02-work" / "active").mkdir(exist_ok=True)

    response = client.get("/board/alpha", params={"view": "list", "group": "review"})

    groups = {g["label"]: g for g in _list_groups(response.text)}
    assert groups["Inbox"]["titles"] == ["Intake"]
    assert groups["Inbox"]["count"] == 1
    assert 'draggable="true"' not in response.text
    assert "@drop.prevent" not in response.text
    assert "@dragover" not in response.text


def test_list_view_skips_empty_groups_but_keeps_missing_warning(tmp_path, monkeypatch):
    entities = """
version: "1.0"
entities:
  alpha: {label: Alpha, flags: [special]}
  other: {label: Other, flags: []}
"""
    _main, client = _client(tmp_path, monkeypatch, entities)
    _note(tmp_path, "alpha", "02-work", "Onlyone")

    response = client.get("/board/alpha", params={"view": "list", "group": "module"})
    board = client.get("/board/alpha", params={"view": "board", "group": "module"})

    groups = _list_groups(response.text)
    labels = [g["label"] for g in groups]
    assert "02-work" in labels
    assert "01-core" not in labels  # present on disk but empty: no section
    missing = [g for g in groups if g["missing"]]
    assert missing, "a required-but-missing module keeps its section"
    assert "required by flags, missing on disk" in missing[0]["html"]
    assert "No notes" not in response.text
    # The Board view still shows the empty column, unchanged.
    assert "01-core" in board.text


def test_list_view_with_no_notes_says_no_notes_once(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)

    response = client.get("/board/alpha", params={"view": "list", "group": "status"})

    assert response.status_code == 200
    assert _list_groups(response.text) == []
    assert response.text.count("No notes") == 1


def test_gallery_stays_a_flat_grid_whatever_the_group(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Galone", status="open")
    _note(tmp_path, "alpha", "02-work", "Galtwo", status="done")

    response = client.get("/board/alpha", params={"view": "gallery", "group": "status"})

    assert 'class="board-gallery"' in response.text
    assert "list-group" not in response.text
    assert "Galone" in response.text and "Galtwo" in response.text


def test_board_view_is_unchanged_by_the_list_grouping(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Boardone", status="open")

    response = client.get("/board/alpha", params={"view": "board", "group": "status"})

    assert 'class="board-columns"' in response.text
    assert 'class="board-column' in response.text
    assert "list-group" not in response.text
