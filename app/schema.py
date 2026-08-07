"""schema.py — the shared Pydantic front-matter model (spec §10 step 3, §7).

Extracted from `policy_enforcer.validate_front_matter` (+ the v2 fields
`check_v2.py` reads). This is a refactor of tested code: the verdict here must
match the enforcer's on every real file, or spec §11 gate 4 fails.

The enforcer checks **presence only** of the required fields, dispatched by
`type` — it never constrains a value. So the model does the same: required
fields are declared as required-but-any-typed, extra fields are allowed, and
the useful v2 fields (`block`, `sub`, `orchestration`, `cadence`, …) are
declared optional so downstream code has named access without changing any
verdict. Typing them strictly would reject files the enforcer accepts (e.g.
`product: null`, a date-typed `created`, or `version: 2.0` parsed as a float)
and is therefore a behaviour change, not the refactor the step calls for.

The required-field sets are duplicated from the enforcer deliberately: the two
run in different worlds (this repo has Pydantic; the enforcer is stdlib-only,
no venv, by design) so they cannot share an import today. The 100-file
agreement check is what guards against the two drifting apart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

# Mirror of policy_enforcer.REQUIRED_FRONT_MATTER_FIELDS[_BY_TYPE].
REQUIRED_FIELDS = ["type", "title", "entity", "product", "status", "created", "updated"]
REQUIRED_FIELDS_BY_TYPE: dict[str, list[str]] = {
    "system-doc": ["type", "title", "version", "status", "created", "updated"],
}


class _Base(BaseModel):
    # extra="allow": unknown keys never fail, matching the enforcer, and stay
    # accessible on the model.
    model_config = ConfigDict(extra="allow")

    # Required in every schema. `Any` with no default = "must be present, any
    # value" — exactly the enforcer's presence check.
    type: Any
    title: Any
    status: Any
    created: Any
    updated: Any

    # Optional v2 fields the step says to cover. Declared, never required, so
    # they give typed-ish access without changing a verdict.
    block: Any = None
    sub: Any = None
    orchestration: Any = None
    cadence: Any = None


class EntityFrontMatter(_Base):
    """Standard entity-scoped document (conventions §3.1 Variant A)."""

    entity: Any
    product: Any


class SystemDocFrontMatter(_Base):
    """`type: system-doc` — no entity/product; carries `version` (Variant B)."""

    version: Any


_MODEL_BY_TYPE: dict[Any, type[_Base]] = {"system-doc": SystemDocFrontMatter}


def model_for(fm: dict) -> type[_Base]:
    """The model class for this front-matter, dispatched by `type` — the same
    dispatch the enforcer does. Unknown/absent type -> the standard schema."""
    return _MODEL_BY_TYPE.get(fm.get("type"), EntityFrontMatter)


def validate_front_matter(fm: dict) -> tuple[bool, list[str]]:
    """Validate an already-parsed front-matter mapping. Returns (ok, problems),
    with the same 'missing required field: X' messages the enforcer emits."""
    if not isinstance(fm, dict):
        # The enforcer assumes a mapping and would raise on a scalar; be
        # explicit instead of crashing. Real vault front-matter is always a map.
        return False, ["front-matter is not a mapping"]
    try:
        model_for(fm).model_validate(fm)
        return True, []
    except ValidationError as e:
        problems = [
            f"missing required field: {err['loc'][0]}"
            for err in e.errors()
            if err["type"] == "missing"
        ]
        # With Any-typed fields the only possible error is a missing field, but
        # surface anything else rather than swallow it.
        problems += [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
            if err["type"] != "missing"
        ]
        return False, problems


def validate_file_text(text: str) -> tuple[bool, list[str]]:
    """Validate a markdown file's text. Extraction replicates
    policy_enforcer.validate_front_matter byte-for-byte so the two agree."""
    if not text.startswith("---"):
        return False, ["no front-matter block found (file must start with '---')"]

    end = text.find("---", 3)
    if end == -1:
        return False, ["front-matter block not closed with second '---'"]

    fm_text = text[3:end]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        return False, [f"front-matter is not valid YAML: {e}"]

    return validate_front_matter(fm)


def validate_file(path: Path | str) -> tuple[bool, list[str]]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return validate_file_text(text)
