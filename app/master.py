"""Read-only master view across every entity.

Cards come from each entity's shared reader (``board.entity_cards``) and are
sorted into four stages: Draft, Waiting, To review, Done. A card whose status
is none of those is listed apart, never dropped. This module only reads; it
never writes to the vault.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode

from .action_receipts import (
    InvalidActionReceipt,
    ReceiptStoreIntegrityError,
    ReceiptStoreUnavailable,
)
from .board import BoardCard, BoardLink, NoteUnreadable, _sort_cards, entity_cards
from .config import VaultRootUnavailable
from .console_routing import failure_contract
from .entities import EntityManifestError, EntitySelectionError
from .outbox import OutboxError
from .reminders import Reminder
from .scope import CrossScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError, Vault

STAGES = ("draft", "waiting", "review", "done")
_LABELS = {"draft": "Draft", "waiting": "Waiting", "review": "To review", "done": "Done"}
_FILED_STAGE = {"draft": "draft", "waiting": "waiting", "done": "done", "active": "done"}
OTHER_STAGE = "other"
_SORTS = (("updated", "Updated"), ("title", "Title"))
_TONES = 6
_DONE_LIMIT = 10
_SOON = timedelta(hours=24)
_LEVEL_RANK = {"overdue": 0, "soon": 1, "later": 2}
#: Only these columns put cards with a reminder first (under the "updated" sort).
_REMINDER_FIRST = ("waiting", "review")

#: Per-entity failures the page reports and survives: the same classes as the
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
    # `entity_cards` raises it for an unreadable inbox or filed note.
    NoteUnreadable,
)

#: A raw OSError or UnicodeDecodeError that still escapes `entity_cards` (for
#: example from listing a directory) is recorded
#: as `NoteUnreadable` ("a file could not be read"), so the page does not say
#: E-UNKNOWN. This is local to this page; no other route's message changes.
_NOTE_READ_FAILURES = (UnicodeDecodeError, OSError)


def stage_of(card: BoardCard) -> str:
    """The STAGES key a card belongs to, or ``"other"`` for an unlisted status."""
    if card.lane in ("inbox", "proposed"):
        return "review"
    return _FILED_STAGE.get(card.status.strip().lower(), OTHER_STAGE)


def stage_label(stage: str) -> str:
    return _LABELS.get(stage, "Another status")


def entity_tone(index: int) -> int:
    """The chip colour (1..6) for the entity at `index` in registry order."""
    return index % _TONES + 1


@dataclass(frozen=True)
class MasterCard:
    card: BoardCard
    entity: str
    entity_label: str
    tone: int
    reminder: Reminder | None = None


@dataclass(frozen=True)
class AttentionCard:
    """One entry of the "Needs attention" strip.

    ``card`` is None when the reminder's card is gone (moved or removed) or its
    entity could not be read; ``unreadable`` tells those two apart.
    """

    reminder: Reminder
    entity_label: str
    tone: int
    level: str  # "overdue" | "soon" | "later"
    card: MasterCard | None
    unreadable: bool


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
    done_recent_href: str
    attention: tuple[AttentionCard, ...] = ()
    reminders_available: bool = True


def due_label(due: datetime) -> str:
    """The stored wall time, e.g. ``Wed 4 Nov 11:00``."""
    return due.strftime("%a %-d %b %H:%M")


def _level(due: datetime, now: datetime) -> str:
    if due < now:
        return "overdue"
    if due < now + _SOON:
        return "soon"
    return "later"


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


def _attention(
    reminders: Mapping[str, Reminder],
    chosen: tuple[str, ...],
    stages: dict[str, list[MasterCard]],
    other: list[MasterCard],
    failed_slugs: set[str],
    bundles: list,
    now: datetime,
) -> tuple[AttentionCard, ...]:
    """The "Needs attention" strip: every reminder in the entity filter whose
    card is not Done, overdue first, then soon, then later, soonest first."""
    cards = {
        (m.entity, m.card.relative_path): m
        for masters in (*stages.values(), other)
        for m in masters
    }
    done = {
        (m.entity, m.card.relative_path) for m in stages["done"]
    }
    registry = {bundle.slug: (index, bundle) for index, bundle in enumerate(bundles)}
    entries = []
    for reminder in reminders.values():
        if chosen and reminder.entity not in chosen:
            continue
        ident = (reminder.entity, reminder.key)
        if ident in done:
            continue  # Done resolves a reminder; its row is kept untouched.
        known = registry.get(reminder.entity)
        entries.append(
            AttentionCard(
                reminder=reminder,
                entity_label=known[1].label if known else reminder.entity,
                tone=entity_tone(known[0]) if known else 1,
                level=_level(reminder.due_at, now),
                card=cards.get(ident),
                unreadable=reminder.entity in failed_slugs,
            )
        )
    entries.sort(key=lambda e: (_LEVEL_RANK[e.level], e.reminder.due_at, e.reminder.key))
    return tuple(entries)


# Per-entity read failures are reported on the page, not raised, so
# `entity_cards` is a contained call; only the bundle listing (via
# `vault.bundles()`) and the vault root can fail the whole page.
@failure_contract(
    raises=(DestinationRegistryError, EntityManifestError, VaultRootUnavailable)
)
def load_master(
    vault: Vault,
    scope_for: Callable[[str], Scope],
    *,
    entities: tuple[str, ...],
    sort: str,
    done_all: bool,
    now: datetime,
    reminders: Mapping[str, Reminder] | None,
) -> MasterPage:
    """Read every selected entity's cards into the four stages. Reads only.

    ``now`` must be timezone-aware. ``reminders`` maps a card's relative path to
    its reminder; ``None`` means the store is unavailable.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    available = reminders is not None
    reminders = reminders or {}
    selected_sort = sort if sort in dict(_SORTS) else "updated"
    bundles = vault.bundles()
    on_disk = [(index, bundle) for index, bundle in enumerate(bundles) if bundle.on_disk]
    chosen = tuple(bundle.slug for _, bundle in on_disk if bundle.slug in entities)
    # No selected slug is on disk (all unknown, or the only one went away): show every entity.
    reading = [(i, b) for i, b in on_disk if not chosen or b.slug in chosen]

    stages: dict[str, list[MasterCard]] = {key: [] for key in STAGES}
    other: list[MasterCard] = []
    failures: list[EntityFailure] = []
    failed_slugs: set[str] = set()
    for index, bundle in reading:
        tone = entity_tone(index)
        try:
            cards = entity_cards(scope_for(bundle.slug), vault)
        except _ENTITY_FAILURES as exc:
            failures.append(EntityFailure(bundle.label, exc))
            failed_slugs.add(bundle.slug)
            continue
        except _NOTE_READ_FAILURES as exc:  # any file read in this entity
            unreadable = NoteUnreadable("note could not be read")
            unreadable.__cause__ = exc
            failures.append(EntityFailure(bundle.label, unreadable))
            failed_slugs.add(bundle.slug)
            continue

        def wrap(card: BoardCard) -> MasterCard:
            reminder = reminders.get(card.relative_path)
            if reminder is not None and reminder.entity != bundle.slug:
                reminder = None
            return MasterCard(card, bundle.slug, bundle.label, tone, reminder)

        proposed_paths = {card.relative_path for card in cards.proposed}
        for card in cards.proposed:
            stages["review"].append(wrap(card))
        for card in cards.inbox:
            if card.relative_path not in proposed_paths:
                stages["review"].append(wrap(card))
        for card in cards.filed:
            stage = stage_of(card)
            (other if stage == OTHER_STAGE else stages[stage]).append(wrap(card))

    columns = []
    for key in STAGES:
        masters = stages[key]
        if key == "done":
            masters = _ordered(masters, "updated")
            shown = masters if done_all else masters[:_DONE_LIMIT]
        else:
            shown = _ordered(masters, selected_sort)
            if key in _REMINDER_FIRST and selected_sort == "updated":
                # Cards with a reminder first, soonest first; the rest keep their order.
                due = sorted(
                    (m for m in shown if m.reminder),
                    key=lambda m: (m.reminder.due_at, m.card.relative_path),
                )
                shown = due + [m for m in shown if not m.reminder]
        columns.append(MasterColumn(key, _LABELS[key], tuple(shown), len(masters)))

    slugs = tuple(bundle.slug for _, bundle in on_disk)
    chips = tuple(
        EntityChip(
            slug=bundle.slug,
            label=bundle.label,
            tone=entity_tone(index),
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
    attention = _attention(
        reminders, chosen, stages, other, failed_slugs, bundles, now
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
        done_recent_href=_href(chosen, selected_sort, False),
        attention=attention,
        reminders_available=available,
    )

