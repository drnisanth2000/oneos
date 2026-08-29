"""Route and reader metadata declarations. Pure metadata — importable by
services without touching the taxonomy; it owns no copy and no route list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

SURFACES = frozenset({"page", "fragment-only"})
#: `admin-record` is an administrative structured record — YAML or JSON —
#: whose existing domain exception stays an administrative refusal. It is
#: the record-shaped parallel of `admin-db`.
READER_CATEGORIES = frozenset(
    {"registry", "proposal", "front-matter", "admin-db", "admin-record"}
)

_Decorated = TypeVar("_Decorated", bound=Callable)


def _require_exception_class(value: object, *, field: str) -> None:
    if value in (Exception, BaseException):
        raise ValueError(f"{field} may not contain a catch-all")
    if not (isinstance(value, type) and issubclass(value, BaseException)):
        raise ValueError(f"{field} must contain exception classes")


@dataclass(frozen=True)
class DeliberateUnknown:
    exception: type[BaseException]
    reason: str

    def __post_init__(self) -> None:
        _require_exception_class(self.exception, field="deliberate_unknown")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("a deliberate unknown requires a non-empty reason")


@dataclass(frozen=True)
class FailureContract:
    raises: tuple[type[BaseException], ...]
    calls: tuple[Callable[..., object], ...]
    deliberate_unknown: tuple[DeliberateUnknown, ...]

    def __post_init__(self) -> None:
        declared_raises = tuple(self.raises)
        declared_calls = tuple(self.calls)
        declared_unknown = tuple(self.deliberate_unknown)
        object.__setattr__(self, "raises", declared_raises)
        object.__setattr__(self, "calls", declared_calls)
        object.__setattr__(self, "deliberate_unknown", declared_unknown)

        for exception in declared_raises:
            _require_exception_class(exception, field="raises")
        if len(set(declared_raises)) != len(declared_raises):
            raise ValueError("raises may not contain duplicate classes")

        for disposition in declared_unknown:
            if not isinstance(disposition, DeliberateUnknown):
                raise ValueError(
                    "deliberate_unknown must contain DeliberateUnknown values"
                )
        unknown_classes = tuple(item.exception for item in declared_unknown)
        if len(set(unknown_classes)) != len(unknown_classes):
            raise ValueError("deliberate_unknown may not contain duplicate classes")
        if set(declared_raises) & set(unknown_classes):
            raise ValueError("an exception cannot be both raised and deliberate unknown")

        for target in declared_calls:
            if not callable(target) or not isinstance(
                getattr(target, "__failure_contract__", None), FailureContract
            ):
                raise ValueError("calls targets must carry a FailureContract")


def failure_contract(
    *,
    raises: tuple[type[BaseException], ...] = (),
    calls: tuple[Callable[..., object], ...] = (),
    deliberate_unknown: tuple[DeliberateUnknown, ...] = (),
) -> Callable[[_Decorated], _Decorated]:
    """Attach immutable failure metadata without wrapping the callable."""
    contract = FailureContract(tuple(raises), tuple(calls), tuple(deliberate_unknown))

    def decorate(boundary: _Decorated) -> _Decorated:
        if not callable(boundary):
            raise ValueError("a failure contract may decorate only a callable")
        boundary.__failure_contract__ = contract
        return boundary

    return decorate


@dataclass(frozen=True)
class ConsoleRoute:
    catches: tuple[type[BaseException], ...]
    surface: str
    services: tuple[Callable[..., object], ...] = ()


def console_route(
    *,
    catches: tuple[type[BaseException], ...],
    surface: str,
    services: tuple[Callable[..., object], ...] = (),
) -> Callable[[_Decorated], _Decorated]:
    """Declare a route's catch family and surface on its endpoint.

    A catch-all is refused outright: laundering programmer errors into 200
    fragments is the opposite of S6's purpose (design §5).
    """
    declared = tuple(catches)
    for cls in declared:
        if cls in (Exception, BaseException):
            raise ValueError("a console route may not declare a catch-all")
        if not (isinstance(cls, type) and issubclass(cls, BaseException)):
            raise ValueError("catches must contain exception classes")
    if surface not in SURFACES:
        raise ValueError("surface is not a permitted value")
    declared_services = tuple(services)
    for service in declared_services:
        if not callable(service) or not isinstance(
            getattr(service, "__failure_contract__", None), FailureContract
        ):
            raise ValueError("route services must carry a FailureContract")

    def decorate(endpoint: _Decorated) -> _Decorated:
        endpoint.__console_route__ = ConsoleRoute(
            declared, surface, declared_services
        )
        return endpoint

    return decorate


def structured_reader(*, category: str) -> Callable[[_Decorated], _Decorated]:
    """Declare a structured reader's failure category at its definition."""
    if category not in READER_CATEGORIES:
        raise ValueError("category is not a permitted value")

    def decorate(reader: _Decorated) -> _Decorated:
        reader.__structured_reader__ = category
        return reader

    return decorate
