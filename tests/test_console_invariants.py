import ast
import os
import pathlib
import stat as stat_module

import pytest

AMBIGUOUS = {"CrossScopeError", "ReviewedStateConflict",
             "UnsafeDestinationPath", "InvalidSourceLeaf"}


def test_no_direct_raise_of_an_ambiguous_base():
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                name = getattr(node.exc.func, "id", getattr(node.exc.func, "attr", ""))
                if name in AMBIGUOUS:
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_every_new_subtype_is_caught_as_its_base():
    from app.scope import CrossScopeError, OutOfScopeError, RedirectedPathError
    from app.outbox import ProposalSourceUnavailable, UnreadableProposalRecord, OutboxError
    from app.destinations import (
        UnsafeDestinationPath, RedirectedDestination, MissingDestination,
        InvalidSourceLeaf, RedirectedSourceLeaf, MissingSourceLeaf, NonCanonicalLeaf,
    )
    from app.git_transaction import (
        ReviewedStateConflict, ReviewedPathIntegrityError,
        ReviewedStateChanged, ReviewedPathUnavailable, InvalidTransactionPath,
    )
    pairs = [
        (OutOfScopeError, CrossScopeError),
        (RedirectedPathError, CrossScopeError),
        (ProposalSourceUnavailable, CrossScopeError),
        (UnreadableProposalRecord, OutboxError),
        (RedirectedDestination, UnsafeDestinationPath),
        (MissingDestination, UnsafeDestinationPath),
        (RedirectedSourceLeaf, InvalidSourceLeaf),
        (MissingSourceLeaf, InvalidSourceLeaf),
        (NonCanonicalLeaf, InvalidSourceLeaf),
        (ReviewedPathIntegrityError, ReviewedStateConflict),
        (ReviewedStateChanged, ReviewedStateConflict),
        (ReviewedPathUnavailable, ReviewedStateConflict),
        (InvalidTransactionPath, ReviewedStateConflict),
    ]
    for sub, base in pairs:
        with pytest.raises(base):
            raise sub("x")


# --- Task 6: invariants 1 and 2 ---------------------------------------------


def _application_modules():
    """Import every module under app/ except app.main, whose import executes
    build_catalog() at module scope and would read a live vault or fail
    without ONEOS_VAULT."""
    import importlib
    import pkgutil

    import app

    modules = []
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        if info.name == "app.main":
            continue
        modules.append(importlib.import_module(info.name))
    return modules


def _application_exception_classes():
    classes = set()
    for module in _application_modules():
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseException)
                and value.__module__ == module.__name__
            ):
                classes.add(value)
    return classes


def _probe(cls):
    try:
        return cls("probe")
    except TypeError:
        # Classes with structured constructors; describe() reads only the
        # class identity, so an uninitialized instance is a faithful probe.
        return cls.__new__(cls)


def _abstract_bases():
    from app.destinations import InvalidSourceLeaf, UnsafeDestinationPath
    from app.git_transaction import ReviewedStateConflict
    from app.scope import CrossScopeError

    return {
        CrossScopeError,
        ReviewedStateConflict,
        UnsafeDestinationPath,
        InvalidSourceLeaf,
    }


def test_every_application_exception_resolves_to_its_designed_code():
    from fastapi.exceptions import RequestValidationError

    from app import (
        destinations,
        entities,
        git_transaction,
        outbox,
        proposal_identity,
        registry,
        rename,
        scope,
        vault,
    )
    from app.console_errors import describe
    from app.ingest import base as ingest_base

    # app.main is excluded from the walk, and the root package is invisible
    # to it; assert by AST that neither defines an exception class, so the
    # exclusions cannot hide one.
    for source in ("app/main.py", "app/__init__.py"):
        tree = ast.parse(pathlib.Path(source).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                base_names = {
                    getattr(base, "id", getattr(base, "attr", ""))
                    for base in node.bases
                }
                assert not any(
                    "Error" in name or "Exception" in name for name in base_names
                ), f"{source} defines exception class {node.name}"

    exempt = _abstract_bases()
    for cls in _application_exception_classes():
        if cls in exempt:
            continue
        code = describe(_probe(cls)).code
        assert code != "E-UNKNOWN", f"{cls.__module__}.{cls.__qualname__} is unmapped"

    # The design's class map, transcribed as test data — this dict is not a
    # second runtime map; it exists so an implementation that maps a class to
    # a *wrong* non-unknown code fails here.
    expected = {
        git_transaction.GitTransactionCommittedError: "E-COMMITTED",
        git_transaction.GitTransactionRecoveryError: "E-RECOVER",
        git_transaction.ReviewedPathIntegrityError: "E-TAMPER",
        git_transaction.ReviewedPathUnavailable: "E-UNAVAILABLE",
        git_transaction.ReviewedStateChanged: "E-CONFLICT",
        git_transaction.InvalidTransactionPath: "E-INTERNAL",
        git_transaction.VaultBusyError: "E-BUSY",
        git_transaction.GitTransactionFailure: "E-GIT",
        git_transaction.GitTransactionError: "E-GIT",
        git_transaction._ApprovalLockCleanupFailure: "E-GIT",
        git_transaction._ReviewedIndexOwnershipConflict: "E-CONFLICT",
        scope.RedirectedPathError: "E-TAMPER",
        outbox.ProposalSourceUnavailable: "E-UNAVAILABLE",
        scope.OutOfScopeError: "E-SCOPE",
        outbox.OutboxScopeError: "E-SCOPE",
        outbox.UnreadableProposalRecord: "E-UNREADABLE",
        outbox.StaleProposalSource: "E-STALE",
        outbox.MissingProposalSource: "E-MISSING",
        outbox.ProposalFreshnessError: "E-STALE",
        outbox.OutboxTransactionError: "E-GIT",
        outbox.OutboxDestinationError: "E-INVALID",
        outbox.OutboxError: "E-INVALID",
        proposal_identity.ProposalIdentityError: "E-INVALID",
        destinations.RedirectedDestination: "E-TAMPER",
        destinations.RedirectedSourceLeaf: "E-TAMPER",
        destinations.NonCanonicalLeaf: "E-DEST",
        destinations.MissingSourceLeaf: "E-DEST",
        destinations.MissingDestination: "E-DEST",
        destinations.DestinationError: "E-DEST",
        vault.DestinationRegistryError: "E-CONFIG",
        entities.SystemRegistryPathError: "E-TAMPER",
        entities.RecipientConfigurationError: "E-CONFIG",
        entities.EntityManifestError: "E-CONFIG",
        entities.EntitySelectionError: "E-ENTITY",
        registry.RegistryTransactionError: "E-GIT",
        registry.RegistryError: "E-REGISTRY",
        ingest_base.IngestError: "E-INGEST",
        rename.RenameError: "E-ADMIN",
        RequestValidationError: "E-REQUEST",
    }
    for cls, code in expected.items():
        assert describe(_probe(cls)).code == code, cls.__qualname__


def _transitive_subclasses(cls):
    found = set()
    pending = list(cls.__subclasses__())
    while pending:
        current = pending.pop()
        if current in found:
            continue
        # Synthetic subclasses created inside other tests may linger in
        # __subclasses__() until collected; the invariant is about
        # application classes.
        if not current.__module__.startswith("app."):
            continue
        found.add(current)
        pending.extend(current.__subclasses__())
    return found


def test_closed_family_every_subclass_has_exact_entry():
    from app.console_errors import ALLOWLIST, _EXACT
    from app.git_transaction import GitTransactionError, GitTransactionFailure

    exempt = _abstract_bases()
    for cls in _transitive_subclasses(GitTransactionError):
        if cls in exempt:
            continue
        assert cls in _EXACT, f"{cls.__qualname__} lacks its own exact entry"

    # Same walk for the allowlist's membership: every private subclass of
    # GitTransactionFailure must be listed, so a new one fails here until it
    # is a deliberate member.
    for cls in _transitive_subclasses(GitTransactionFailure):
        assert cls in ALLOWLIST, f"{cls.__qualname__} is not allowlisted"


def test_no_domain_module_imports_the_taxonomy():
    forbidden = {"console_errors", "console_render"}
    offenders = []
    excluded = {"main.py", "console_render.py", "console_routing.py"}
    for path in pathlib.Path("app").rglob("*.py"):
        if path.name in excluded or path.name == "console_errors.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""} | {alias.name for alias in node.names}
            else:
                continue
            if any(
                part in forbidden
                for name in names
                for part in name.split(".")
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


# --- Task 3: the safe-read contract on `_read_no_follow_bytes` --------------
#
#   missing leaf                       -> FileNotFoundError (re-raised)
#   ELOOP / O_NOFOLLOW rejection       -> RedirectedPathError
#   fstat says non-regular             -> RedirectedPathError
#   any other OSError (perm, IO, race) -> ProposalSourceUnavailable
#
# Both raised types subclass CrossScopeError, so every existing `except`
# clause is unchanged and no refusal changes.


def test_safe_read_missing_leaf_raises_filenotfound(tmp_path):
    from app.outbox import _read_no_follow_bytes

    with pytest.raises(FileNotFoundError):
        _read_no_follow_bytes(tmp_path / "absent.md")


def test_safe_read_symlink_raises_redirected(tmp_path):
    from app.outbox import _read_no_follow_bytes
    from app.scope import CrossScopeError, RedirectedPathError

    target = tmp_path / "target.md"
    target.write_text("target\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    with pytest.raises(RedirectedPathError) as raised:
        _read_no_follow_bytes(link)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_nonregular_raises_redirected(tmp_path, monkeypatch):
    # A real file whose descriptor reports a non-regular st_mode. Opening a
    # FIFO read-only blocks forever without a writer, so no FIFO fixture —
    # monkeypatch os.fstat instead.
    import app.outbox as outbox
    from app.scope import CrossScopeError, RedirectedPathError

    regular = tmp_path / "regular.md"
    regular.write_text("regular\n", encoding="utf-8")
    real_fstat = os.fstat

    def nonregular_fstat(descriptor):
        result = real_fstat(descriptor)
        return os.stat_result(
            (stat_module.S_IFIFO | stat_module.S_IMODE(result.st_mode),)
            + tuple(result)[1:]
        )

    monkeypatch.setattr(outbox.os, "fstat", nonregular_fstat)

    with pytest.raises(RedirectedPathError) as raised:
        outbox._read_no_follow_bytes(regular)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_permission_error_raises_unavailable(tmp_path):
    from app.outbox import ProposalSourceUnavailable, _read_no_follow_bytes
    from app.scope import CrossScopeError

    unreadable = tmp_path / "unreadable.md"
    unreadable.write_text("unreadable\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        with pytest.raises(ProposalSourceUnavailable) as raised:
            _read_no_follow_bytes(unreadable)
    finally:
        unreadable.chmod(0o644)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_replacement_race_raises_redirected(tmp_path):
    # A directory swapped in where the reviewed file used to be.
    from app.outbox import _read_no_follow_bytes
    from app.scope import CrossScopeError, RedirectedPathError

    swapped = tmp_path / "swapped.md"
    swapped.mkdir()

    with pytest.raises(RedirectedPathError) as raised:
        _read_no_follow_bytes(swapped)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_other_oserror_raises_unavailable(tmp_path):
    from app.outbox import ProposalSourceUnavailable, _read_no_follow_bytes
    from app.scope import CrossScopeError

    parent = tmp_path / "sealed"
    parent.mkdir()
    leaf = parent / "receipt.md"
    leaf.write_text("sealed\n", encoding="utf-8")
    parent.chmod(0)
    try:
        with pytest.raises(ProposalSourceUnavailable) as raised:
            _read_no_follow_bytes(leaf)
    finally:
        parent.chmod(0o755)
    assert isinstance(raised.value, CrossScopeError)



# --- Invariant 6: the route declaration guard (design §7) --------------------
#
# design §7 invariant 6 requires a structural test that fails
#
#   (a) on any handler registered with FastAPI but carrying no `@console_route`
#       declaration,
#   (b) on any handler whose body contains a bare `except Exception`, and
#   (c) on any declaration whose tuple contains `Exception` or `BaseException`
#       — "checking only the body would still permit a declared catch-all".
#
# (c) is enforced at declaration time by `console_route()` raising ValueError
# and is covered in tests/test_console_render.py. (a) and (b) live here.
#
# (a) reads the registration fact from `app.routes` rather than from the
# source's `@app.get` decorators: registration is what the design's filter
# names, and a route added by `add_api_route` would be invisible to a
# decorator scan. Enumerating the router cannot omit a registered route.
#
# The endpoint filter excludes what the design says it means to exclude —
# FastAPI's OpenAPI and docs endpoints and the mounted StaticFiles app, none
# of which can satisfy a Console requirement — rather than keeping only
# `__module__ == "app.main"`. Filtering *in* by module name would exempt any
# handler defined elsewhere and registered on the app, which is application
# code the guard exists to check. Design §7's closing rule governs: "If a
# future finding is 'the design's list omits X', the correct fix is to delete
# the list and add the invariant that would have caught X."

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_COMPOSITION_ROOT = _REPO_ROOT / "app" / "main.py"


def _load_console_app(tmp_path, monkeypatch):
    """`app.main` bound to a throwaway vault. Importing it runs
    `build_catalog()` at module scope, so the vault must exist first — the
    same reason the module walk above excludes `app.main`."""
    import importlib

    from tests.conftest import scaffold_modules, write_vault

    write_vault(tmp_path, 'version: "1.0"\nentities:\n  alpha: '
                          "{ label: Alpha, flags: [] }\n")
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    return importlib.reload(main)


def _registered_console_endpoints(main):
    """Every endpoint the router will actually dispatch to, minus the
    framework's own. A Mount (the StaticFiles application) exposes no
    `endpoint` attribute at all."""
    for route in main.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        if (getattr(endpoint, "__module__", "") or "").startswith(
            ("fastapi.", "starlette.")
        ):
            continue
        yield endpoint


def test_every_registered_route_declares_its_catch_family(tmp_path, monkeypatch):
    from app.console_routing import ConsoleRoute

    main = _load_console_app(tmp_path, monkeypatch)

    checked, undeclared = [], []
    for endpoint in _registered_console_endpoints(main):
        checked.append(endpoint.__qualname__)
        # Identity, not mere presence: an arbitrary attribute of that name
        # must not pass for a declaration.
        if not isinstance(
            getattr(endpoint, "__console_route__", None), ConsoleRoute
        ):
            undeclared.append(endpoint.__qualname__)

    # Floor, so a sweep that silently matched nothing cannot pass by asserting
    # [] == []. S6 adds no route, so the count only ever falls by regression.
    assert len(checked) >= 11, f"the sweep saw only {checked}"
    assert undeclared == []


def _handled_exception_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        names = set()
        for element in node.elts:
            names |= _handled_exception_names(element)
        return names
    return {getattr(node, "id", getattr(node, "attr", ""))}


def _catch_all_offenders(source: pathlib.Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _handled_exception_names(node.type)
        if node.type is None or names & {"Exception", "BaseException"}:
            offenders.append(f"{source.name}:{node.lineno}")
    return offenders


def test_no_handler_in_the_composition_root_launders_with_a_catch_all(
    tmp_path, monkeypatch
):
    """Design §5: "Routes catch declared domain families, never bare
    `Exception`. A blanket catch would launder programmer errors into 200
    fragments, which is the opposite of S6's purpose."

    Scanned over every function in the files that own the registered
    endpoints, not only the endpoints themselves: a helper a route delegates
    to — `_outbox_list` is one — would launder exactly as effectively, and a
    route-only scan is the omission-shaped hole this branch has already paid
    for. The composition root has no legitimate use for a catch-all; the
    global fallback is registered through `@app.exception_handler(Exception)`,
    which is a decorator argument and not an `except` clause.

    Paths are anchored to this file, never to the process cwd. A relative
    `Path("app/main.py")` reads a *different* `app/main.py` when pytest runs
    from the enclosing checkout, passing green while the laundering it
    forbids is live — the same defect the ledger records as Task 8 finding I1.
    """
    import inspect

    main = _load_console_app(tmp_path, monkeypatch)

    sources = {_COMPOSITION_ROOT}
    for endpoint in _registered_console_endpoints(main):
        source = inspect.getsourcefile(endpoint)
        if source:
            sources.add(pathlib.Path(source).resolve())

    # Positive control: the detector must fire on a known catch-all
    # regardless of what the scanned files happen to contain, so this cannot
    # degrade into "the files were clean" the way a bare count would. A count
    # floor is deliberately absent — Task 12 rewrites four of the six current
    # `except` clauses, and a floor tuned to today's total would go
    # spuriously red and invite the next implementer to lower it.
    control = _REPO_ROOT / "tests" / "conftest.py"
    assert control.exists()
    synthetic = ast.parse("try:\n    f()\nexcept Exception:\n    pass\n")
    fired = [
        node
        for node in ast.walk(synthetic)
        if isinstance(node, ast.ExceptHandler)
        and _handled_exception_names(node.type) & {"Exception"}
    ]
    assert len(fired) == 1, "the catch-all detector does not fire"

    assert _COMPOSITION_ROOT in sources and _COMPOSITION_ROOT.exists()
    offenders = []
    for source in sorted(sources):
        offenders += _catch_all_offenders(source)
    assert offenders == []


def test_pulse_declaration_selects_the_fragment_surface(tmp_path, monkeypatch):
    """`pulse`'s declaration is not cosmetic, and the route inventory's
    `pulse | unchanged` is not literally true.

    design §5 is normative: "a route with no full-page template always uses
    the fragment renderer". `pulse` renders `blocks/pulse.html` and has none,
    so `surface="fragment-only"` is its real shape. §5's parenthetical list of
    fragment-only routes omits `pulse`, but §7 records that every enumeration
    in the design "was wrong at least once, and each was wrong in the
    direction of omission" — the rule governs, not the list.

    What changes: an error escaping `pulse` without `HX-Request` used to
    render the full `error.html` page and now renders the alert fragment.
    Status is unaffected — the global fallback forces the code's page status
    either way. Nothing pinned that before, so flipping `surface` to `"page"`
    would have passed silently.
    """
    from starlette.testclient import TestClient

    main = _load_console_app(tmp_path, monkeypatch)

    class _Boom:
        @staticmethod
        def now():
            raise RuntimeError("synthetic pulse failure - must not leak")

    monkeypatch.setattr(main, "datetime", _Boom)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.get("/blocks/pulse")

    assert response.status_code == 500          # E-UNKNOWN, never laundered to 200
    assert 'role="alert"' in response.text
    # blocks/alert.html is a bare div; error.html is a whole document. This is
    # what distinguishes the two surfaces.
    assert "<!doctype" not in response.text.lower()
    assert "synthetic pulse failure" not in response.text
