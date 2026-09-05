"""Manual triage uses registry choices and the existing propose-only boundary."""
import hashlib
from html.parser import HTMLParser

import pytest
import yaml

from tests.conftest import git_head
from tests.test_app import client, snapshot_entity_tree  # shared synthetic HTTP fixture


class ManualForms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "manual-propose" in attrs.get("class", "").split():
            self.current = {"attrs": attrs, "inputs": {}, "modules": []}
            self.forms.append(self.current)
        elif self.current is not None:
            if tag == "input":
                self.current["inputs"][attrs.get("name")] = attrs.get("value", "")
            elif tag == "select":
                self.select = attrs.get("name")
            elif tag == "option" and self.select == "module" and attrs.get("value"):
                self.current["modules"].append(attrs["value"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None
        elif tag == "select":
            self.select = None


def _unclassified(client):
    source = client.vault / "alpha/00-inbox/active/marker.md"
    source.write_text("---\ntype: inbox-item\ntitle: Needs human judgement\nsub: triage\n---\nSynthetic receipt\n")
    return source


def _form(response, filename="marker.md"):
    forms = ManualForms(response.text).forms
    assert forms, "triage must offer a manual proposal form"
    return next(form for form in forms if form["inputs"]["filename"] == filename)


def _registry(client):
    path = client.vault / "_system/archetypes.yaml"
    return path, yaml.safe_load(path.read_text())


def test_manual_triage_available_without_matching_rule_and_get_is_read_only(client):
    _unclassified(client)
    before = snapshot_entity_tree(client.vault, "alpha")
    head = git_head(client.vault)

    response = client.get("/triage/alpha")
    form = _form(response)

    assert form["attrs"]["hx-post"] == "/triage/alpha/propose"
    assert form["attrs"]["method"] == "post"
    assert form["attrs"]["action"] == "/triage/alpha/propose"
    assert form["modules"] == ["00-intake", "01-core", "02-work", "zz-extra"]
    assert "Preview proposal" in response.text
    assert "Approve" not in response.text
    assert snapshot_entity_tree(client.vault, "alpha") == before
    assert git_head(client.vault) == head


def test_manual_triage_also_allows_correcting_confident_and_invalid_suggestions(client):
    response = client.get("/triage/alpha")
    assert _form(response, "marker.md")["modules"]
    assert _form(response, "invalid.md")["modules"]


def test_manual_choices_use_scope_flags_and_active_subs_not_directory_discovery(client):
    path, registry = _registry(client)
    registry["submodules"]["02-work"]["restricted"] = {"name": "Restricted", "flag": "other"}
    path.write_text(yaml.safe_dump(registry))
    (client.vault / "alpha/undeclared/active").mkdir(parents=True)

    alpha = client.get("/triage/alpha")
    beta = client.get("/triage/beta1")

    assert "zz-extra" in _form(alpha)["modules"]
    assert "zz-extra" not in _form(beta)["modules"]
    assert "undeclared" not in _form(alpha)["modules"]
    assert alpha.context["manual_destinations"]["02-work"] == ["general"]
    assert beta.context["manual_destinations"]["02-work"] == ["general", "restricted"]


def test_manual_choices_do_not_offer_the_source_inbox_as_a_destination(client):
    path, registry = _registry(client)
    registry["modules"]["00-inbox"] = {"block": "system", "core": True}
    path.write_text(yaml.safe_dump(registry))

    response = client.get("/triage/alpha")

    assert "00-inbox" not in _form(response)["modules"]


def test_manual_choices_exclude_modules_without_active_lifecycle(client):
    path, registry = _registry(client)
    registry["modules"]["01-core"]["lifecycle_pattern"] = False
    path.write_text(yaml.safe_dump(registry))

    response = client.get("/triage/alpha")

    assert "01-core" not in _form(response)["modules"]


@pytest.mark.parametrize("sub", ["", "general"])
def test_manual_form_submission_writes_only_a_proposal_and_preserves_source(client, sub):
    source = _unclassified(client)
    response = client.get("/triage/alpha")
    form = _form(response)
    source_bytes = source.read_bytes()
    head = git_head(client.vault)
    before = set((client.vault / "alpha/outbox").iterdir())
    rules = (client.vault / "_system/classifier/rules.yaml").read_bytes()

    result = client.post(form["attrs"]["hx-post"], data={
        **form["inputs"], "module": "02-work", "sub": sub,
    }, headers={"HX-Request": "true"})

    assert result.status_code == 200
    assert result.headers["HX-Trigger"] == "console:proposal-persisted"
    assert "nothing moved yet" in result.text
    added = set((client.vault / "alpha/outbox").iterdir()) - before
    assert len(added) == 1
    record = yaml.safe_load(added.pop().read_text())
    assert record["module"] == "02-work"
    assert record["sub"] == (sub or None)
    assert record["block"] == "build"
    assert record["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source.read_bytes() == source_bytes
    assert not (client.vault / "alpha/02-work/active/marker.md").exists()
    assert git_head(client.vault) == head
    assert (client.vault / "_system/classifier/rules.yaml").read_bytes() == rules


@pytest.mark.parametrize("tamper", [
    {"module": "../beta1/02-work"},
    {"sub": "inactive"},
    {"entity": "beta1"},
    {"block": "finance"},
])
def test_manual_form_tampering_is_refused_without_writes(client, tamper):
    _unclassified(client)
    form = _form(client.get("/triage/alpha"))
    before = snapshot_entity_tree(client.vault, "alpha")
    other = snapshot_entity_tree(client.vault, "beta1")
    head = git_head(client.vault)

    result = client.post(form["attrs"]["hx-post"], data={
        **form["inputs"], "module": "02-work", "sub": "general", **tamper,
    }, headers={"HX-Request": "true"})

    assert "HX-Trigger" not in result.headers
    assert 'role="alert"' in result.text
    assert snapshot_entity_tree(client.vault, "alpha") == before
    assert snapshot_entity_tree(client.vault, "beta1") == other
    assert git_head(client.vault) == head


def test_manual_form_revalidates_registry_changed_since_page_render(client):
    form = _form(client.get("/triage/alpha"))
    path, registry = _registry(client)
    registry["modules"]["02-work"]["requires_flag"] = "other"
    path.write_text(yaml.safe_dump(registry))
    before = snapshot_entity_tree(client.vault, "alpha")

    result = client.post(form["attrs"]["hx-post"], data={
        **form["inputs"], "module": "02-work", "sub": "general",
    }, headers={"HX-Request": "true"})

    assert "HX-Trigger" not in result.headers
    assert "E-DEST" in result.text
    assert snapshot_entity_tree(client.vault, "alpha") == before


def test_missing_module_is_visible_and_cannot_be_proposed(client):
    # Preserve the missing-module warning rather than silently filtering it out.
    module = client.vault / "alpha/zz-extra"
    module.rename(client.vault / "alpha/retained-module")
    response = client.get("/triage/alpha")
    form = _form(response)
    before = snapshot_entity_tree(client.vault, "alpha")

    assert "zz-extra" in form["modules"]
    assert "E4" in response.text
    result = client.post(form["attrs"]["hx-post"], data={
        **form["inputs"], "module": "zz-extra", "sub": "",
    }, headers={"HX-Request": "true"})
    assert "HX-Trigger" not in result.headers
    assert "E-DEST" in result.text
    assert snapshot_entity_tree(client.vault, "alpha") == before


def test_manual_form_escapes_filename_as_one_hidden_value(client):
    source = _unclassified(client)
    filename = 'receipt\'"><img src=x onerror=bad()>.md'
    source.rename(source.with_name(filename))

    response = client.get("/triage/alpha")
    form = _form(response, filename)

    assert form["inputs"] == {"filename": filename}
    assert "<img src=x onerror=bad()>" not in response.text


def test_manual_triage_empty_choice_set_is_visible_not_an_enabled_action(client):
    path, registry = _registry(client)
    for spec in registry["modules"].values():
        spec["lifecycle_pattern"] = False
    path.write_text(yaml.safe_dump(registry))

    response = client.get("/triage/alpha")

    assert "No active destinations are available" in response.text
    assert ManualForms(response.text).forms == []
