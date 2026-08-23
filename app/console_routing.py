"""Route and reader metadata declarations. Pure metadata — importable by
services without touching the taxonomy; it owns no copy and no route list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

SURFACES = frozenset({"page", "fragment-only"})
READER_CATEGORIES = frozenset({"registry", "proposal", "front-matter", "admin-db"})

_Decorated = TypeVar("_Decorated", bound=Callable)


@dataclass(frozen=True)
class ConsoleRoute:
    catches: tuple[type[BaseException], ...]
    surface: str


def console_route(
    *, catches: tuple[type[BaseException], ...], surface: str
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

    def decorate(endpoint: _Decorated) -> _Decorated:
        endpoint.__console_route__ = ConsoleRoute(declared, surface)
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
