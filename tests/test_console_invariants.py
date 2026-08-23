import ast
import os
import pathlib
import re
import stat as stat_module
import textwrap
from html.parser import HTMLParser

import pytest

# PR #15 must-fix 7: anchored to this file, never to the process cwd. A bare
# `pathlib.Path("app")` resolves relative to whatever directory pytest is
# invoked from, so running from anywhere other than the repo root makes
# `rglob` yield nothing and every offender-scanning test below pass having
# scanned zero files — the exact defect already recorded twice in the ledger
# (Task 8 finding I1, Task 10a's C1) and fixed for `app/main.py`'s own scan,
# but left standing in the two scans below. Defined before its first use.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

AMBIGUOUS = {"CrossScopeError", "ReviewedStateConflict",
             "UnsafeDestinationPath", "InvalidSourceLeaf"}


def test_no_direct_raise_of_an_ambiguous_base():
    offenders = []
    for path in (_REPO_ROOT / "app").rglob("*.py"):
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
        review_tokens,
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
        review_tokens.ReviewedProposalChanged: "E-REVIEW",
        review_tokens.InvalidReviewToken: "E-REQUEST",
        review_tokens.ReviewContractViolation: "E-INTERNAL",
        review_tokens.ReviewTokenError: "E-INTERNAL",
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


def test_review_token_family_every_subclass_has_exact_entry():
    """S7's outcome family is closed the same way S5's is.

    `ReviewTokenError` is documented as never raised directly, so its base
    entry is a runtime backstop, not a home for real outcomes. Without this
    walk a future subclass would degrade silently to E-INTERNAL — a
    stop-and-inspect 500 — for what may in fact be an ordinary refusal that
    changed nothing. Declaring an outcome must be deliberate.
    """
    from app.console_errors import _EXACT
    from app.review_tokens import ReviewTokenError

    for cls in _transitive_subclasses(ReviewTokenError):
        assert cls in _EXACT, f"{cls.__qualname__} lacks its own exact entry"


def test_no_domain_module_imports_the_taxonomy():
    forbidden = {"console_errors", "console_render"}
    offenders = []
    excluded = {"main.py", "console_render.py", "console_routing.py"}
    for path in (_REPO_ROOT / "app").rglob("*.py"):
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


def _registered_console_endpoints(app):
    """Every endpoint the router will actually dispatch to, minus the
    framework's own. A Mount (the StaticFiles application) exposes no
    `endpoint` attribute at all."""
    for route in app.routes:
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
    for endpoint in _registered_console_endpoints(main.app):
        name = getattr(endpoint, "__qualname__", None) or repr(endpoint)
        checked.append(name)
        # Identity, not mere presence: an arbitrary attribute of that name
        # must not pass for a declaration.
        if not isinstance(
            getattr(endpoint, "__console_route__", None), ConsoleRoute
        ):
            undeclared.append(name)

    # Floor, so a sweep that silently matched nothing cannot pass by asserting
    # [] == []. S6 adds no route, so the count only ever falls by regression.
    assert len(checked) >= 11, f"the sweep saw only {checked}"
    assert undeclared == []


def _endpoint_source_files(app):
    """The files owning every registered application endpoint, and the
    endpoints whose source could not be resolved. An unresolvable endpoint —
    a callable instance, or a `functools.partial` — cannot be scanned, so it
    is returned for the caller to fail on by name rather than being dropped
    silently."""
    import inspect

    sources, unresolved = set(), []
    for endpoint in _registered_console_endpoints(app):
        try:
            source = inspect.getsourcefile(endpoint)
        except TypeError:      # a callable instance, not a function
            source = None
        if source:
            sources.add(pathlib.Path(source).resolve())
        else:
            unresolved.append(repr(endpoint))
    return sources, unresolved


def _label(source: pathlib.Path) -> str:
    """Repo-relative where possible, so two scanned files sharing a basename
    stay distinguishable in an offender list."""
    try:
        return str(source.relative_to(_REPO_ROOT))
    except ValueError:
        return source.name


def _handled_exception_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        names = set()
        for element in node.elts:
            names |= _handled_exception_names(element)
        return names
    return {getattr(node, "id", getattr(node, "attr", ""))}


def _target_names(target: ast.expr):
    """Every plain name an assignment target binds, at any nesting depth."""
    if isinstance(target, ast.Name):
        yield target.id
    for element in getattr(target, "elts", ()):
        yield from _target_names(element)


def _exception_aliases(tree: ast.Module) -> dict[str, set[str]]:
    """Names bound anywhere in the module to an exception class or a tuple of
    them, so `except _SOME_TUPLE` is resolved rather than read as an opaque
    name.

    Bindings are harvested from EVERY scope — module level, function bodies,
    class bodies, comprehensions — with no scope model, so a function-local
    name can shadow an unrelated module-level one. That over-approximates,
    which is the fail-closed direction for a guard.

    Resolved binding forms are exactly `Assign`, `AnnAssign` and `NamedExpr`,
    with `Name` targets or `Tuple`/`List` targets at any nesting depth
    (starred elements excepted — a starred target binds a list, which cannot
    be a runtime-valid `except` operand). Every other binding form — a `for`
    target, a `with ... as`, an `except ... as`, an import — and every
    non-trivial value expression such as a ternary or a computed tuple is NOT
    resolved. Stated as the rule the code implements rather than as a list of
    unsupported shapes, because a list is wrong in the direction it cannot
    see.

    Task 11 introduced the first such form in `app/` (`except
    _TRIAGE_CATCHES`). Before it, the ledger recorded aliased catch targets
    as an accepted blind spot on the explicit grounds that no such shape
    existed — the moment one does, the justification expires and the guard
    has to grow instead. An alias is exactly how a catch-all would hide from
    a name-matching scan.
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            if node.value is None:
                continue
            targets, value = [node.target], node.value
        else:
            continue
        bound = _handled_exception_names(value)
        for target in targets:
            # A tuple target unions rather than pairs positionally: that
            # over-approximates, which is the fail-closed direction for a
            # guard. `ast.walk` rather than `tree.body` so an alias bound
            # inside a module-level `if`/`try` is seen too. `_target_names`
            # recurses, because `_A, (_B,) = Exception, (Exception,)` binds a
            # working catch-all one level down.
            for name in _target_names(target):
                aliases.setdefault(name, set()).update(bound)

    # Fixpoint, so an alias of an alias resolves. Bounded by the number of
    # aliases: each pass either grows a set or terminates.
    for _ in range(len(aliases) + 1):
        grown = False
        for name, bound in list(aliases.items()):
            expanded = set(bound)
            for inner in bound:
                expanded |= aliases.get(inner, set())
            if expanded != bound:
                aliases[name] = expanded
                grown = True
        if not grown:
            break
    return aliases


def _catch_all_offenders(source: pathlib.Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    aliases = _exception_aliases(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _handled_exception_names(node.type)
        resolved = set(names)
        for name in names:
            resolved |= aliases.get(name, set())
        if node.type is None or resolved & {"Exception", "BaseException"}:
            offenders.append(f"{_label(source)}:{node.lineno}")
    return offenders


def test_no_route_source_file_launders_with_a_catch_all(
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
    main = _load_console_app(tmp_path, monkeypatch)

    sources, unresolved = _endpoint_source_files(main.app)
    sources.add(_COMPOSITION_ROOT)

    # Controls, driven through `_catch_all_offenders` itself — the function
    # the real scan calls, reading a real file off disk. An earlier revision
    # re-implemented the detection inline here, which exercised only
    # `_handled_exception_names` and left the traversal that opens the files
    # uncontrolled: breaking `ast.walk` passed green with live laundering in
    # the composition root. A control that does not run the code under test
    # is this branch's signature defect (ledger, Task 8 round 2 — "the test
    # added to prove the trigger passed while the trigger was dead").
    #
    # A count floor is deliberately absent — Task 12 rewrites four of the six
    # current `except` clauses, and a floor tuned to today's total would go
    # spuriously red and invite the next implementer to lower it. These two
    # controls prove the scanner works regardless of what `app/` contains.
    #
    # They live in their own directory so the synthetic vault root stays a
    # clean vault.
    controls = tmp_path / "_controls"
    controls.mkdir(exist_ok=True)

    positive = controls / "catch_all_control.py"
    positive.write_text(
        "_LAUNDER = Exception\n"                    # 1
        "_INDIRECT = _LAUNDER\n"                    # 2   alias of an alias
        "_ANNOTATED: type = Exception\n"            # 3   AnnAssign
        "_WAL = (_W := Exception)\n"                # 4   NamedExpr, alone
        "_TA, _TB = Exception, ValueError\n"        # 5   tuple target union
        "_NA, (_NB,) = Exception, (Exception,)\n"   # 6   NESTED tuple target
        "if True:\n    _NESTED = Exception\n"       # 7, 8  not in tree.body
        "def f():\n"                                # 9
        "    try:\n        g()\n"                   # 10, 11
        "    except ValueError:\n        pass\n"     # 12, 13
        "    except Exception:\n        pass\n"      # 14, 15
        "def h():\n"                                # 16
        "    try:\n        g()\n"                   # 17, 18
        "    except _LAUNDER:\n        pass\n"       # 19, 20
        "def i():\n"                                # 21
        "    try:\n        g()\n"                   # 22, 23
        "    except _INDIRECT:\n        pass\n"      # 24, 25
        "def j():\n"                                # 26
        "    try:\n        g()\n"                   # 27, 28
        "    except _ANNOTATED:\n        pass\n"     # 29, 30
        "def k():\n"                                # 31
        "    try:\n        g()\n"                   # 32, 33
        "    except _NESTED:\n        pass\n"        # 34, 35
        "def m():\n"                                # 36
        "    try:\n        g()\n"                   # 37, 38
        "    except _W:\n        pass\n"             # 39, 40
        "def n():\n"                                # 41
        "    try:\n        g()\n"                   # 42, 43
        "    except _TA:\n        pass\n"            # 44, 45
        "def o():\n"                                # 46
        "    try:\n        g()\n"                   # 47, 48
        "    except _NB:\n        pass\n",           # 49, 50
        encoding="utf-8",
    )
    # One clause per resolution stage, so no stage can rot into a no-op while
    # another carries the assertion: 14 the literal catch-all, 19 a direct
    # alias, 24 alias-of-alias (the fixpoint), 29 AnnAssign, 34 a binding
    # outside tree.body, 39 NamedExpr alone, 44 a tuple target (the union),
    # 49 a NESTED tuple target (the recursion). `except ValueError` at line
    # 12 proves a declared family is not reported.
    assert sorted(_catch_all_offenders(positive)) == [
        "catch_all_control.py:14",
        "catch_all_control.py:19",
        "catch_all_control.py:24",
        "catch_all_control.py:29",
        "catch_all_control.py:34",
        "catch_all_control.py:39",
        "catch_all_control.py:44",
        "catch_all_control.py:49",
    ]

    negative = controls / "catch_all_clean.py"
    negative.write_text(
        "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n",
        encoding="utf-8",
    )
    assert _catch_all_offenders(negative) == []

    # Control for the *collection* stage, which the two above cannot reach:
    # with it broken, a laundering handler in a module other than the
    # composition root would never be scanned. `_COMPOSITION_ROOT` is seeded
    # unconditionally, so the design-required scope survives regardless —
    # this controls the widening beyond it.
    import importlib.util

    from fastapi import FastAPI

    probe_source = controls / "endpoint_source_control.py"
    probe_source.write_text("def endpoint():\n    return None\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "endpoint_source_control", probe_source
    )
    probe_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_module)
    scratch = FastAPI()
    scratch.add_api_route("/control", probe_module.endpoint, methods=["GET"])
    collected, collected_unresolved = _endpoint_source_files(scratch)
    assert probe_source.resolve() in collected
    assert collected_unresolved == []

    # An endpoint whose source cannot be resolved cannot be scanned, so it
    # fails here by name rather than escaping the ban unnoticed.
    assert unresolved == []
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


# --- Task 13a: design §7 invariant 5 -----------------------------------------
#
# "A test scans templates/ for hx-vals attributes and fails on any whose
# value is not a single {{ ... | tojson }} expression." Before this task the
# check existed only as a triage-specific assertion
# (tests/test_app.py::test_triage_serializes_canonical_destination_as_one_hx_vals_mapping),
# which is the shape invariant 6 was in before Task 10a — carried in the
# ledger as "Design §7 invariant 5's general hx-vals scan over templates/
# exists in no test file and no task's step list... Close it the same way: a
# scan, not a list." Task 13's own list of offenders names only the two known
# templates (registry.html, blocks/delete_impact.html), which is exactly the
# two-row list design §6 says is not enforcement.
#
# C2 (round 1 review): the first version of this scan matched `hx-vals='...'`
# as a byte sequence, not an attribute — `re.compile(r"hx-vals='([^']*)'")`.
# Five spellings defeated it: double-quoted, whitespace around `=`, a
# newline before the value, an unquoted value, and `HX-VALS` in upper case.
# Two are live exploits — rewriting `registry.html` to the double-quoted or
# the whitespace-around-`=` form let a hand-built, injectable mapping ship
# while the scan stayed green. The shape check was separately porous: `\S+`
# for the expression half absorbed a leading `x|safe` in `{{ x|safe|tojson
# }}`, so a filter chain longer than a bare `tojson` still matched —
# fail-open in the direction that matters. And the collection stage was
# uncontrolled, the same defect Task 10a's C2/MN2 closed for
# `_endpoint_source_files`: swapping `rglob` for a non-recursive `glob`
# silently dropped every file under `templates/blocks/` — including
# `delete_impact.html` itself — and the scan still passed. Fixed then by
# parsing real start tags with `html.parser`.
#
# Round 2 review found the round-1 fix still incomplete:
#
# R2-C1 — `data-hx-vals` was invisible. htmx honours it as a fallback for
# every `hx-*` attribute (`ee(e,t)||ee(e,"data-"+t)` in
# `static/vendor/htmx.min.js`), so `data-hx-vals='...'` is exploitable
# exactly like `hx-vals='...'` and the exact `name == "hx-vals"` check let it
# straight through. Now both names are recognized.
#
# R2-C2 — the collection control asserted only descent (`rglob` vs `glob`),
# never root coverage: `rglob("*/*.html")` (skips every top-level template)
# and `rglob("blocks/*.html")` (the control directory was itself named
# `blocks`) both passed the old single-file, single-directory control. The
# control tree now carries a hostile file at its root AND nested in a
# subdirectory not named `blocks`, with both paths in one assertion, so a
# mutation dropping either coverage is individually red.
#
# R2-I1 — the parser only sees `hx-vals` lexically inside a well-formed
# start tag. Three shapes defeat it, each rendering to a working hand-built
# `hx-vals`: an attribute wrapped in `{% if %}` or `{% for %}` (HTMLParser
# still opens the tag but tokenizes a mangled attribute name like
# `%}hx-vals`, so the exact-name check correctly does not match it — and
# nothing else does either), and one emitted by a `{% macro %}` (the literal
# text sits outside any tag HTMLParser opens at all). A raw-text backstop
# below finds every `(?:data-)?hx-vals\s*=` occurrence in the source and
# fails closed on any the parser did not explain via a genuine,
# whitespace-bounded attribute token inside a tag it actually opened — this
# is also what catches a tag left unterminated at EOF (R2-M2): HTMLParser
# never emits an event for one, even after `close()`, so it can never be
# "explained" either.
#
# R2-M1 — the shape check's own comment says its job is "so a second filter
# has nowhere to hide", which is what requiring exactly one `|` achieves.
# The regex it shipped with instead restricted the LEFT side to a bare
# dotted identifier, rejecting legitimate, equally safe markup: a
# server-built dict literal, a subscript (the natural per-row form), a
# function call, and `tojson` with its own filter arguments. Replaced with a
# check that enforces what the comment claims and nothing more.

_TEMPLATES_ROOT = _REPO_ROOT / "templates"

# Raw-text backstop (R2-I1): every literal occurrence of the attribute name,
# regardless of surrounding syntax, quote, or case.
_RAW_HX_VALS_RE = re.compile(r"(?:data-)?hx-vals\s*=", re.IGNORECASE)
# A genuine attribute token is preceded by whitespace or starts the tag
# text — never glued to other syntax. This is what excludes the
# Jinja-mangled "%}hx-vals=" HTMLParser produces from a `{% if %}` / `{% for
# %}` guard: the parser DID open a tag there, but never tokenized "hx-vals"
# as its own attribute name, so it must not count as explained.
_ATTR_TOKEN_RE = re.compile(r"(?:^|(?<=\s))(?:data-)?hx-vals\s*=", re.IGNORECASE)


def _hx_vals_value_is_clean(value: str) -> bool:
    """True when `value` is a single `{{ EXPR | tojson[(...)] }}` expression
    with EXACTLY one `|` (R2-M1) — the rule this guard's own comment states.
    Any second `|` (a hidden filter chain), any text outside one outer
    `{{ }}` pair (a second expression, or a missing/extra brace), or a
    filter other than `tojson` fails it. The left-hand EXPR is otherwise
    unconstrained — a dict literal, a subscript, and a call are all equally
    safe, since `tojson` still runs last and exactly once.

    One exception, and it is a real fail-open otherwise: Jinja binds a filter
    TIGHTER than `if`/`else`/`and`/`or`, so `{{ raw if y else v | tojson }}`
    parses as `raw if y else (v | tojson)`. When `y` is truthy the output is
    `raw`, autoescaped as HTML rather than JSON — a `"` becomes `&#34;`, which
    the browser decodes back to a delimiter inside the attribute. A textual
    split cannot model Jinja precedence, so a bare conditional or boolean
    operator in EXPR is rejected outright rather than reasoned about."""
    value = value.strip()
    if len(value) < 4 or not (value.startswith("{{") and value.endswith("}}")):
        return False
    inner = value[2:-2]
    parts = inner.split("|")
    if len(parts) != 2:
        return False
    expr, filt = parts[0].strip(), parts[1].strip()
    if not expr or "{{" in expr or "}}" in expr:
        return False
    if re.search(r"(?:^|\s)(?:if|else|and|or)(?:\s|$)", expr):
        return False
    return re.fullmatch(r"tojson(?:\([^{}]*\))?", filt) is not None


class _HxValsAttrParser(HTMLParser):
    """Collects every `hx-vals` / `data-hx-vals` (R2-C1) attribute value
    from real start tags, regardless of quote style, whitespace around `=`,
    a newline between the attribute name and its value, or the name's case —
    the five spellings a `hx-vals='...'` regex over raw bytes cannot see
    (review C2). Also records every start tag's own position and raw text,
    which the raw-text backstop below (R2-I1) uses to decide which literal
    `hx-vals=` occurrences it has already explained."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found: list[tuple[int, str]] = []
        self.tags: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self._record_tag()
        self._collect(attrs)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._record_tag()
        self._collect(attrs)

    def _record_tag(self) -> None:
        line, col = self.getpos()
        self.tags.append((line, col, self.get_starttag_text() or ""))

    def _collect(self, attrs) -> None:
        for name, value in attrs:
            if name in ("hx-vals", "data-hx-vals") and value is not None:
                self.found.append((self.getpos()[0], value))


def _line_start_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _explained_offsets(text: str, tags: list[tuple[int, int, str]]) -> set[int]:
    """Absolute offsets of every hx-vals-like attribute token the parser
    actually recognized inside a start tag it opened — searched within each
    tag's own verbatim text, never the whole document, so a boundary match
    can never cross into text no tag covers (R2-I1)."""
    line_starts = _line_start_offsets(text)
    explained = set()
    for lineno, col, tag_text in tags:
        base = line_starts[lineno - 1] + col
        for match in _ATTR_TOKEN_RE.finditer(tag_text):
            explained.add(base + match.start())
    return explained


def _hx_vals_offenders(
    source: pathlib.Path, root: pathlib.Path | None = None
) -> list[str]:
    """Every `hx-vals` / `data-hx-vals` attribute in `source` whose value is
    not a single `{{ EXPR | tojson[(...)] }}` expression, labelled
    `file:line`, UNIONED with every raw `(?:data-)?hx-vals\\s*=` occurrence
    the HTML parse could not explain (R2-I1) — a conditional attribute
    wrapped in Jinja `{% if %}` / `{% for %}`, one emitted by a `{% macro
    %}`, or a tag left unterminated at EOF (R2-M2), none of which the parser
    alone can see.

    `root`, when given, labels offenders relative to it instead of the repo
    root — used by `_scan_hx_vals` so a collection-stage control rooted
    outside the repo (R2-C2) still distinguishes a nested file from a
    top-level one, rather than both collapsing to a bare basename."""
    text = source.read_text(encoding="utf-8")
    label = source.relative_to(root) if root is not None else _label(source)

    parser = _HxValsAttrParser()
    parser.feed(text)
    # R2-M2: flush any tag left buffered at EOF. HTMLParser never emits an
    # event for a genuinely unterminated tag even after close() — this call
    # is about leaving the parser in a settled state before the backstop
    # below reads its `tags`/`found` collections, not about surfacing the
    # unterminated tag itself. The backstop is what actually catches it:
    # with no start-tag event, it is never in `explained`.
    parser.close()

    offenders = set()
    for line, value in parser.found:
        if not _hx_vals_value_is_clean(value):
            offenders.add(f"{label}:{line}")

    explained = _explained_offsets(text, parser.tags)
    for match in _RAW_HX_VALS_RE.finditer(text):
        if match.start() in explained:
            continue
        line = text.count("\n", 0, match.start()) + 1
        offenders.add(f"{label}:{line}")

    return sorted(offenders)


def _scan_hx_vals(root: pathlib.Path) -> list[str]:
    """Walks `root` recursively for offenders. A thin wrapper so the
    *collection* stage (`rglob`, not `glob`) is itself something a control
    can exercise — the same shape as `_endpoint_source_files` (Task 10a),
    and for the same reason: a scanner that finds every offender in a file
    it never opens still passes.

    R2-M4 (recorded, not fixed — a stated limit rather than a hand-maintained
    list, per design §7's closing rule): this only walks `*.html`, so a
    `.jinja`/`.j2` extension is invisible, and `rglob` does not follow
    symlinked subdirectories by default, so a symlinked template directory
    would be invisible too. Nothing under `templates/` is either today."""
    offenders = []
    for path in sorted(root.rglob("*.html")):
        offenders += _hx_vals_offenders(path, root)
    return offenders


def test_no_template_hand_builds_hx_vals(tmp_path):
    """Rule 8 / invariant 5, closed as a scan over the real `templates/`
    tree rather than as a list of known offenders.

    Controls, driven through `_hx_vals_offenders` and `_scan_hx_vals`
    themselves rather than re-implementing the shape, the parser backstop,
    or the collection stage inline — the branch's signature defect (ledger,
    Task 8 round 2 and Task 10a's C1/C2): a control that does not exercise
    the real function proves nothing about it. Written to `tmp_path`, not
    the repo's own `templates/`, so the positive control's deliberately
    broken markup never touches a real template.
    """
    controls = tmp_path / "_controls"
    controls.mkdir(exist_ok=True)

    positive = controls / "hostile.html"
    positive.write_text(
        "<button hx-vals='{\"slug\": \"{{ slug }}\"}'>hand-built, single-quoted</button>\n"  # 1
        "<button hx-vals=\"{'slug': '{{ slug }}'}\">hand-built, double-quoted</button>\n"    # 2
        "<button hx-vals ='{\"slug\": \"{{ slug }}\"}'>hand-built, space before =</button>\n"  # 3
        "<button hx-vals={{slug}}>hand-built, unquoted, no filter at all</button>\n"          # 4
        "<button hx-vals='{{ a }}{{ b | tojson }}'>not a single expression</button>\n"        # 5
        "<button hx-vals='{{ x|safe|tojson }}'>filter chain, not tojson alone</button>\n"     # 6
        "<button data-hx-vals='{\"slug\": \"{{ slug }}\"}'>data-hx-vals hand-built (R2-C1)</button>\n"  # 7
        "<button {% if s %}hx-vals='{\"slug\": \"{{ s }}\"}'{% endif %}>conditional if (R2-I1)</button>\n"  # 8
        "<button {% for a in b %}hx-vals='{\"slug\": \"{{ s }}\"}'{% endfor %}>conditional for (R2-I1)</button>\n"  # 9
        "{% macro vals(s) %}hx-vals='{\"slug\": \"{{ s }}\"}'{% endmacro %}<button {{ vals(s) }}>macro-emitted (R2-I1)</button>\n"  # 10
        "<button hx-vals='{{ values | tojson }}'>clean, must NOT be reported</button>\n"      # 11, clean
        "<button hx-vals=\"{{ values | tojson }}\">clean, double-quoted</button>\n"           # 12, clean
        "<button hx-vals={{values|tojson}}>clean, unquoted, no spaces</button>\n"             # 13, clean
        "<button\n  hx-vals\n  =\n  '{{ values | tojson }}'>clean, newline before value</button>\n"  # 14-17, clean
        "<button HX-VALS='{{ values | tojson }}'>clean, uppercase attribute name</button>\n"  # 18, clean
        "<button data-hx-vals='{{ values | tojson }}'>clean, data-hx-vals (R2-C1)</button>\n"  # 19, clean
        "<button hx-vals='{{ {\"id\": prop.id} | tojson }}'>clean, dict literal (R2-M1)</button>\n"  # 20, clean
        "<button hx-vals='{{ vals[loop.index0] | tojson }}'>clean, subscript (R2-M1)</button>\n"  # 21, clean
        "<button hx-vals='{{ make_vals(prop) | tojson }}'>clean, function call (R2-M1)</button>\n"  # 22, clean
        "<button hx-vals='{{ values | tojson(indent=0) }}'>clean, filter args (R2-M1)</button>\n"  # 23, clean
        "<button hx-vals='{{ x|tojson|replace(\"a\",\"b\") }}'>two filters, tojson first</button>\n"  # 24
        "<button hx-vals='{{ v | tojson if y else z }}'>cond-expr around the filter</button>\n"  # 25
        "<button hx-vals='{{ raw if y else v | tojson }}'>cond-expr BEFORE the filter</button>\n"  # 26
        "<button hx-vals='unterminated, no closing angle bracket (R2-M2)",  # 27 — MUST stay last: EOF, no ">"
        encoding="utf-8",
    )
    # Every hostile spelling review found across both rounds — hand-built
    # JSON in single quotes, double quotes, extra whitespace around `=`,
    # unquoted with no filter at all, `data-hx-vals`, a value carrying more
    # than one `{{ }}` expression, a filter chain longer than a bare
    # `tojson`, the same attribute wrapped in a Jinja `{% if %}` / `{% for
    # %}`, one emitted through a `{% macro %}`, and a tag left unterminated
    # at EOF — must all be reported. A set, not a sorted list: line 24 pushes
    # numbering into two digits, and a plain string sort would place
    # "...:10" before "...:2", which is not the property this test is
    # checking.
    assert set(_hx_vals_offenders(positive)) == {
        "hostile.html:1",
        "hostile.html:2",
        "hostile.html:3",
        "hostile.html:4",
        "hostile.html:5",
        "hostile.html:6",
        "hostile.html:7",
        "hostile.html:8",
        "hostile.html:9",
        "hostile.html:10",
        # 24 pins the "exactly one `|`" count rule and 25/26 pin the
        # tojson-identity rule; without them `len(parts) != 2` -> `< 2` and
        # `fullmatch(...)` -> `"tojson" in filt` both passed green. 26 is the
        # Jinja-precedence fail-open: it renders HTML-autoescaped, not JSON.
        "hostile.html:24",
        "hostile.html:25",
        "hostile.html:26",
        "hostile.html:27",
    }

    negative = controls / "clean.html"
    negative.write_text(
        "<button hx-vals='{{ values | tojson }}'>ok</button>\n"
        "<button hx-vals='{{ proposal_values | tojson }}'>ok too</button>\n",
        encoding="utf-8",
    )
    assert _hx_vals_offenders(negative) == []

    # Control for the *collection* stage (R2-C2), which the two checks above
    # cannot reach: a hostile file at the control tree's ROOT, and one
    # nested under a subdirectory NOT named `blocks` — both in one
    # assertion. A root-only fixture cannot red `rglob("*/*.html")` (which
    # skips every top-level template but would still find the nested file);
    # a `blocks`-named subdirectory cannot red `rglob("blocks/*.html")`
    # (which would still find a file nested exactly there). Together they
    # force `glob("*.html")`, `rglob("*/*.html")`, and `rglob("blocks/*.html")`
    # to each be individually red, rather than only the `rglob` vs `glob`
    # descent the round-1 control checked.
    nested_root = tmp_path / "_nested_templates"
    (nested_root / "deep").mkdir(parents=True)
    (nested_root / "top.html").write_text(
        "<button hx-vals='{\"slug\": \"{{ slug }}\"}'>root-level hand-built</button>\n",
        encoding="utf-8",
    )
    (nested_root / "deep" / "nested.html").write_text(
        "<button hx-vals='{\"slug\": \"{{ slug }}\"}'>nested hand-built</button>\n",
        encoding="utf-8",
    )
    assert _scan_hx_vals(nested_root) == ["deep/nested.html:1", "top.html:1"]

    # Every real template in this repo, scanned by walking the tree rather
    # than by naming files — the same shape as invariant 6's guard, and for
    # the same reason: a hand-maintained list is wrong in the direction of
    # omission (design §7).
    assert _TEMPLATES_ROOT.is_dir()
    scanned = sorted(_TEMPLATES_ROOT.rglob("*.html"))
    assert scanned, "expected at least one template under templates/"
    assert _scan_hx_vals(_TEMPLATES_ROOT) == []


# --- C2 (S6 review): scope.resolve() must never precede the lexical -------
# --- symlink check on the path it resolves --------------------------------
#
# Design §2 / §7's closing rule: a redirection finding must classify as
# `E-TAMPER`, never `E-SCOPE`. `Scope.resolve(*parts)` raises `OutOfScopeError`
# (-> E-SCOPE) the moment a resolved SUBPATH lands outside the entity root —
# which is exactly what happens when a symlinked component redirects it —
# so any site that calls `scope.resolve(<parts>)` before classifying its own
# lexical (pre-resolution) counterpart's `.is_symlink()` status reports an
# ordinary scope refusal for what is actually a redirection. Three such
# sites shipped after must-fix 6 fixed the first two
# (`destinations.py::_require_real_directory`, `inbox.py::_require_real_directory`):
# `destinations.py`'s own final destination-leaf check,
# `outbox.py::_require_outbox_path`, and `registry.py::_delete_proposal_path`.
#
# Per §7, the fix is not a fourth hand-found patch on top of three — it is
# this invariant, so a future site with the same shape is a red test, not a
# sixth review finding.


def _mentions_scope_root(node: ast.AST) -> bool:
    """True if `node`'s subtree references `scope.root` — the marker for
    "this expression builds a scope-relative LEXICAL path", the thing this
    invariant requires be `.is_symlink()`-checked before the corresponding
    `scope.resolve(...)` call."""
    return any(
        isinstance(n, ast.Attribute)
        and n.attr == "root"
        and isinstance(n.value, ast.Name)
        and n.value.id == "scope"
        for n in ast.walk(node)
    )


def _scope_resolve_call_argcount(node: ast.AST) -> int | None:
    """Arg count of a `scope.resolve(...)` call, or None if `node` is not
    one."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resolve"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "scope"
    ):
        return len(node.args)
    return None


def _is_symlink_call_target(node: ast.AST) -> str | None:
    """The bare variable name of a no-arg `<name>.is_symlink()` call, or
    None."""
    if (
        isinstance(node, ast.Call)
        and not node.args
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "is_symlink"
        and isinstance(node.func.value, ast.Name)
    ):
        return node.func.value.id
    return None


def _iter_in_source_order(node: ast.AST):
    """Pre-order DFS over field order, which — unlike `ast.walk`'s BFS —
    approximates source order for a function's statements closely enough for
    this structural check (the same "structural, not a full control-flow
    proof" scope every invariant in design §7 accepts)."""
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _iter_in_source_order(child)


def _mentions_any_name(node: ast.AST, names: set[str]) -> bool:
    return any(
        isinstance(n, ast.Name) and n.id in names for n in ast.walk(node)
    )


def _resolve_before_lexical_symlink_offenders(
    tree: ast.AST, label: str, *, require_anchor: bool = True
) -> list[str]:
    """Fails a function where a `scope.resolve(<parts>)` call — one that CAN
    raise `OutOfScopeError` for a redirected SUBPATH, i.e. carries at least
    one path-part argument — is reached before a `.is_symlink()` check on
    the "anchor path" this same function already built for it.

    An anchor path is either:
      - a `scope.root`-derived lexical path (`scope.root / ...`), the shape
        `_require_real_directory` and the three C2 fixes all use; or
      - a path built from a name already recognized as an anchor — which
        includes the RESULT of a zero-arg `scope.resolve()` (`Scope.resolve()`
        with no parts already raises the TAMPER-appropriate
        `RedirectedPathError` for a symlinked entity root itself, so its
        result is as trustworthy as a lexical path — `app/registry.py`'s
        pre-fix `_delete_proposal_path` built exactly this shape,
        `bound_outbox = entity_root / "outbox"`, as its OWN intended
        comparison target and simply never checked it).

    A function that builds no anchor path at all is, by default
    (`require_anchor=True`), not flagged — there is no "corresponding
    lexical path" for the invariant to police there (the same exemption
    style as invariant 3's ambiguous bases: refuse silence on the shape that
    exists, not a universal claim that every `scope.resolve()` call anywhere
    is provably safe). A library helper may legitimately rely on a caller,
    or on a function it calls in turn, to have already validated the path —
    exactly why this stays the default for the general, file-wide scan.

    `app/main.py`'s pre-fix `propose()` was exactly this shape — it called
    `scope.resolve("00-inbox", "active")` directly with no anchor path built
    at all — and was the Task 8 corrective residual: a function with NO
    anchor at all is invisible to the default scan, which is exactly why the
    misclassification shipped undetected. The fix is not a `propose()`
    special case; it is `require_anchor=False`, a second axis this same
    scanning function now also walks. Passed by the Stage 5 route-level scan
    below, it makes "no anchor was ever built" itself the offense, because a
    REGISTERED ROUTE HANDLER is the request boundary — nothing wraps it, so
    there is no caller left to have done the check instead. `require_anchor`
    is a parameter of this one function, not a second copy of it, so both
    scans share every other line of detection logic and cannot drift apart.
    """
    offenders = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        anchor_names: set[str] = set()
        checked_names: set[str] = set()
        for node in _iter_in_source_order(func):
            if node is func:
                continue
            if isinstance(node, ast.Assign):
                is_anchor = _mentions_scope_root(node.value) or (
                    anchor_names and _mentions_any_name(node.value, anchor_names)
                )
                zero_arg_resolve = _scope_resolve_call_argcount(node.value) == 0
                if is_anchor or zero_arg_resolve:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            anchor_names.add(target.id)
                    continue
            symlink_target = _is_symlink_call_target(node)
            if symlink_target is not None:
                checked_names.add(symlink_target)
                continue
            argcount = _scope_resolve_call_argcount(node)
            if argcount is not None and argcount >= 1:
                if anchor_names:
                    if not (anchor_names & checked_names):
                        offenders.append(f"{label}:{node.lineno} in {func.name}")
                elif not require_anchor:
                    # Stage 5: no anchor was ever built, and this scan (a
                    # route handler — the request boundary) does not exempt
                    # that. Under the default `require_anchor=True` this
                    # branch never fires, so every existing library-helper
                    # assertion above is unchanged.
                    offenders.append(f"{label}:{node.lineno} in {func.name}")
    return offenders


def test_c2_positive_control_resolve_before_symlink_check_is_flagged():
    """Stage 1: the guard must fire on the exact defective shape — a lexical
    path built, then `scope.resolve()` called on it BEFORE `.is_symlink()`
    is ever checked."""
    src = textwrap.dedent(
        """
        def f(scope, leaf):
            lexical_outbox = scope.root / scope.current_entity() / "outbox"
            resolved_outbox = scope.resolve("outbox")
            if lexical_outbox.is_symlink() or resolved_outbox != lexical_outbox:
                raise RedirectedPathError("bad")
            return resolved_outbox
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_positive.py"
    )
    assert offenders, "guard failed to flag a resolve-before-symlink-check shape"


def test_c2_negative_control_symlink_check_first_is_not_flagged():
    """Stage 2: the guard must NOT fire on the corrected shape — the same
    code with the check reordered, proving the guard is not merely
    "resolve() and is_symlink() both present anywhere in the function"."""
    src = textwrap.dedent(
        """
        def f(scope, leaf):
            lexical_outbox = scope.root / scope.current_entity() / "outbox"
            if lexical_outbox.is_symlink():
                raise RedirectedPathError("bad")
            resolved_outbox = scope.resolve("outbox")
            if resolved_outbox != lexical_outbox:
                raise RedirectedPathError("bad")
            return resolved_outbox
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_negative.py"
    )
    assert offenders == []


def test_c2_zero_arg_resolve_result_becomes_its_own_anchor():
    """Stage 3a: a zero-arg `scope.resolve()` result is itself trustworthy
    (`Scope.resolve()` with no parts already raises the TAMPER-appropriate
    `RedirectedPathError` for a symlinked entity root), so a subpath BUILT
    FROM it is a valid anchor and must be `.is_symlink()`-checked like any
    other before a later `scope.resolve(<args>)` call trusts it. This is
    exactly `app/registry.py`'s pre-fix `_delete_proposal_path` shape —
    `bound_outbox = entity_root / "outbox"` — which had no `scope.root`
    anywhere in it and would be invisible to a guard that only recognized
    lexical paths built directly from `scope.root`."""
    src = textwrap.dedent(
        """
        def f(scope):
            entity_root = scope.resolve()
            bound_outbox = entity_root / "outbox"
            resolved_outbox = scope.resolve("outbox")
            return resolved_outbox
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_zero_arg_anchor.py"
    )
    assert offenders, "a subpath derived from scope.resolve() must be a tracked anchor"


def test_c2_bare_zero_arg_resolve_alone_is_never_flagged():
    """Stage 3b: a bare zero-arg `scope.resolve()` call, with no later
    args-carrying `scope.resolve()` call in the same function to guard, is
    never itself the offending call — there is nothing after it for the
    invariant to police, and a zero-arg call can never be the ONE flagged
    (only calls with `argcount >= 1` are ever appended to `offenders`)."""
    src = textwrap.dedent(
        """
        def f(scope):
            entity_root = scope.resolve()
            return entity_root
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_bare_zero_arg.py"
    )
    assert offenders == []


def test_c2_resolve_with_no_local_lexical_path_is_out_of_scope_for_the_guard():
    """Stage 4: under the DEFAULT (`require_anchor=True`, general/library)
    scan, a function with no `scope.root`-derived path at all is not flagged
    merely for calling `scope.resolve(<parts>)` — there is nothing local for
    the invariant to compare it against, and a library helper may
    legitimately rely on its caller (or a function it calls in turn) to have
    already validated the path.

    This was `app/main.py`'s pre-fix `propose()` shape exactly, and — read
    only against this default scan — is indistinguishable from a genuinely
    safe helper. That indistinguishability was the Task 8 corrective
    residual: `propose()` is a registered ROUTE, not a library helper with a
    caller left to guard it, and the stage 5 tests below
    (`require_anchor=False`) prove the guard now DOES flag this exact shape
    once it is scanned as a route handler. This test's assertion is
    unchanged and still correct for the general case; it is deliberately not
    the whole story any more."""
    src = textwrap.dedent(
        """
        def f(scope, filename):
            item_path = scope.resolve("00-inbox", "active") / filename
            return item_path
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_no_lexical.py"
    )
    assert offenders == []


def test_c2_no_app_function_calls_resolve_before_its_lexical_symlink_check():
    """The real scan, anchored to `_REPO_ROOT` (never the process cwd — see
    `_REPO_ROOT`'s own comment). Must be zero after the C2 fix at all three
    sites; the three mutation tests below prove each one individually red."""
    offenders = []
    sources = list((_REPO_ROOT / "app").rglob("*.py"))
    assert len(sources) > 10, f"guard scanned too few files: {len(sources)}"
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(_resolve_before_lexical_symlink_offenders(tree, str(path)))
    assert offenders == []


# Each string below is the EXACT pre-fix shape at the named site (captured
# from the diff this fix batch applied), reduced to a standalone function so
# the mutation proof does not depend on rewriting real files on disk. Each
# is asserted red individually — "each stage individually red under
# mutation" — rather than only proving the batch of three together.

_C2_MUTATIONS = {
    "destinations.py:172 (pre-fix)": textwrap.dedent(
        """
        def resolve_classification_destination(scope, item_path, *, module, sub):
            destination_lexical = scope.root / entity / module / "active" / leaf
            destination = scope.resolve(module, "active", leaf)
            if (
                destination.parent != active_dir
                or destination_lexical.is_symlink()
                or destination.is_symlink()
            ):
                raise RedirectedDestination("destination is not canonical")
        """
    ),
    "outbox.py:127-129 (pre-fix)": textwrap.dedent(
        """
        def _require_outbox_path(scope, proposal_path=None, *, create_directory=False):
            lexical_outbox = scope.root / scope.current_entity() / "outbox"
            resolved_outbox = scope.resolve("outbox")
            if lexical_outbox.is_symlink() or resolved_outbox != lexical_outbox:
                raise RedirectedPathError("outbox directory is redirected")
        """
    ),
    "registry.py:289-295 (pre-fix)": textwrap.dedent(
        """
        def _delete_proposal_path(scope, proposal_id):
            entity_root = scope.resolve()
            bound_outbox = entity_root / "outbox"
            resolved_outbox = scope.resolve("outbox")
            if resolved_outbox != bound_outbox:
                raise RedirectedPathError("outbox redirects outside the bound outbox")
        """
    ),
}


@pytest.mark.parametrize("label, src", list(_C2_MUTATIONS.items()))
def test_c2_each_original_site_is_individually_red_under_mutation(label, src):
    offenders = _resolve_before_lexical_symlink_offenders(ast.parse(src), label)
    assert offenders, f"guard did not catch the pre-fix shape at {label}"


def test_c2_guard_would_have_caught_all_five_sites_on_the_axis():
    """Method step 4: would the axis invariant have caught all five original
    sites (the two must-fix-6 fixed before this review, plus the three C2
    fixed here)? The two already-fixed sites are proven NOT flagged in their
    real, current, fixed form (a regression here would mean the invariant
    itself broke a working site); the three C2 sites are proven flagged in
    their pre-fix form by the parametrized test above. Together: yes, all
    five are on one axis this single invariant covers."""
    from app import destinations, inbox

    for path in (
        pathlib.Path(destinations.__file__),
        pathlib.Path(inbox.__file__),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            o
            for o in _resolve_before_lexical_symlink_offenders(tree, str(path))
            if "_require_real_directory" in o
        ]
        assert offenders == [], (
            f"the already-fixed _require_real_directory in {path} "
            f"regressed: {offenders}"
        )


# --- Stage 5 (Task 8 corrective): the direct ROUTE-LEVEL pattern -----------
#
# The stage 4 test above documents, on purpose, that a function with NO
# anchor path at all is invisible to the general scan — that was the
# residual: `app/main.py`'s `propose()` is exactly this shape, and it is a
# registered ROUTE, not a library helper. A library helper may rely on its
# caller (or a callee it delegates to) to have already validated the path; a
# route handler is the request boundary, so there is no caller left to have
# done it instead. `require_anchor=False` is that missing AXIS on the same
# scanning function used everywhere else in this file — not a name check for
# `propose`, which appears nowhere below.


def _route_level_resolve_offenders(app) -> list[str]:
    """Every registered console route handler's OWN source, scanned with
    `require_anchor=False`: a bare `scope.resolve(<parts>)` with no anchor
    built and no symlink guard anywhere in the handler is always an
    offender here, never merely "out of scope for the guard" the way stage
    4 treats it for an arbitrary helper. Routes are discovered from the live
    `app.routes` registration (`_registered_console_endpoints`, already used
    by the route-coverage invariant above) — not by name, not by file path —
    so a future route with this shape is caught the same way `propose` was,
    regardless of what it is called."""
    import inspect

    offenders: list[str] = []
    for endpoint in _registered_console_endpoints(app):
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            continue
        tree = ast.parse(textwrap.dedent(source))
        label = getattr(endpoint, "__qualname__", None) or repr(endpoint)
        offenders.extend(
            _resolve_before_lexical_symlink_offenders(
                tree, label, require_anchor=False
            )
        )
    return offenders


def test_c2_route_level_positive_control_bare_unguarded_resolve_is_flagged():
    """Stage 5 positive control, driven through the real scanning function:
    the exact pre-fix `propose()` shape — a bare `scope.resolve(<parts>)`
    with no anchor built anywhere in the function — IS flagged once scanned
    with `require_anchor=False`, unlike the stage 4 default."""
    src = textwrap.dedent(
        """
        def propose(scope, filename):
            item_path = scope.resolve("00-inbox", "active") / filename
            return item_path
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_route_no_anchor.py", require_anchor=False
    )
    assert offenders, "stage 5 failed to flag a bare unguarded route-level resolve()"


def test_c2_route_level_negative_control_guarded_resolve_is_not_flagged():
    """Stage 5 negative control: the corrected shape — the real fix's own
    pattern, a `scope.root`-derived lexical anchor checked for `.is_symlink()`
    BEFORE `scope.resolve()` — is not flagged even under the stricter
    `require_anchor=False` scan. Proves stage 5 does not regress the
    already-correct pattern; it only removes the stage-4 exemption for a
    function that never built an anchor in the first place."""
    src = textwrap.dedent(
        """
        def propose(scope, filename):
            inbox_active_lexical = (
                scope.root / scope.current_entity() / "00-inbox" / "active"
            )
            if inbox_active_lexical.is_symlink():
                raise RedirectedPathError("bad")
            item_path = scope.resolve("00-inbox", "active") / filename
            return item_path
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_route_guarded.py", require_anchor=False
    )
    assert offenders == []


def test_c2_route_level_negative_control_bare_zero_arg_resolve_still_safe():
    """Stage 5 negative control: a bare zero-arg `scope.resolve()` — which
    can never raise `OutOfScopeError` for a redirected SUBPATH, since it has
    no subpath — must still never be the offending call, mirroring stage 3b
    under the stricter scan. Only calls with `argcount >= 1` are ever
    appended to `offenders`, in both scan modes."""
    src = textwrap.dedent(
        """
        def f(scope):
            entity_root = scope.resolve()
            return entity_root
        """
    )
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), "synthetic_route_bare_zero_arg.py", require_anchor=False
    )
    assert offenders == []


# The exact pre-fix shape at `app/main.py`'s `propose()` (captured from the
# Task 8 corrective diff), reduced to a standalone function so the mutation
# proof does not depend on rewriting the real file on disk — the same style
# as `_C2_MUTATIONS` above, on the same scanning function, with the one
# parameter that makes route-level scanning stricter.
_C2_ROUTE_LEVEL_MUTATIONS = {
    "main.py:469 (pre-fix, Task 8 corrective)": textwrap.dedent(
        """
        def propose(scope, filename):
            item_path = scope.resolve("00-inbox", "active") / filename
            return item_path
        """
    ),
}


@pytest.mark.parametrize("label, src", list(_C2_ROUTE_LEVEL_MUTATIONS.items()))
def test_c2_route_level_mutation_is_individually_red(label, src):
    offenders = _resolve_before_lexical_symlink_offenders(
        ast.parse(src), label, require_anchor=False
    )
    assert offenders, f"stage 5 did not catch the pre-fix shape at {label}"


def test_c2_no_registered_route_calls_resolve_without_any_symlink_guard(
    tmp_path, monkeypatch
):
    """The real stage-5 scan: every route FastAPI actually registers, loaded
    from the live app rather than read off a file list. Must be zero after
    the Task 8 corrective fixes `propose()` — proven red pre-fix by
    reintroducing the exact mutation against the real app in the
    accompanying report, since mutating the real `app/main.py` on disk here
    would leave the suite in a failing state for every other test in this
    session."""
    main = _load_console_app(tmp_path, monkeypatch)
    offenders = _route_level_resolve_offenders(main.app)
    assert offenders == [], f"stage 5 found an unguarded route-level resolve(): {offenders}"


def test_the_quarantine_status_filter_hides_only_quarantine_records(tmp_path):
    """S7 Amendment 1: verification may exclude exactly
    `<entity>/outbox/.consumed/*.yaml` and nothing broader. A filter that
    hid any path containing `.consumed` would conceal real regressions
    elsewhere in the vault."""
    from tests.conftest import git_status_apart_from_quarantine, git_vault

    vault = git_vault(tmp_path, {"tracked.md": "tracked\n"})
    hidden = vault / "demo/outbox/.consumed"
    hidden.mkdir(parents=True)
    (hidden / "20260101T000000-aa.yaml").write_text("record\n", encoding="utf-8")

    # None of these are quarantine records, and none may be hidden.
    decoys = {
        "demo/outbox/.consumed/notes.md": "wrong suffix",
        "demo/outbox/.consumed/nested/deep.yaml": "nested below quarantine",
        "demo/.consumed/stray.yaml": "not under an outbox",
        "elsewhere/.consumed-ish/thing.yaml": "lookalike directory",
        "top-level.consumed.yaml": "lookalike filename",
    }
    for relative in decoys:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("decoy\n", encoding="utf-8")

    remaining = git_status_apart_from_quarantine(vault).decode()

    assert "20260101T000000-aa.yaml" not in remaining, "a real record leaked through"
    for relative, why in decoys.items():
        assert relative in remaining, f"{why} was wrongly hidden: {relative}"


def _executable_source(function) -> str:
    """A function's code with comments and docstrings removed.

    Matching raw source would let a *comment* — for instance one explaining
    that a reader was deliberately removed — fail a "must not call" check,
    and would equally let a commented-out call pass one. Only executable
    code is evidence.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
    return ast.unparse(tree)


# --- S7 Task 6: structural proof of the bound-review surface -----------------
#
# These cover only S7's changed surface. They are deliberately NOT the
# separately sequenced global route-declaration audit.

_S7_ACTIONS = ("approve", "reject", "execute_delete")
_S7_ROUTES = ("outbox_approve", "outbox_reject", "registry_delete_execute")
_S7_ACTION_TEMPLATES = (
    "templates/blocks/outbox_card.html",
    "templates/blocks/delete_impact.html",
)


def _main_module():
    import app.main as main

    return main


def test_every_reviewed_service_requires_the_fingerprint():
    import inspect

    import app.outbox as outbox
    import app.registry as registry

    for name in _S7_ACTIONS:
        service = getattr(outbox, name, None) or getattr(registry, name)
        parameters = inspect.signature(service).parameters
        assert list(parameters)[-1] == "review_sha256", name
        assert parameters["review_sha256"].default is inspect.Parameter.empty, name


def test_every_reviewed_route_requires_and_passes_the_fingerprint(
    tmp_path, monkeypatch
):
    import ast
    import inspect

    main = _load_console_main(tmp_path, monkeypatch)
    for name in _S7_ROUTES:
        route = getattr(main, name)
        parameters = inspect.signature(route).parameters
        assert "review_sha256" in parameters, name
        source = _executable_source(route)
        tree = ast.parse(source)
        # A parameter is an `arg` node, not a `Name`; a `Name` is a *use*.
        # So: declared once, and used at least once to hand it onward.
        declared = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.arg) and node.arg == "review_sha256"
        ]
        used = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "review_sha256"
        ]
        assert declared, f"{name} does not declare review_sha256"
        assert used, f"{name} never passes review_sha256 onward"
        assert "get_proposal_review(" not in source, name
        assert "get_delete_review(" not in source, name


def test_no_reviewed_action_route_reads_through_a_value_only_reader(
    tmp_path, monkeypatch
):
    import inspect

    main = _load_console_main(tmp_path, monkeypatch)
    forbidden = ("get_proposal(", "get_delete_proposal(", "load_proposals(")
    for name in _S7_ROUTES:
        source = _executable_source(getattr(main, name))
        helper = f"_{name}_response"
        if hasattr(main, helper):
            source += _executable_source(getattr(main, helper))
        for reader in forbidden:
            assert reader not in source, f"{name} reads through {reader}"


def test_delete_success_copy_comes_from_the_bound_execution(tmp_path, monkeypatch):
    import inspect

    main = _load_console_main(tmp_path, monkeypatch)
    source = _executable_source(main.registry_delete_execute)
    assert "execute_delete(scope, id, review_sha256)" in source
    assert "get_delete_proposal" not in source
    # The returned proposal is what the success fragment renders.
    success = source.split("delete_success.html", 1)[1]
    assert "prop.kind" in success and "prop.slug" in success


def test_every_action_template_carries_the_fingerprint_through_tojson():
    import re as _re

    for relative in _S7_ACTION_TEMPLATES:
        source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        values = _re.findall(r"hx-vals='([^']*)'", source)
        assert values, f"{relative} renders no hx-vals"
        for value in values:
            assert value.strip().startswith("{{"), relative
            assert "| tojson" in value, relative
        assert "review_sha256" in source, relative


def test_no_row_without_controls_can_carry_a_fingerprint():
    """The projection's own invariant, restated where the templates rely on
    it: controls and fingerprint are issued together or not at all."""
    from app.outbox import OutboxRow

    with pytest.raises(ValueError):
        OutboxRow(None, None, None, can_approve=False, can_reject=False,
                  review_sha256="a" * 64)
    with pytest.raises(ValueError):
        OutboxRow(None, None, None, can_approve=True, can_reject=False,
                  review_sha256=None)


def test_every_review_fragment_route_declares_its_family(tmp_path, monkeypatch):
    main = _load_console_main(tmp_path, monkeypatch)
    for name in ("outbox_review_fragment", "registry_delete_review_fragment"):
        declaration = getattr(main, name).__console_route__
        assert declaration.surface == "fragment-only", name
        assert declaration.catches, name
        assert Exception not in declaration.catches, name
        assert BaseException not in declaration.catches, name


def test_every_review_fragment_route_is_read_only(tmp_path, monkeypatch):
    import inspect

    main = _load_console_main(tmp_path, monkeypatch)
    mutating = (
        "approve(", "reject(", "execute_delete(", "propose_classification(",
        "propose_delete(", "unlink", "write_text", "write_bytes", "mkdir",
        "execute_transaction(", "quarantine_path_if_unchanged(",
    )
    for name in ("outbox_review_fragment", "registry_delete_review_fragment"):
        source = _executable_source(getattr(main, name))
        for call in mutating:
            assert call not in source, f"{name} reaches {call}"


def _load_console_main(tmp_path, monkeypatch):
    import importlib

    from tests.conftest import scaffold_modules, write_vault

    write_vault(
        tmp_path,
        '\nversion: "1.0"\nentities:\n  alpha: { label: Alpha, flags: [] }\n',
    )
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    return importlib.reload(main)
