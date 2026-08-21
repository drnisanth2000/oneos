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
