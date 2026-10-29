"""Read-only master view across every entity.

Cards come from each entity's shared reader (``board.entity_cards``) and are
sorted into four stages: Draft, Waiting, To review, Done. A card whose status
is none of those is listed apart, never dropped. This module only reads; it
never writes to the vault.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from .action_receipts import (
    InvalidActionReceipt,
    ReceiptStoreIntegrityError,
    ReceiptStoreUnavailable,
)
from .board import BoardCard, BoardLink, _sort_cards, entity_cards
from .console_routing import failure_contract
from .entities import EntityManifestError, EntitySelectionError
from .outbox import OutboxError
from .scope import CrossScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError, Vault

STAGES = ("draft", "waiting", "review", "done")
_LABELS = {"draft": "Draft", "waiting": "Waiting", "review": "To review", "done": "Done"}
_FILED_STAGE = {"draft": "draft", "waiting": "waiting", "done": "done", "active": "done"}
_SORTS = (("updated", "Updated"), ("title", "Title"))
_TONES = 6
_DONE_LIMIT = 10

#: Per-entity failures the page reports and survives. The same classes as the
#: Board route catches, plus a redirected directory.
_ENTITY_FAILURES = (
    OutboxError,
    CrossScopeError,
    DestinationRegistryError,
    EntityManifestError,
    EntitySelectionError,
    InvalidActionReceipt,
    ReceiptStoreIntegrityError,
    ReceiptStoreUnavailable,
    RedirectedPathError,
)


@dataclass(frozen=True)
class MasterCard:
    card: BoardCard
    entity: str
    entity_label: str
    tone: int


@dataclass(frozen=True)
class MasterColumn:
    key: str
    label: str
    cards: tuple[MasterCard, ...]
    total: int


@dataclass(frozen=True)
class EntityChip:
    slug: str
    label: str
    tone: int
    active: bool
    href: str


@dataclass(frozen=True)
class EntityFailure:
    entity_label: str
    error: BaseException


@dataclass(frozen=True)
class MasterPage:
    greeting: str
    date_label: str
    time_label: str
    chips: tuple[EntityChip, ...]
    sort: str
    sort_links: tuple[BoardLink, ...]
    columns: tuple[MasterColumn, ...]
    other: tuple[MasterCard, ...]
    failures: tuple[EntityFailure, ...]
    done_all: bool
    done_all_href: str


def _greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Good morning"
    if now.hour < 17:
        return "Good afternoon"
    return "Good evening"


def _href(entities: tuple[str, ...], sort: str, done_all: bool) -> str:
    pairs = [("entity", slug) for slug in entities]
    pairs.append(("sort", sort))
    if done_all:
        pairs.append(("done", "all"))
    return "/?" + urlencode(pairs)


def _ordered(masters: list[MasterCard], sort: str) -> list[MasterCard]:
    by_card = {id(master.card): master for master in masters}
    return [by_card[id(card)] for card in _sort_cards([m.card for m in masters], sort)]


@failure_contract(raises=(DestinationRegistryError,), calls=(entity_cards,))
def load_master(
    vault: Vault,
    scope_for: Callable[[str], Scope],
    *,
    entities: tuple[str, ...],
    sort: str,
    done_all: bool,
    now: datetime,
) -> MasterPage:
    """Read every selected entity's cards into the four stages. Reads only."""
    selected_sort = sort if sort in dict(_SORTS) else "updated"
    bundles = vault.bundles()
    on_disk = [(index, bundle) for index, bundle in enumerate(bundles) if bundle.on_disk]
    chosen = tuple(bundle.slug for _, bundle in on_disk if bundle.slug in entities)
    reading = [(i, b) for i, b in on_disk if not chosen or b.slug in chosen]

    stages: dict[str, list[MasterCard]] = {key: [] for key in STAGES}
    other: list[MasterCard] = []
    failures: list[EntityFailure] = []
    for index, bundle in reading:
        tone = index % _TONES + 1
        try:
            cards = entity_cards(scope_for(bundle.slug), vault)
        except _ENTITY_FAILURES as exc:
            failures.append(EntityFailure(bundle.label, exc))
            continue

        def wrap(card: BoardCard) -> MasterCard:
            return MasterCard(card, bundle.slug, bundle.label, tone)

        proposed_paths = {card.relative_path for card in cards.proposed}
        for card in cards.proposed:
            stages["review"].append(wrap(card))
        for card in cards.inbox:
            if card.relative_path not in proposed_paths:
                stages["review"].append(wrap(card))
        for card in cards.filed:
            stage = _FILED_STAGE.get(card.status.strip().lower())
            (stages[stage] if stage else other).append(wrap(card))

    columns = []
    for key in STAGES:
        masters = stages[key]
        if key == "done":
            masters = _ordered(masters, "updated")
            shown = masters if done_all else masters[:_DONE_LIMIT]
        else:
            shown = _ordered(masters, selected_sort)
        columns.append(MasterColumn(key, _LABELS[key], tuple(shown), len(masters)))

    slugs = tuple(bundle.slug for _, bundle in on_disk)
    chips = tuple(
        EntityChip(
            slug=bundle.slug,
            label=bundle.label,
            tone=index % _TONES + 1,
            active=bundle.slug in chosen,
            # Each chip links to the selection with that entity toggled.
            href=_href(
                tuple(s for s in slugs if (s in chosen) != (s == bundle.slug)),
                selected_sort,
                done_all,
            ),
        )
        for index, bundle in on_disk
    )
    sort_links = tuple(
        BoardLink(label, _href(chosen, key, done_all), key == selected_sort)
        for key, label in _SORTS
    )
    return MasterPage(
        greeting=_greeting(now),
        date_label=f"{now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}",
        time_label=now.strftime("%H:%M"),
        chips=chips,
        sort=selected_sort,
        sort_links=sort_links,
        columns=tuple(columns),
        other=tuple(_ordered(other, selected_sort)),
        failures=tuple(failures),
        done_all=done_all,
        done_all_href=_href(chosen, selected_sort, True),
    )

