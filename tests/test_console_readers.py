"""Structured readers declare a category; registry-category escaping failures
become E-CONFIG while absorbed tolerances are pinned (S6 Task 8, design §5
"Boundary conversions" and §7 invariant 4)."""
import ast
import os
import pathlib
import textwrap

import pytest
import yaml

from tests.conftest import write_vault

_TRIGGER_HELP = (
    "structured read site without a @structured_reader category declaration"
)

_APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

# Names that parse a structured document, whatever the receiver is called. The
# guard matches on the call shape, not on an import alias, because its entire
# purpose is catching sites nobody remembered to declare.
_YAML_LOADERS = {"safe_load", "load", "full_load", "unsafe_load",
                 "safe_load_all", "load_all",
                 # Event stream and node graph: still structured reads of the
                 # same file, and the layer a rewriter's offsets derive from.
                 "parse", "compose", "compose_all"}
_SQLITE_OPENERS = {"connect", "Connection"}
_BYTE_READERS = {"read_text", "read_bytes", "open"}


def _receiver_chain(node) -> list[str]:
    """Every attribute/name/call target in a receiver expression."""
    names, current = [], node
    while True:
        if isinstance(current, ast.Attribute):
            names.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Call):
            current = current.func
        elif isinstance(current, ast.Name):
            names.append(current.id)
            return names
        else:
            return names


def _module_aliases(tree: ast.AST) -> tuple[dict, set]:
    """Resolve this module's import aliases.

    `import yaml as y` must not defeat the guard, so receivers are matched
    against the module they actually name, and bare names imported with
    `from yaml import safe_load as sl` are tracked too.
    """
    modules, bare = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = (
                    alias.name
                    if alias.asname
                    else
                    alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            for alias in node.names:
                local = alias.asname or alias.name
                if root == "yaml" and alias.name in _YAML_LOADERS:
                    bare.add(local)
                elif alias.name == "split_front_matter":
                    bare.add(local)
                elif root == "sqlite3" and alias.name in _SQLITE_OPENERS:
                    bare.add(local)
                else:
                    modules[local] = alias.name
    return modules, bare


def _resolved_chain(node, modules: dict) -> list[str]:
    """Receiver names with aliases resolved, split on dots.

    `import a.b.yaml as y` binds `y` to the whole dotted module, so the alias
    must expand to its components or a `yaml` receiver hides behind it.
    """
    resolved: list[str] = []
    for name in _receiver_chain(node):
        resolved.extend(str(modules.get(name, name)).split("."))
    return resolved


def _is_trigger_call(node: ast.Call, modules=None, bare=None,
                     path_names=None) -> bool:
    modules = modules or {}
    bare = bare or set()
    path_names = path_names or set()
    func = node.func
    if isinstance(func, ast.Attribute):
        chain = _resolved_chain(func.value, modules)
        if func.attr in _YAML_LOADERS and "yaml" in chain:
            return True
        if func.attr in _SQLITE_OPENERS and "sqlite3" in chain:
            return True
        if func.attr == "split_front_matter":
            return True
        # Design invariant 4's third trigger: reading a system_path result,
        # whether chained or read from a name bound earlier.
        if func.attr in _BYTE_READERS and (
            "system_path" in chain or path_names & set(chain)
        ):
            return True
    if isinstance(func, ast.Name):
        if func.id in _YAML_LOADERS | {"split_front_matter"} or func.id in bare:
            return True
    return False


def _is_reader_decorator(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        node = node.func
    return getattr(node, "id", getattr(node, "attr", "")) == "structured_reader"


def _declared_category(node: ast.expr):
    """The literal category on a @structured_reader decorator, or None."""
    if not isinstance(node, ast.Call) or not _is_reader_decorator(node):
        return None
    for kw in node.keywords:
        if kw.arg == "category" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return False  # declared, but not a literal category


def _bound_from_system_path(node) -> list[str]:
    """Names this statement binds to a `system_path(...)` result, if any."""
    targets = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    if not targets or node.value is None:
        return []
    if "system_path" not in _receiver_chain(node.value):
        return []
    names = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _system_path_attributes(tree: ast.AST) -> set:
    """`self.<attr> = ...system_path(...)` — bound in one method, read in another."""
    names = set()
    for node in ast.walk(tree):
        for name in _bound_from_system_path(node):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Attribute) for target in node.targets
            ):
                names.add(name)
    return names


def _collect_offenders(tree: ast.AST, path: pathlib.Path) -> list[str]:
    """Flag every structured read that no enclosing function declares.

    Names bound to a `system_path(...)` result are tracked **per function**:
    tracking them module-wide over-matches, because common names like `path`
    are rebound in functions that write rather than read. Attributes assigned
    on `self` are module-wide, since __init__ binds and another method reads.
    """
    offenders = []
    modules, bare = _module_aliases(tree)
    self_names = _system_path_attributes(tree)

    def visit(node, function_stack, path_names):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared = any(
                _is_reader_decorator(decorator)
                for decorator in node.decorator_list
            )
            # A nested reader inherits its enclosing declaration deliberately:
            # the outer function is the unit that owns the category.
            function_stack = function_stack + [declared]
            path_names = set(self_names)
        for name in _bound_from_system_path(node):
            path_names.add(name)
        if isinstance(node, ast.Call) and _is_trigger_call(
            node, modules, bare, path_names
        ):
            if not any(function_stack):
                offenders.append(f"{path}:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, function_stack, path_names)

    visit(tree, [], set(self_names))
    return offenders


def _app_sources() -> list[pathlib.Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def test_every_structured_read_site_declares_a_category():
    sources = _app_sources()
    # An invariant whose job is refusing silence must not pass by scanning
    # nothing: the previous version used a cwd-relative path and returned
    # zero files (and therefore zero offenders) from any other directory.
    assert len(sources) > 10, f"guard scanned too few files: {len(sources)}"
    offenders = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(_collect_offenders(tree, path))
    assert offenders == [], f"{_TRIGGER_HELP}: {offenders}"


def test_guard_catches_a_synthetic_undeclared_reader():
    """The guard must fail on shapes a future reader could plausibly use."""
    from app.console_routing import READER_CATEGORIES

    shapes = [
        "def f(p):\n    return yaml.safe_load(p)",
        "def f(p):\n    return yaml.full_load(p)",
        "import yaml as y\ndef f(p):\n    return y.load(p)",
        "import sqlite3 as sq\ndef f(s):\n    return sq.connect(s)",
        "from yaml import safe_load as sl\ndef f(p):\n    return sl(p)",
        "import a.b.yaml as y\ndef f(p):\n    return y.compose(p)",
        "import ruamel.yaml\ndef f(p):\n    return ruamel.yaml.safe_load(p)",
        "from ruamel.yaml import safe_load\ndef f(p):\n    return safe_load(p)",
        "def f(s):\n    return json.loads(s.system_path('x.json').read_text())",
        "def f(v):\n    p = v.system_path('members.yaml')\n    return p.read_bytes()",
        "def f(v):\n    p = v.system_path('x.json')\n    return json.loads(p.read_text())",
        "class C:\n    def __init__(self, v):\n        self._p = v.system_path('r.yaml')\n"
        "    def load(self):\n        return self._p.read_text()",
        "def f(p):\n    return yaml.safe_load_all(p)",
        "def f(p):\n    return yaml.parse(p)",
        "def f(p):\n    return yaml.compose(p)",
        "from yaml import compose as c\ndef f(p):\n    return c(p)",
        "def f(t):\n    return split_front_matter(t)",
    ]
    for src in shapes:
        tree = ast.parse(textwrap.dedent(src))
        found = _collect_offenders(tree, pathlib.Path("synthetic.py"))
        assert found, f"guard missed an undeclared reader shape:\n{src}"
    assert READER_CATEGORIES


def test_every_declaration_names_a_literal_known_category():
    """The guard refuses silence; this refuses a non-literal category."""
    from app.console_routing import READER_CATEGORIES

    bad = []
    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                category = _declared_category(decorator)
                if category is None:
                    continue
                if category not in READER_CATEGORIES:
                    bad.append(f"{path}:{node.lineno} -> {category!r}")
    assert bad == [], f"non-literal or unknown reader category: {bad}"


ENTITIES = 'version: "1.0"\nentities:\n  demo: {label: Demo, flags: []}\n'


def _scope(tmp_path):
    from app.scope import Scope

    write_vault(tmp_path, ENTITIES)
    (tmp_path / "demo").mkdir(exist_ok=True)
    return Scope(tmp_path, "demo")


def test_registry_reader_unparseable_yaml_becomes_config(tmp_path):
    from app.classifier import Classifier
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.registry import products_for
    from app.vault import DestinationRegistryError, Vault

    scope = _scope(tmp_path)

    (tmp_path / "_system/products.yaml").write_text(
        "products: [unterminated\n", encoding="utf-8"
    )
    with pytest.raises(DestinationRegistryError) as products_raised:
        products_for(scope)
    assert describe(products_raised.value).code == "E-CONFIG"

    (tmp_path / "_system/archetypes.yaml").write_text(
        "modules: [unterminated\n", encoding="utf-8"
    )
    vault = Vault(EntityCatalog.load(tmp_path))
    with pytest.raises(DestinationRegistryError) as vault_raised:
        vault._load_yaml("archetypes.yaml")
    assert describe(vault_raised.value).code == "E-CONFIG"

    classifier_dir = tmp_path / "_system/classifier"
    classifier_dir.mkdir(parents=True)
    (classifier_dir / "rules.yaml").write_text(
        "rules: [unterminated\n", encoding="utf-8"
    )
    with pytest.raises(DestinationRegistryError) as rules_raised:
        Classifier(vault)
    assert describe(rules_raised.value).code == "E-CONFIG"


def test_registry_reader_wrongly_shaped_yaml_becomes_config(tmp_path):
    from app.console_errors import describe
    from app.registry import _count_workspaces, products_for
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)

    # A list where a mapping is expected parses cleanly and previously raised
    # AttributeError on access — the likelier hand-editing mistake.
    (tmp_path / "_system/products.yaml").write_text(
        "- not-a-mapping\n", encoding="utf-8"
    )
    with pytest.raises(DestinationRegistryError) as file_shape:
        products_for(scope)
    assert describe(file_shape.value).code == "E-CONFIG"

    (tmp_path / "_system/products.yaml").write_text(
        "products:\n- not-a-mapping\n", encoding="utf-8"
    )
    with pytest.raises(DestinationRegistryError) as key_shape:
        products_for(scope)
    assert describe(key_shape.value).code == "E-CONFIG"

    (tmp_path / "_system/workspaces.yaml").write_text(
        "workspaces: not-a-list\n", encoding="utf-8"
    )
    with pytest.raises(DestinationRegistryError) as workspace_shape:
        _count_workspaces(scope, "product", "anything")
    assert describe(workspace_shape.value).code == "E-CONFIG"


def test_registry_reader_absent_products_still_returns_empty(tmp_path):
    from app.registry import products_for

    scope = _scope(tmp_path)
    assert not (tmp_path / "_system/products.yaml").exists()
    assert products_for(scope) == []


def test_registry_reader_absent_workspaces_still_counts_zero(tmp_path):
    from app.registry import _count_workspaces

    scope = _scope(tmp_path)
    assert not (tmp_path / "_system/workspaces.yaml").exists()
    assert _count_workspaces(scope, "product", "anything") == 0


def test_front_matter_malformed_still_returns_empty_mapping():
    from app.inbox import split_front_matter

    fm, body = split_front_matter("---\n[unterminated\n---\nbody text\n")
    assert fm == {}
    assert "body text" in body


def test_proposal_reader_failure_is_unreadable_not_config(tmp_path):
    from app.console_errors import describe
    from app.outbox import UnreadableProposalRecord
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "ab" * 16
    outbox = tmp_path / "demo/outbox"
    outbox.mkdir(parents=True)
    (outbox / f"{proposal_id}.yaml").write_text(
        "action: delete\nslug: [unterminated\n", encoding="utf-8"
    )

    with pytest.raises(UnreadableProposalRecord) as raised:
        get_delete_proposal(scope, proposal_id)

    described = describe(raised.value)
    assert described.code == "E-UNREADABLE"


# --- I5: conversion coverage for the registry readers the first pass missed,
# --- plus the tolerance row that was correct but unpinned.

def test_scoped_registry_removal_converts_unparseable_bytes(tmp_path):
    """`_remove_scoped_registry_value` gained conversions with no test."""
    from app import registry
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)
    for payload in (b"\xff\xfe not utf-8", b"products: [unclosed\n"):
        with pytest.raises(DestinationRegistryError):
            registry._remove_scoped_registry_value(scope, "product", "p", payload)


def test_entity_catalog_load_converts_to_config(tmp_path):
    """`EntityCatalog.load` is declared `registry`; pin that it yields E-CONFIG."""
    from app.console_errors import describe
    from app.entities import EntityCatalog

    system = tmp_path / "_system"
    system.mkdir(parents=True)
    (system / "entities.yaml").write_text("entities: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(Exception) as raised:
        EntityCatalog.load(tmp_path)
    assert describe(raised.value).code == "E-CONFIG"


def test_front_matter_counter_still_skips_an_unreadable_file(tmp_path):
    """The fifth tolerance row: an unreadable markdown file is skipped."""
    from app import registry

    entity_root = tmp_path / "demo"
    (entity_root / "01-core").mkdir(parents=True)
    good = entity_root / "01-core" / "good.md"
    good.write_text("---\nproduct: p\n---\nbody\n", encoding="utf-8")
    bad = entity_root / "01-core" / "bad.md"
    bad.write_text("---\nproduct: p\n---\nbody\n", encoding="utf-8")
    if os.geteuid() == 0:
        pytest.skip("permission bits have no effect for root (CAP_DAC_OVERRIDE)")
    bad.chmod(0o000)
    try:
        assert registry._count_front_matter(entity_root, "product", "p") == 1
    finally:
        bad.chmod(0o644)


def test_corrupt_books_db_is_a_registry_refusal_not_unknown(tmp_path):
    """I3: a corrupt books.db reached the operator as E-UNKNOWN."""
    from app import registry
    from app.console_errors import describe

    entity_root = tmp_path / "demo"
    entity_root.mkdir(parents=True)
    (entity_root / "books.db").write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(Exception) as raised:
        registry._count_books_db(entity_root, "product", "p")
    assert describe(raised.value).code == "E-REGISTRY"


def test_workspaces_tolerates_every_falsy_entry(tmp_path):
    """C1: narrowing this to `is None` invented a refusal."""
    from app import registry

    scope = _scope(tmp_path)
    system = tmp_path / "_system"
    real = "  - {entity: demo, product: p}\n"
    for falsy in ("  - false\n", "  - ''\n", "  - 0\n", "  -\n"):
        # Pair each falsy entry with a real one and assert 1, not 0: asserting
        # 0 alone would also pass if the reader bailed out and tolerated
        # nothing, since 0 is the answer for an absent file too.
        (system / "workspaces.yaml").write_text(
            "workspaces:\n" + falsy + real, encoding="utf-8"
        )
        assert registry._count_workspaces(scope, "product", "p") == 1


def test_mid_approval_reread_of_a_vanished_record_is_unreadable(tmp_path, monkeypatch):
    """I4b: approve()'s re-read escaped as E-UNKNOWN at the highest-stakes moment.

    This must drive approve() for real. An earlier version monkeypatched and
    then asserted only on a constructed exception, so it passed with the guard
    deleted — the defect it exists to prevent.
    """
    from tests.test_outbox import _propose, _vault
    from tests.conftest import git_head
    from app import outbox
    from app.console_errors import describe

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    head_before = git_head(vault)

    # S7: the record vanishes between the strict scan that locates it and
    # the capture that takes ownership of it — a real race, expressed
    # without inspecting call frames. This is the window approve's guard
    # exists for, and it is the last read of the proposal before mutation.
    fingerprint = outbox.get_proposal_review(scope, prop.id).sha256
    real_scan = outbox._load_proposal_reviews

    def _vanish_after_locating(bound_scope):
        reviews = real_scan(bound_scope)
        prop.path.unlink()
        return reviews

    monkeypatch.setattr(outbox, "_load_proposal_reviews", _vanish_after_locating)
    with pytest.raises(outbox.UnreadableProposalRecord) as raised:
        outbox.approve(scope, prop.id, fingerprint)
    assert describe(raised.value).code == "E-UNREADABLE"
    assert git_head(vault) == head_before


def test_wrongly_shaped_product_list_is_config_not_unknown(tmp_path):
    """A list of scalars where a list of mappings is expected: valid YAML, wrong shape."""
    from app import registry
    from app.console_errors import describe

    scope = _scope(tmp_path)
    payload = b"products:\n  demo:\n  - p\n  - q\n"
    with pytest.raises(Exception) as raised:
        registry._remove_scoped_registry_value(scope, "product", "p", payload)
    assert describe(raised.value).code == "E-CONFIG"


def test_archetypes_without_modules_is_config_not_unknown(tmp_path):
    """bundles() is called unguarded from every Console page."""
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import Vault

    write_vault(tmp_path, ENTITIES, archetypes_yaml='version: "2.0"\nflags: {}\n')
    with pytest.raises(Exception) as raised:
        Vault(EntityCatalog.load(tmp_path)).bundles()
    assert describe(raised.value).code == "E-CONFIG"


def test_archetypes_modules_as_list_is_config_not_unknown(tmp_path):
    """C2 (S6 review): `archetypes.yaml` with `modules:` written as a LIST
    rather than a mapping is valid YAML, wrong shape — `Vault.active_modules`
    called `.items()` on it directly (`app/vault.py`), raising a bare
    `AttributeError` that reached the operator as `E-UNKNOWN` on every
    `bundles()` caller, exactly like the unknown-flag shape below but from
    the OTHER of the two sites review named."""
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(
        tmp_path, ENTITIES,
        archetypes_yaml='version: "2.0"\nflags: {}\nmodules:\n  - 00-intake\n',
    )
    with pytest.raises(DestinationRegistryError) as raised:
        Vault(EntityCatalog.load(tmp_path)).bundles()
    assert describe(raised.value).code == "E-CONFIG"


def test_entities_unknown_flag_is_config_not_unknown(tmp_path):
    """C2 (S6 review): a hand-edited `entities.yaml` naming a flag that
    `archetypes.yaml` never declares is valid YAML — the wrong VALUE, not
    the wrong shape — and `Vault.resolve_flags` raised a bare `ValueError`
    for it (`app/vault.py`), which reached the operator as `E-UNKNOWN`
    (and, through the C1 sidebar re-entrancy bug, an EMPTY 500 body) on
    every one of `bundles()`'s callers: `/`, `/triage`, `/triage/<entity>`,
    `/outbox/<entity>`, and `/registry/<entity>/products`. This is the
    SECOND of the two real shapes review measured; the existing test above
    covers the other (`modules:` entirely absent) — the review's point was
    that an existing test picking one shape does not prove its neighbour is
    covered, and here it was not."""
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  demo: {label: Demo, flags: [nosuchflag]}\n',
    )
    with pytest.raises(DestinationRegistryError) as raised:
        Vault(EntityCatalog.load(tmp_path)).bundles()
    assert describe(raised.value).code == "E-CONFIG"


# --- Task 8 corrective: bundles() shape-space boundary conversion ----------
#
# design §5 "Boundary conversions" requires every registry reader to
# normalize a failure that ESCAPES it — a list where a mapping is expected,
# raising `AttributeError` or `TypeError` on access — into
# `DestinationRegistryError`. `resolve_flags` / `active_modules` / `block_of`
# (the three functions `bundles()` reads `archetypes.yaml` through — they
# hold eight guarded accesses between them; see app/main.py on why the
# count is not written down, independent
# of `_destination_registry`'s own, stricter, semantic validation) did not.
#
# Each row below was measured against the UNCORRECTED `app/vault.py` with a
# standalone probe (no test framework, no monkeypatching) before this test,
# or the fix it pins, existed: `tolerated=True` rows already succeed today
# and MUST keep succeeding with the identical result; `tolerated=False` rows
# already raise a bare `AttributeError` or `TypeError` (reaching the
# operator as `E-UNKNOWN`, and — through the C1 sidebar-rebuild re-entrancy
# path `app/main.py` documents — an EMPTY 500 body on every one of
# `bundles()`'s five callers) and must become a typed
# `DestinationRegistryError` / `E-CONFIG` with no other behaviour change.
#
# Six representative shapes — `[]`, `{}`, `""`, `5`, `None`, and a nested
# variant (a list containing a mapping for `flags:`/`modules:`; a spec whose
# `requires_flag:` is itself a list for "a module spec") — at each of the
# three VALUE-shaped access points `bundles()` makes, PLUS a fourth axis
# added by the Task 8 corrective review: the `modules:` mapping KEY. C-A
# (S6 corrective review) found this axis was missing entirely — the value
# axes above vary the shape at three levels and never vary a key, and a
# `modules:` key that resolves to a non-string (a truncated `00-intake` ->
# `00` read back as int `0`; a YAML 1.1 bareword `on:`/`no:` read back as
# bool; a bare float; or a mix of string and int keys in the same mapping)
# is untouched by any value-shape row and reached the operator as an
# untyped `TypeError` at two DIFFERENT sites: `Vault.active_modules`'s
# `sorted(out)` (mixed-type keys, since a str and an int don't order) and
# `bundles()`'s own `(bundle_dir / name).is_dir()` (a non-`str`/`PathLike`
# key). This is not the 3x6 cross product any more — it is that cross
# product PLUS a `modules_key` axis with its own tolerated/fatal rows,
# interleaved the same way, so a test that could not tell the difference
# would fail somewhere in here rather than passing vacuously.
_SHAPE_SPACE = (
    # (level, value in archetypes.yaml, tolerated?, expected (name, block) pairs if tolerated)
    ("flags", [], True, [("00-intake", "system")]),
    ("flags", {}, True, [("00-intake", "system")]),
    ("flags", "", True, [("00-intake", "system")]),
    ("flags", 5, False, None),
    ("flags", None, True, [("00-intake", "system")]),
    ("flags", [{"a": 1}], False, None),
    ("modules", [], False, None),
    ("modules", {}, True, []),
    ("modules", "", False, None),
    ("modules", 5, False, None),
    ("modules", None, False, None),
    ("modules", [{"a": 1}], False, None),
    ("modules_key", "00-intake", True, [("00-intake", "system")]),
    ("modules_key", 0, False, None),
    ("modules_key", True, False, None),
    ("modules_key", False, False, None),
    ("modules_key", 1.5, False, None),
    ("modules_key", "__MIXED_STR_INT__", False, None),
    ("spec", [], True, [("00-intake", "")]),
    ("spec", {}, True, [("00-intake", "")]),
    ("spec", "", True, [("00-intake", "")]),
    ("spec", 5, False, None),
    ("spec", None, True, [("00-intake", "")]),
    ("spec", {"block": "system", "requires_flag": ["x"]}, False, None),
)


@pytest.mark.parametrize(
    "level, value, tolerated, expected",
    _SHAPE_SPACE,
    ids=[
        f"{level}-{shape!r}"
        for level, shape, _tolerated, _expected in _SHAPE_SPACE
    ],
)
def test_bundles_shape_space_boundary_conversion(tmp_path, level, value, tolerated, expected):
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    archetypes = {
        "version": "2.0",
        "flags": {},
        "modules": {"00-intake": {"block": "system"}},
    }
    if level == "flags":
        archetypes["flags"] = value
    elif level == "modules":
        archetypes["modules"] = value
    elif level == "modules_key":
        if value == "__MIXED_STR_INT__":
            # A registry with both string and int keys in `modules:` — the
            # int key (e.g. a bare `5`) is itself a valid single-module
            # shape (see the other `modules_key` rows), but MIXING it with
            # a string key is what makes `sorted(out)` raise: Python cannot
            # order a `str` against an `int`.
            archetypes["modules"] = {
                "00-intake": {"block": "system"},
                5: {"block": "system"},
            }
        else:
            archetypes["modules"] = {value: {"block": "system"}}
    else:
        assert level == "spec"
        archetypes["modules"] = {"00-intake": value}

    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        yaml.safe_dump(archetypes),
    )
    if level == "modules_key":
        # The four `modules_key` failure rows other than the mixed-keys one
        # raise only in `bundles()`'s own `(bundle_dir / name).is_dir()`
        # check, which short-circuits (`on_disk and not ...`) unless the
        # bundle directory actually exists — so the entity directory must be
        # real, unlike every other row in this space, whose failures happen
        # earlier in `active_modules`/`resolve_flags` regardless of disk state.
        (tmp_path / "alpha").mkdir(parents=True, exist_ok=True)
    vault = Vault(EntityCatalog.load(tmp_path))

    if tolerated:
        (bundle,) = vault.bundles()
        assert [(m.name, m.block) for m in bundle.modules] == expected
    else:
        with pytest.raises(DestinationRegistryError) as raised:
            vault.bundles()
        assert type(raised.value) is DestinationRegistryError
        assert describe(raised.value).code == "E-CONFIG"


def test_flags_as_plain_list_activates_a_gated_module(tmp_path):
    """I-A (S6 corrective review): every `tolerated=True` row for the
    `flags:` level in `_SHAPE_SPACE` above — `[]`, `{}`, `""`, `None` — is
    FALSY. `declared = set(cfg.get("flags") or {})` short-circuits every one
    of them to `set()` regardless of shape, and no module in that harness
    declares `requires_flag:`, so `declared` is never actually consulted by
    `resolve_flags`/`active_modules`. Those rows pass whether or not the
    `flags:` value is even read correctly — vacuously — and so cannot be the
    evidence for `_boundary`'s docstring claim that "a `flags:` written as a
    plain list of names works today via `set([...])` and must keep working."

    This is the one shape that discriminates: a NON-EMPTY, non-falsy list of
    flag names, feeding a module that actually requires one of them. If
    `_boundary` were replaced by the `isinstance(cfg.get("flags"), dict)`
    pre-check the design ruling considered and rejected, THIS row — not the
    four falsy ones — is the one that would wrongly go fatal, because a
    plain list is not a `dict` even though `set(a_list)` works fine on it.
    """
    from app.entities import EntityCatalog
    from app.vault import Vault

    archetypes = {
        "version": "2.0",
        "flags": ["beta"],
        "modules": {
            "00-intake": {"block": "system"},
            "zz-extra": {"block": "system", "requires_flag": "beta"},
        },
    }
    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: [beta]}\n',
        yaml.safe_dump(archetypes),
    )
    vault = Vault(EntityCatalog.load(tmp_path))

    (bundle,) = vault.bundles()
    assert sorted(m.name for m in bundle.modules) == ["00-intake", "zz-extra"]


def test_block_of_spec_shape_boundary_conversion_reached_independently_of_bundles(
    tmp_path,
):
    """`block_of()` is called directly by `Classifier.classify()`
    (app/classifier.py) with a module name read from `classifier/rules.yaml`
    — independently of `bundles()` / `active_modules()`, which happen to
    validate the identical shape first on THAT path only because they always
    run before `block_of` for the same module. Calling `block_of()` directly
    here, bypassing `active_modules()` entirely, proves the guard on this
    call path is load-bearing rather than dead code shadowed by the other
    one."""
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(
        tmp_path, ENTITIES,
        archetypes_yaml='version: "2.0"\nflags: {}\nmodules:\n  00-intake: [block, system]\n',
    )
    vault = Vault(EntityCatalog.load(tmp_path))
    with pytest.raises(DestinationRegistryError) as raised:
        vault.block_of("00-intake")
    assert describe(raised.value).code == "E-CONFIG"


def test_block_of_modules_shape_boundary_conversion(tmp_path):
    """Same independence as above, for the `modules:` top-level shape
    rather than a single module's spec."""
    from app.console_errors import describe
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(
        tmp_path, ENTITIES,
        archetypes_yaml='version: "2.0"\nflags: {}\nmodules: []\n',
    )
    vault = Vault(EntityCatalog.load(tmp_path))
    with pytest.raises(DestinationRegistryError) as raised:
        vault.block_of("00-intake")
    assert describe(raised.value).code == "E-CONFIG"


def test_boundary_readers_carry_no_blanket_try_of_their_own():
    """M2 (S6 corrective review): design §5 forbids a blanket
    `except (AttributeError, TypeError)` wrapping a whole reader function —
    conversion must narrow to the specific access it guards. Nothing pinned
    that requirement: hoisting the per-access `_boundary` calls inside
    `Vault.active_modules` into a single `try`/`except` around the WHOLE
    function body is invisible to every behavioural test above (every
    `_SHAPE_SPACE` row still passes, because the same set of escaping
    shapes still converts to the same typed error) — the only difference is
    that a blanket form also silently converts an unrelated bug anywhere
    else in the same function body, which Rule 5 forbids.

    This is therefore a structural check, not a behavioural one: parse
    `app/vault.py` and assert that none of the methods `bundles()` reads
    `archetypes.yaml` through contain a `try` statement of their own — every
    access-level conversion must go through the shared `_boundary` helper,
    whose own `try` wraps exactly one statement.
    """
    import ast
    import inspect

    import app.vault as vault_module

    tree = ast.parse(inspect.getsource(vault_module))

    (vault_class,) = (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Vault"
    )
    guarded = {"resolve_flags", "active_modules", "block_of", "bundles"}
    checked = set()
    for node in vault_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in guarded:
            checked.add(node.name)
            own_try = [n for n in ast.walk(node) if isinstance(n, ast.Try)]
            assert not own_try, (
                f"Vault.{node.name} contains its own try/except statement — "
                "every access-level conversion must go through `_boundary`, "
                "not a blanket catch wrapping the function body"
            )
    assert checked == guarded, f"method(s) not found on Vault: {guarded - checked}"

    (boundary_fn,) = (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_boundary"
    )
    (boundary_try,) = (n for n in ast.walk(boundary_fn) if isinstance(n, ast.Try))
    assert len(boundary_try.body) == 1, (
        "_boundary's own try body must stay a single statement "
        "(`return read()`) — widening it defeats the per-access narrowness "
        "this helper exists to enforce"
    )
