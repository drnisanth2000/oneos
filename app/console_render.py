"""Fragment selection and status for described Console errors.

Owns no copy and no route list. Only the presentation composition root
(`app/main.py`) and this module may import the taxonomy.
"""
from __future__ import annotations

from typing import Callable

from .console_errors import ConsoleError
from .console_routing import ConsoleRoute


def is_fragment(request, endpoint: Callable) -> bool:
    """Route shape first, then HX-Request (design §5).

    A route with no full-page template always uses the fragment renderer; a
    page route fragments only when the request came from HTMX.
    """
    metadata: ConsoleRoute | None = getattr(endpoint, "__console_route__", None)
    if metadata is not None and metadata.surface == "fragment-only":
        return True
    return request.headers.get("HX-Request") == "true"


def status_for(error: ConsoleError, fragment: bool) -> int:
    """Fragment status follows severity, not code; a full page always
    returns the code's page status (design §5)."""
    if fragment and error.severity == "refusal":
        return 200
    return error.page_status
