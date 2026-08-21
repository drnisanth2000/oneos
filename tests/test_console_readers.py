"""Structured readers declare a category; registry-category escaping failures
become E-CONFIG while absorbed tolerances are pinned (S6 Task 8, design §5
"Boundary conversions" and §7 invariant 4)."""
import ast
import pathlib
import textwrap

import pytest

from tests.conftest import write_vault

_TRIGGER_HELP = (
    "structured read site without a @structured_reader category declaration"
)

_APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

# Names that parse a structured document, whatever the receiver is called. The
# guard matches on the call shape, not on an import alias, because its entire
# purpose is catching sites nobody remembered to declare.
_YAML_LOADERS = {"safe_load", "load", "full_load", "unsafe_load",
                 "safe_load_all", "load_all"}
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
    return [modules.get(name, name) for name in _receiver_chain(node)]


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
        "def f(s):\n    return json.loads(s.system_path('x.json').read_text())",
        "def f(v):\n    p = v.system_path('members.yaml')\n    return p.read_bytes()",
        "def f(v):\n    p = v.system_path('x.json')\n    return json.loads(p.read_text())",
        "class C:\n    def __init__(self, v):\n        self._p = v.system_path('r.yaml')\n"
        "    def load(self):\n        return self._p.read_text()",
        "def f(p):\n    return yaml.safe_load_all(p)",
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
    from app.git_transaction import PathState

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    head_before = git_head(vault)
    real = outbox.capture_path_state

    def _vanished(root, rel):
        state = real(root, rel)
        return PathState.absent() if rel.endswith(".yaml") else state

    monkeypatch.setattr(outbox, "capture_path_state", _vanished)
    with pytest.raises(outbox.UnreadableProposalRecord) as raised:
        outbox.approve(scope, prop.id)
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
