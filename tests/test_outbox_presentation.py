"""Readable reviews retain exact action binding and safe, read-only projection."""
import hashlib
import json
from html.parser import HTMLParser

import pytest

from tests.conftest import git_head
from tests.test_app import client, snapshot_entity_tree  # noqa: F401


class ReviewHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details_depth = 0
        self.visible = []
        self.technical = []
        self.details = []
        self.buttons = []
        self.stylesheets = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.details_depth += 1
            self.details.append(attrs)
        if tag == "button":
            self.buttons.append((attrs, self.details_depth))
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "details":
            self.details_depth -= 1

    def handle_data(self, data):
        (self.technical if self.details_depth else self.visible).append(data)


def test_outbox_review_has_readable_title_route_and_collapsed_details(client):
    before = snapshot_entity_tree(client.vault, "alpha")
    head = git_head(client.vault)
    response = client.get("/outbox/alpha")
    assert response.status_code == 200
    page = ReviewHTML(response.text)
    visible = " ".join(page.visible)
    assert "alpha-marker valid-destination-marker" in visible
    assert "From" in visible and "To" in visible and "Category" in visible
    assert "00-inbox" in visible and "02-work" in visible
    assert "Approve move" in visible
    details = [d for d in page.details if d.get("class") == "review-technical"]
    assert len(details) == 1 and "open" not in details[0]
    assert "move: alpha/00-inbox/active/marker.md" in " ".join(page.technical)
    assert "move: alpha/" not in visible
    proposal_path = next((client.vault / "alpha/outbox").glob("*.yaml"))
    assert proposal_path.stem not in visible
    actions = [(attrs, depth) for attrs, depth in page.buttons if "hx-post" in attrs]
    assert len(actions) == 2
    for attrs, depth in actions:
        assert depth == 0
        assert json.loads(attrs["hx-vals"]) == {
            "id": proposal_path.stem,
            "review_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        }
        assert attrs["hx-target"] == "#outbox-list"
        assert attrs["hx-include"].startswith("#review-fields-")
    assert snapshot_entity_tree(client.vault, "alpha") == before
    assert git_head(client.vault) == head


@pytest.mark.parametrize("title, expected", [
    ('"<img src=x onerror=alert(1)>"', "&lt;img src=x onerror=alert(1)&gt;"),
    ("null", "marker.md"),
    ("[]", "marker.md"),
    ("2026-99-99", "marker.md"),
    ("!!timestamp nonsense", "marker.md"),
    ("!!bool nonsense", "marker.md"),
])
def test_review_title_is_escaped_or_falls_back_to_filename(client, title, expected):
    source = client.vault / "alpha/00-inbox/active/marker.md"
    source.write_text(f"---\ntitle: {title}\nsub: triage\n---\nSample\n")
    html = client.get("/outbox/alpha").text
    assert f'class="review-title">{expected}</h2>' in html
    assert "<img src=x" not in html


def test_empty_category_is_human_readable(client):
    path = next((client.vault / "alpha/outbox").glob("*.yaml"))
    path.write_text(path.read_text().replace("sub: general", "sub: null"))
    page = ReviewHTML(client.get("/outbox/alpha").text)
    assert "No category" in " ".join(page.visible)
    assert "None" not in " ".join(page.visible)


def test_missing_source_error_stays_visible_and_cannot_approve(client):
    (client.vault / "alpha/00-inbox/active/marker.md").unlink()
    page = ReviewHTML(client.get("/outbox/alpha").text)
    assert "E-MISSING" in " ".join(page.visible)
    assert "marker.md" in " ".join(page.visible)
    assert not any(a.get("hx-post", "").endswith("/approve") for a, _ in page.buttons)
    assert any(a.get("hx-post", "").endswith("/reject") and depth == 0
               for a, depth in page.buttons)


def test_title_and_diff_share_one_safe_source_observation(client, monkeypatch):
    import app.outbox as outbox
    original = outbox._read_no_follow_bytes
    calls = []
    source = client.vault / "alpha/00-inbox/active/marker.md"

    def observed(root, relative):
        contents = original(root, relative)
        if str(relative) == "alpha/00-inbox/active/marker.md":
            calls.append(relative)
            source.write_text("---\ntitle: changed-after-read\n---\nChanged\n")
        return contents

    monkeypatch.setattr(outbox, "_read_no_follow_bytes", observed)
    page = ReviewHTML(client.get("/outbox/alpha").text)
    assert len(calls) == 1
    assert "alpha-marker valid-destination-marker" in " ".join(page.visible)
    assert "alpha-marker valid-destination-marker" in " ".join(page.technical)
    assert "changed-after-read" not in " ".join(page.visible + page.technical)


def test_redirected_source_never_supplies_a_display_title(client):
    source = client.vault / "alpha/00-inbox/active/marker.md"
    source.unlink()
    source.symlink_to(client.vault / "beta1/00-inbox/active/marker.md")
    response = client.get("/outbox/alpha")
    page = ReviewHTML(response.text)
    assert "beta1-marker" not in " ".join(page.visible + page.technical)
    # Registry/path validation may refuse the entire listing before projection.
    assert response.status_code >= 400
    assert "Do not use a symlink" in " ".join(page.visible)
    assert not any(a.get("hx-post", "").endswith("/approve") for a, _ in page.buttons)


def test_blocked_listing_preserves_title_but_withholds_actions(client):
    (client.vault / "alpha/outbox" / ("20260815T090704-" + "33" * 16 + ".yaml")).write_text("[invalid")
    page = ReviewHTML(client.get("/outbox/alpha").text)
    assert "alpha-marker valid-destination-marker" in " ".join(page.visible)
    assert not any("hx-post" in a for a, _ in page.buttons)


def test_preview_explains_next_step_and_collapses_technical_diff(client):
    response = client.post("/triage/alpha/propose", data={
        "filename": "marker.md", "module": "02-work", "sub": "general",
    })
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "console:proposal-persisted"
    page = ReviewHTML(response.text)
    assert "nothing moved yet" in " ".join(page.visible)
    assert "02-work" in " ".join(page.visible)
    assert 'href="/outbox/alpha"' in response.text
    assert len(page.details) == 1 and "open" not in page.details[0]
    assert "move: alpha/" in " ".join(page.technical)


def test_stylesheet_url_is_content_versioned_and_served(client):
    from app.main import BASE
    page = ReviewHTML(client.get("/outbox/alpha").text)
    css = (BASE / "static/app.css").read_bytes()
    assert page.stylesheets == [f"/static/app.css?v={hashlib.sha256(css).hexdigest()[:12]}"]
    assert client.get(page.stylesheets[0]).content == css
