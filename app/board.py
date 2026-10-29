"""Read-only board over one entity.

Cards come from the inbox, pending classification proposals, and Markdown
already in a module ``active/`` directory. The page draws those cards as a
list, board, gallery, calendar, planner, or life map. The only write this
module performs is quick capture, and that calls the shared ingest path.
"""
from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .console_routing import failure_contract, structured_reader
from .inbox import InboxItem, read_inbox, split_front_matter
from .inbox import _require_real_directory as _require_real_lifecycle_directory
from .inbox import _require_real_receipt as _require_real_entry
from .ingest.base import IngestError, IngestResult, commit_inbox_item
from .outbox import Proposal, project_outbox
from .scope import CrossScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError, Vault

_INBOX_MODULE = "00-inbox"
_VIEWS = frozenset({"board", "list", "gallery", "calendar", "planner", "map"})
_GROUPS = frozenset({"review", "status", "sub", "module"})
_SORTS = frozenset({"updated", "title"})
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_COUNT = re.compile(r"^count\(related\)$")
_COUNT_WHERE = re.compile(r'^count\(related where status == "([^"]*)"\)$')
_IF = re.compile(r'^if\(status == "([^"]*)", "([^"]*)", "([^"]*)"\)$')
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_BULLET = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+(\S.*)$")


@dataclass(frozen=True)
class BoardCard:
    entity: str
    module: str
    block: str
    title: str
    status: str
    sub: str | None
    product: str | None
    member: str | None
    updated: str
    due: str | None
    cadence: str | None
    formula: str | None
    formula_result: str | None
    progress: int | None
    relative_path: str
    lane: str
    filename: str | None = None
    proposal_id: str | None = None
    draggable: bool = False


@dataclass(frozen=True)
class BoardColumn:
    key: str
    label: str
    module: str | None
    sub: str | None
    missing: bool
    droppable: bool
    cards: tuple[BoardCard, ...]


@dataclass(frozen=True)
class BoardLink:
    label: str
    href: str
    active: bool
    count: int | None = None


@dataclass(frozen=True)
class OutlineNode:
    text: str
    children: tuple["OutlineNode", ...]


@dataclass(frozen=True)
class CalendarDay:
    iso: str
    number: int
    in_month: bool
    cards: tuple[BoardCard, ...]


@dataclass(frozen=True)
class MapRow:
    module: str
    block: str
    count: int
    percent: int
    missing: bool


@dataclass(frozen=True)
class Attention:
    inbox: int
    proposed: int
    updated: int
    by_status: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class BoardPage:
    entity: str
    view: str
    group: str
    sort: str
    blocked: bool
    columns: tuple[BoardColumn, ...]
    cards: tuple[BoardCard, ...]
    undated: tuple[BoardCard, ...]
    unscheduled: tuple[BoardCard, ...]
    habits: tuple[BoardCard, ...]
    weeks: tuple[tuple[CalendarDay, ...], ...]
    days: tuple[CalendarDay, ...]
    map_rows: tuple[MapRow, ...]
    view_links: tuple[BoardLink, ...]
    group_links: tuple[BoardLink, ...]
    sort_links: tuple[BoardLink, ...]
    chips: tuple[BoardLink, ...]
    period_label: str
    previous_href: str
    next_href: str
    attention: Attention
    outline: tuple[OutlineNode, ...]
    outline_title: str
    note_open: bool
    clear_outline_href: str
    self_href: str


def _text(value: object) -> str | None:
    # YAML parses an unquoted date as a date. Front matter still means the
    # calendar day the operator wrote.
    if isinstance(value, datetime):
        value = value.date().isoformat()
    elif isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _status(value: object) -> str:
    return _text(value) or ""


def _progress(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > 100:
        return None
    return int(value)


def _day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    token = value.strip()[:10]
    if _DAY.fullmatch(token) is None:
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _choice(value: str, allowed: frozenset[str], default: str) -> str:
    return value if value in allowed else default


def _require_real_directory(scope: Scope, *parts: str) -> Path | None:
    return _require_real_lifecycle_directory(scope, *parts, label="module directory")


def _require_real_note(directory: Path, discovered: Path) -> Path:
    return _require_real_entry(directory, discovered, label="module note")


def _scalar_card(
    *,
    entity: str,
    module: str,
    block: str,
    title: str,
    fm: dict,
    relative_path: str,
    lane: str,
    filename: str | None,
    proposal_id: str | None,
    formula_result: str | None,
) -> BoardCard:
    updated = _text(fm.get("updated")) or ""
    due = _text(fm.get("due"))
    return BoardCard(
        entity=entity,
        module=module,
        block=block,
        title=title,
        status=_status(fm.get("status")),
        sub=_text(fm.get("sub")),
        product=_text(fm.get("product")),
        member=_text(fm.get("member")),
        updated=updated,
        due=due if _day(due) else None,
        cadence=_text(fm.get("cadence")),
        formula=_text(fm.get("formula")),
        formula_result=formula_result,
        progress=_progress(fm.get("progress")),
        relative_path=relative_path,
        lane=lane,
        filename=filename,
        proposal_id=proposal_id,
    )


def _evaluate(formula: str | None, status: str, related: list[BoardCard]) -> str | None:
    if formula is None:
        return None
    if _COUNT.fullmatch(formula):
        return str(len(related))
    matched = _COUNT_WHERE.fullmatch(formula)
    if matched:
        wanted = matched.group(1)
        return str(sum(1 for card in related if card.status == wanted))
    conditional = _IF.fullmatch(formula)
    if conditional:
        wanted, yes, no = conditional.groups()
        return yes if status == wanted else no
    return None


def _needs_related(formula: str | None) -> bool:
    return bool(formula) and (
        _COUNT.fullmatch(formula) is not None or _COUNT_WHERE.fullmatch(formula) is not None
    )


def _related(card: BoardCard, filed: list[BoardCard]) -> list[BoardCard]:
    if not card.product:
        return []
    return [
        other
        for other in filed
        if other.relative_path != card.relative_path and other.product == card.product
    ]


@structured_reader(category="front-matter")
def _read_filed(scope: Scope, vault: Vault) -> list[BoardCard]:
    entity = scope.current_entity()
    cards: list[BoardCard] = []
    for module in sorted(vault.active_modules_for(scope)):
        if module == _INBOX_MODULE:
            continue
        spec = vault.module_spec(module)
        if spec.get("lifecycle_pattern", True) is False:
            continue
        if _require_real_directory(scope, module) is None:
            continue
        directory = _require_real_directory(scope, module, "active")
        if directory is None:
            continue
        block = vault.block_of(module)
        for discovered in sorted(directory.glob("*.md")):
            path = _require_real_note(directory, discovered)
            fm, _body = split_front_matter(path.read_text(encoding="utf-8"))
            title = _text(fm.get("title")) or path.stem
            relative = scope.vault_relative(path)
            cards.append(
                _scalar_card(
                    entity=entity,
                    module=module,
                    block=block,
                    title=title,
                    fm=fm,
                    relative_path=relative,
                    lane="filed",
                    filename=None,
                    proposal_id=None,
                    formula_result=None,
                )
            )
    evaluated: list[BoardCard] = []
    for card in cards:
        related = _related(card, cards) if _needs_related(card.formula) else []
        result = _evaluate(card.formula, card.status, related)
        evaluated.append(
            BoardCard(**{**card.__dict__, "formula_result": result})
        )
    return evaluated


@structured_reader(category="front-matter")
def note_body(scope: Scope, relative: str) -> str:
    path = scope.resolve_stored(relative)
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise RedirectedPathError("module note is redirected")
    _fm, body = split_front_matter(path.read_text(encoding="utf-8"))
    return body


def _inbox_cards(scope: Scope, vault: Vault, items: list[InboxItem]) -> list[BoardCard]:
    entity = scope.current_entity()
    cards: list[BoardCard] = []
    for item in items:
        cards.append(
            _scalar_card(
                entity=entity,
                module=_INBOX_MODULE,
                block=vault.block_of(_INBOX_MODULE),
                title=item.title,
                fm=item.fm,
                relative_path=scope.vault_relative(item.path),
                lane="inbox",
                filename=item.path.name,
                proposal_id=None,
                formula_result=None,
            )
        )
    return cards


def _proposal_cards(scope: Scope, listing_rows) -> tuple[list[BoardCard], bool]:
    entity = scope.current_entity()
    cards: list[BoardCard] = []
    blocked = listing_rows.blocked
    for row in listing_rows.rows:
        proposal = row.proposal
        if not isinstance(proposal, Proposal) or proposal.action != "classify":
            continue
        title = row.title or Path(proposal.src).name
        cards.append(
            BoardCard(
                entity=entity,
                module=proposal.module,
                block=proposal.block,
                title=title,
                status="pending",
                sub=proposal.sub,
                product=None,
                member=None,
                updated=proposal.created[:10],
                due=None,
                cadence=None,
                formula=None,
                formula_result=None,
                progress=None,
                relative_path=proposal.src,
                lane="proposed",
                filename=None,
                proposal_id=proposal.id,
            )
        )
    return cards, blocked


@dataclass(frozen=True)
class EntityCards:
    """One entity's cards by lane, as the Board and the master view read them."""

    inbox: tuple[BoardCard, ...]
    proposed: tuple[BoardCard, ...]
    filed: tuple[BoardCard, ...]
    blocked: bool


@failure_contract(
    raises=(DestinationRegistryError, RedirectedPathError),
    calls=(read_inbox, project_outbox),
)
def entity_cards(scope: Scope, vault: Vault) -> EntityCards:
    """Read one entity's inbox, proposed and filed cards. Reads only."""
    inbox = _inbox_cards(scope, vault, read_inbox(scope))
    proposed, blocked = _proposal_cards(scope, project_outbox(scope))
    filed = _read_filed(scope, vault)
    return EntityCards(
        inbox=tuple(inbox),
        proposed=tuple(proposed),
        filed=tuple(filed),
        blocked=blocked,
    )


def _sort_cards(cards: list[BoardCard], sort: str) -> list[BoardCard]:
    if sort == "title":
        return sorted(cards, key=lambda card: (card.title.casefold(), card.relative_path))
    present = [card for card in cards if card.updated]
    blank = [card for card in cards if not card.updated]
    present.sort(key=lambda card: card.relative_path)
    present.sort(key=lambda card: card.updated, reverse=True)
    blank.sort(key=lambda card: card.relative_path)
    return present + blank


def _matches(card: BoardCard, subs: tuple[str, ...], products: tuple[str, ...]) -> bool:
    if subs and (card.sub or "") not in subs:
        return False
    if products and (card.product or "") not in products:
        return False
    return True


def _query(
    entity: str,
    *,
    view: str,
    group: str,
    sort: str,
    subs: tuple[str, ...],
    products: tuple[str, ...],
    month: str,
    week: str,
    note: str,
) -> str:
    pairs: list[tuple[str, str]] = [
        ("view", view),
        ("group", group),
        ("sort", sort),
    ]
    pairs.extend(("sub", value) for value in subs)
    pairs.extend(("product", value) for value in products)
    if month:
        pairs.append(("month", month))
    if week:
        pairs.append(("week", week))
    if note:
        pairs.append(("note", note))
    return f"/board/{entity}?{urlencode(pairs)}"


def _chips(
    entity: str,
    population: list[BoardCard],
    *,
    view: str,
    group: str,
    sort: str,
    subs: tuple[str, ...],
    products: tuple[str, ...],
    month: str,
    week: str,
) -> tuple[BoardLink, ...]:
    links: list[BoardLink] = []
    for field, selected in (("sub", subs), ("product", products)):
        counts: dict[str, int] = {}
        for card in population:
            value = card.sub if field == "sub" else card.product
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        for value in sorted(counts):
            active = value in selected
            if field == "sub":
                next_subs = tuple(item for item in subs if item != value)
                next_products = products
                if not active:
                    next_subs = tuple(sorted((*subs, value)))
            else:
                next_subs = subs
                next_products = tuple(item for item in products if item != value)
                if not active:
                    next_products = tuple(sorted((*products, value)))
            links.append(
                BoardLink(
                    label=value,
                    href=_query(
                        entity,
                        view=view,
                        group=group,
                        sort=sort,
                        subs=next_subs,
                        products=next_products,
                        month=month,
                        week=week,
                        note="",
                    ),
                    active=active,
                    count=counts[value],
                )
            )
    return tuple(links)


def _links(
    entity: str,
    *,
    view: str,
    group: str,
    sort: str,
    subs: tuple[str, ...],
    products: tuple[str, ...],
    month: str,
    week: str,
) -> tuple[tuple[BoardLink, ...], tuple[BoardLink, ...], tuple[BoardLink, ...]]:
    common = dict(subs=subs, products=products, month=month, week=week, note="")
    views = tuple(
        BoardLink(
            label=name.capitalize(),
            href=_query(entity, view=name, group=group, sort=sort, **common),
            active=name == view,
        )
        for name in ("board", "list", "gallery", "calendar", "planner", "map")
    )
    groups = tuple(
        BoardLink(
            label={"sub": "Category", "module": "Module"}.get(name, name.capitalize()),
            href=_query(entity, view=view, group=name, sort=sort, **common),
            active=name == group,
        )
        for name in ("review", "status", "sub", "module")
    )
    sorts = tuple(
        BoardLink(
            label={"updated": "Updated", "title": "Title"}[name],
            href=_query(entity, view=view, group=group, sort=name, **common),
            active=name == sort,
        )
        for name in ("updated", "title")
    )
    return views, groups, sorts


def _column(
    key: str,
    label: str,
    cards: list[BoardCard],
    *,
    module: str | None,
    sub: str | None,
    missing: bool,
    droppable: bool,
    sort: str,
) -> BoardColumn:
    return BoardColumn(
        key=key,
        label=label,
        module=module,
        sub=sub,
        missing=missing,
        droppable=droppable,
        cards=tuple(_sort_cards(cards, sort)),
    )


def _review_columns(
    vault: Vault,
    scope: Scope,
    inbox: list[BoardCard],
    proposed: list[BoardCard],
    *,
    sort: str,
    droppable: bool,
) -> tuple[BoardColumn, ...]:
    columns: list[BoardColumn] = [
        _column(
            "inbox",
            "Inbox",
            inbox,
            module=None,
            sub=None,
            missing=False,
            droppable=False,
            sort=sort,
        )
    ]
    placed: set[str] = set()
    for module in sorted(vault.active_modules_for(scope)):
        if module == _INBOX_MODULE:
            continue
        spec = vault.module_spec(module)
        if spec.get("lifecycle_pattern", True) is False:
            continue
        missing = _require_real_directory(scope, module) is None
        subs = sorted(vault.active_submodules_for(scope, module))
        targets = subs or [None]
        for sub in targets:
            key = f"{module}/{sub or ''}"
            label = f"{module} / {sub}" if sub else module
            matched = [
                card
                for card in proposed
                if card.module == module and card.sub == sub
            ]
            placed.update(card.relative_path for card in matched)
            active = _require_real_directory(scope, module, "active")
            columns.append(
                _column(
                    key,
                    label,
                    matched,
                    module=module,
                    sub=sub,
                    missing=missing,
                    droppable=droppable and active is not None,
                    sort=sort,
                )
            )
    orphans = [card for card in proposed if card.relative_path not in placed]
    for card in orphans:
        key = f"orphan/{card.module}/{card.sub or ''}"
        if any(column.key == key for column in columns):
            continue
        matched = [
            other
            for other in orphans
            if other.module == card.module and other.sub == card.sub
        ]
        columns.append(
            _column(
                key,
                f"{card.module} / {card.sub}" if card.sub else card.module,
                matched,
                module=None,
                sub=None,
                missing=False,
                droppable=False,
                sort=sort,
            )
        )
    return tuple(columns)


def _library_columns(
    vault: Vault,
    scope: Scope,
    filed: list[BoardCard],
    *,
    group: str,
    sort: str,
) -> tuple[BoardColumn, ...]:
    if group == "module":
        columns = []
        for module in sorted(vault.active_modules_for(scope)):
            if module == _INBOX_MODULE:
                continue
            spec = vault.module_spec(module)
            if spec.get("lifecycle_pattern", True) is False:
                continue
            missing = _require_real_directory(scope, module) is None
            matched = [card for card in filed if card.module == module]
            columns.append(
                _column(
                    module,
                    module,
                    matched,
                    module=module,
                    sub=None,
                    missing=missing,
                    droppable=False,
                    sort=sort,
                )
            )
        return tuple(columns)
    if group == "sub":
        declared: list[str] = []
        for module in sorted(vault.active_modules_for(scope)):
            if module == _INBOX_MODULE:
                continue
            for sub in sorted(vault.active_submodules_for(scope, module)):
                if sub not in declared:
                    declared.append(sub)
        present = sorted({card.sub for card in filed if card.sub and card.sub not in declared})
        labels = declared + present
        columns = [
            _column(
                sub,
                sub,
                [card for card in filed if card.sub == sub],
                module=None,
                sub=sub,
                missing=False,
                droppable=False,
                sort=sort,
            )
            for sub in labels
        ]
        blank = [card for card in filed if not card.sub]
        if blank:
            columns.append(
                _column(
                    "unspecified",
                    "Unspecified",
                    blank,
                    module=None,
                    sub=None,
                    missing=False,
                    droppable=False,
                    sort=sort,
                )
            )
        return tuple(columns)
    labels = sorted({card.status for card in filed if card.status})
    columns = [
        _column(
            status,
            status,
            [card for card in filed if card.status == status],
            module=None,
            sub=None,
            missing=False,
            droppable=False,
            sort=sort,
        )
        for status in labels
    ]
    blank = [card for card in filed if not card.status]
    if blank:
        columns.append(
            _column(
                "unspecified",
                "Unspecified",
                blank,
                module=None,
                sub=None,
                missing=False,
                droppable=False,
                sort=sort,
            )
        )
    return tuple(columns)


def _by_day(filed: list[BoardCard], sort: str) -> dict[date, list[BoardCard]]:
    by_day: dict[date, list[BoardCard]] = {}
    for card in filed:
        placed = _day(card.due) or _day(card.updated)
        if placed is None:
            continue
        by_day.setdefault(placed, []).append(card)
    return {day: _sort_cards(cards, sort) for day, cards in by_day.items()}


def _weeks(
    filed: list[BoardCard], month_start: date, sort: str
) -> tuple[tuple[CalendarDay, ...], ...]:
    cal = calendar.Calendar(firstweekday=0)
    by_day = _by_day(filed, sort)
    weeks = []
    for week in cal.monthdatescalendar(month_start.year, month_start.month):
        weeks.append(
            tuple(
                CalendarDay(
                    iso=day.isoformat(),
                    number=day.day,
                    in_month=day.month == month_start.month,
                    cards=tuple(by_day.get(day, ())),
                )
                for day in week
            )
        )
    return tuple(weeks)


def _map_rows(vault: Vault, scope: Scope, filed: list[BoardCard]) -> tuple[MapRow, ...]:
    rows = []
    for module in sorted(vault.active_modules_for(scope)):
        if module == _INBOX_MODULE:
            continue
        spec = vault.module_spec(module)
        if spec.get("lifecycle_pattern", True) is False:
            continue
        notes = [card for card in filed if card.module == module]
        if notes:
            score = 0.0
            for card in notes:
                if card.progress is not None:
                    score += card.progress / 100
                elif card.status:
                    score += 1
            percent = round(100 * score / len(notes))
        else:
            percent = 0
        missing = _require_real_directory(scope, module) is None
        rows.append(
            MapRow(
                module=module,
                block=vault.block_of(module),
                count=len(notes),
                percent=percent,
                missing=missing,
            )
        )
    return tuple(rows)


def _attention(
    inbox: list[BoardCard], proposed: list[BoardCard], filed: list[BoardCard], today: date
) -> Attention:
    start = today - timedelta(days=7)
    recent = []
    for card in filed:
        updated = _day(card.updated)
        if updated is not None and start <= updated <= today:
            recent.append(card)
    counts: dict[str, int] = {}
    for card in recent:
        label = card.status or "Unspecified"
        counts[label] = counts.get(label, 0) + 1
    return Attention(
        inbox=len(inbox),
        proposed=len(proposed),
        updated=len(recent),
        by_status=tuple(sorted(counts.items())),
    )


def _outline(body: str) -> tuple[OutlineNode, ...]:
    items: list[tuple[int, str]] = []
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            items.append((len(heading.group(1)), heading.group(2).strip()))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            items.append((len(bullet.group(1)) // 2 + 1, bullet.group(2).strip()))
    root: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for depth, text in items:
        node = {"text": text, "children": []}
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            root.append(node)
        stack.append((depth, node))

    def freeze(nodes: list[dict]) -> tuple[OutlineNode, ...]:
        return tuple(OutlineNode(node["text"], freeze(node["children"])) for node in nodes)

    return freeze(root)


def _shift_month(month_start: date, step: int) -> date:
    month_index = month_start.year * 12 + (month_start.month - 1) + step
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


@failure_contract(
    raises=(DestinationRegistryError, RedirectedPathError),
    calls=(entity_cards,),
)
def load_board(
    scope: Scope,
    vault: Vault,
    *,
    view: str,
    group: str,
    sort: str,
    subs: tuple[str, ...],
    products: tuple[str, ...],
    month: str,
    week: str,
    note: str,
    today: date | None = None,
) -> BoardPage:
    """Assemble one entity's board. Reads only."""
    current = today or date.today()
    selected_view = _choice(view, _VIEWS, "board")
    selected_group = _choice(group, _GROUPS, "review")
    selected_sort = _choice(sort, _SORTS, "updated")
    cards = entity_cards(scope, vault)
    inbox, proposed, filed = list(cards.inbox), list(cards.proposed), list(cards.filed)
    blocked = cards.blocked
    shows_filed = selected_view in {"gallery", "calendar", "planner", "map"} or selected_group != "review"
    shows_inbox = selected_view == "planner" or (
        selected_view in {"board", "list"} and selected_group == "review"
    )
    shows_proposed = selected_view in {"board", "list"} and selected_group == "review"
    population: list[BoardCard] = []
    if shows_inbox:
        population.extend(inbox)
    if shows_proposed:
        population.extend(proposed)
    if shows_filed:
        population.extend(filed)
    filtered = [card for card in population if _matches(card, subs, products)]
    filed_filtered = [card for card in filed if _matches(card, subs, products)]
    inbox_filtered = [card for card in inbox if _matches(card, subs, products)]
    proposed_filtered = [card for card in proposed if _matches(card, subs, products)]
    draggable = (
        selected_view == "board"
        and selected_group == "review"
        and not blocked
    )
    if draggable:
        inbox_filtered = [
            BoardCard(**{**card.__dict__, "draggable": True}) for card in inbox_filtered
        ]
    if selected_group == "review":
        columns = _review_columns(
            vault,
            scope,
            inbox_filtered,
            proposed_filtered,
            sort=selected_sort,
            droppable=draggable,
        )
        listed = _sort_cards(inbox_filtered + proposed_filtered, selected_sort)
    else:
        columns = _library_columns(
            vault, scope, filed_filtered, group=selected_group, sort=selected_sort
        )
        listed = _sort_cards(filed_filtered, selected_sort)

    month_start = _day(f"{month}-01") if _MONTH.fullmatch(month) else None
    if month_start is None:
        month_start = current.replace(day=1)
    week_start = _day(week)
    if week_start is None:
        week_start = current - timedelta(days=current.weekday())
    else:
        week_start = week_start - timedelta(days=week_start.weekday())
    month_token = month_start.strftime("%Y-%m")
    week_token = week_start.isoformat()
    view_links, group_links, sort_links = _links(
        scope.current_entity(),
        view=selected_view,
        group=selected_group,
        sort=selected_sort,
        subs=subs,
        products=products,
        month=month_token,
        week=week_token,
    )
    opened = next((card for card in filed if card.relative_path == note), None)
    outline = _outline(note_body(scope, opened.relative_path)) if opened else ()
    undated = tuple(
        card for card in _sort_cards(filed_filtered, selected_sort)
        if _day(card.due) is None and _day(card.updated) is None
    )
    common = dict(
        view=selected_view,
        group=selected_group,
        sort=selected_sort,
        subs=subs,
        products=products,
        note="",
    )
    entity = scope.current_entity()
    return BoardPage(
        entity=entity,
        view=selected_view,
        group=selected_group,
        sort=selected_sort,
        blocked=blocked,
        columns=columns,
        cards=tuple(listed if selected_view != "gallery" else _sort_cards(filed_filtered, selected_sort)),
        undated=undated,
        unscheduled=tuple(_sort_cards(inbox_filtered, selected_sort)),
        habits=tuple(card for card in _sort_cards(filed_filtered, selected_sort) if card.cadence),
        weeks=_weeks(filed_filtered, month_start, selected_sort),
        days=_span(filed_filtered, week_start, 7, selected_sort),
        map_rows=_map_rows(vault, scope, filed_filtered),
        view_links=view_links,
        group_links=group_links,
        sort_links=sort_links,
        chips=_chips(
            entity,
            population,
            view=selected_view,
            group=selected_group,
            sort=selected_sort,
            subs=subs,
            products=products,
            month=month_token,
            week=week_token,
        ),
        period_label=(
            month_start.strftime("%B %Y")
            if selected_view == "calendar"
            else f"{week_start.isoformat()} – {(week_start + timedelta(days=6)).isoformat()}"
        ),
        previous_href=_query(
            entity,
            month=_shift_month(month_start, -1).strftime("%Y-%m")
            if selected_view == "calendar"
            else month_token,
            week=(week_start - timedelta(days=7)).isoformat()
            if selected_view != "calendar"
            else week_token,
            **common,
        ),
        next_href=_query(
            entity,
            month=_shift_month(month_start, 1).strftime("%Y-%m")
            if selected_view == "calendar"
            else month_token,
            week=(week_start + timedelta(days=7)).isoformat()
            if selected_view != "calendar"
            else week_token,
            **common,
        ),
        attention=_attention(inbox, proposed, filed, current),
        outline=outline,
        outline_title=opened.title if opened else "",
        note_open=opened is not None,
        clear_outline_href=_query(entity, month=month_token, week=week_token, **common),
        self_href=_query(
            entity,
            month=month_token,
            week=week_token,
            **{**common, "note": note},
        ),
    )


def _span(filed: list[BoardCard], start: date, count: int, sort: str) -> tuple[CalendarDay, ...]:
    by_day = _by_day(filed, sort)
    days = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        days.append(
            CalendarDay(
                iso=day.isoformat(),
                number=day.day,
                in_month=True,
                cards=tuple(by_day.get(day, ())),
            )
        )
    return tuple(days)


@failure_contract(raises=(IngestError, CrossScopeError))
def capture_note(scope: Scope, *, title: str, text: str) -> IngestResult:
    """One redacted inbox receipt through the shared ingest path."""
    cleaned = " ".join(title.split())
    if not cleaned or len(cleaned) > 200:
        raise IngestError("capture needs a title")
    body = text if isinstance(text, str) else ""
    if len(body) > 8000:
        raise IngestError("capture text is too long")
    digest = hashlib.sha256(f"{cleaned}\n{body}".encode("utf-8")).hexdigest()
    return commit_inbox_item(
        scope,
        text=body,
        title=cleaned,
        source="capture",
        source_id=digest,
        received_at=datetime.now().isoformat(timespec="seconds"),
        sha256=digest,
        mime="text/plain",
        size=len(body.encode("utf-8")),
        slug_seed=digest,
    )
