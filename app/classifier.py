"""classifier.py — deterministic, rule-based triage routing (spec §8.5).

No LLM in the request path (invariant 3). A rule matches on keyword substrings
(and optionally source); the first match wins. Corrections persist as new rules
via add_rule, so the next similar item arrives pre-classified — the correction
loop is the product, not day-one accuracy.

Reads `_system/classifier/rules.yaml` — created here, in step 6 (spec §2.2a
lists it as "STILL TO BUILD"). Absent file => everything is unclassified, which
is a safe default, not an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import yaml

from .console_routing import structured_reader
from .vault import DestinationRegistryError, Vault, _boundary

_DEFAULT = {"module": "00-inbox", "sub": "triage"}


def _require_mapping(value: object, message: str) -> None:
    """Mirrors the access this guards: every reader of `value` in this module
    tolerates it being *falsy* (`x or {}`), so only a *truthy* non-mapping —
    the shape that would actually crash the access — is fatal. Guarding on
    `value is not None` instead (an earlier revision of this function did)
    invents a refusal for every other falsy shape (`{}`, `0`, `''`, `False`)
    that the access already tolerates — design §5's "S6 changes the type of
    something already fatal, never the fatality of something already
    tolerated."
    """
    if value and not isinstance(value, dict):
        raise DestinationRegistryError(message)


def _validate_rules_shape(cfg: dict) -> None:
    """PR #15 must-fix 1: `_load()` checked only that the whole document is a
    mapping. A syntactically valid but wrongly shaped nested value passed
    silently and only crashed later inside `classify()` with a raw
    `AttributeError` (`E-UNKNOWN`) instead of `E-CONFIG`.

    Every shape validated here mirrors the corresponding access in
    `classify()` rather than guessing a stricter one — C1 (S6 review, fix
    batch): the previous revision of this function checked the wrong axis
    (type only, never truthiness) and turned 24 already-tolerated falsy
    shapes into a fatal `E-CONFIG`, six of them reachable on the primary
    triage screen. `classify()` reads `rules`, `match`, and `match.any`
    through `x or <fallback>`, which absorbs every falsy value; only a
    *truthy* wrong-shaped value reaches an attribute/method access that
    raises. So only that is validated here:

    - `rules` truthy and not a list — crashes the per-rule loop.
    - a rule item that is not a mapping — crashes `rule.get("match")`.
    - `match` truthy and not a mapping — crashes `match.get(...)`.
    - `match.any` truthy, a list, and containing a non-string element —
      crashes the `k.lower()` keyword comprehension. A truthy *non-list*
      `any` (a bare scalar, a mapping) is not validated: `classify()` never
      calls a mapping method on it, and iterating a string or a mapping in
      the keyword comprehension yields strings, which do not crash.
    - `default` truthy and not a mapping — crashes `d.get("module", ...)` the
      moment no rule matches.

    `route` is deliberately NOT validated here. `route.get("module", ...)`
    only ever runs once THIS rule has matched — a rule that never fires can
    carry a malformed `route:` all day (a hand-edited rule whose keywords
    have not yet triggered) without ever reaching that access. Validating it
    here for every rule regardless of match status was exactly the invented
    refusal C1 measured: "route: bad on a NON-matching rule" going from a
    200 triage page to a 500 `E-CONFIG`. The check instead lives in
    `classify()`'s matched branch, at the access site, wrapped in
    `_boundary` so it can only retype an access that already raises.

    Also deliberately NOT validated, because it is already safe: `match.source`
    is only ever compared with `!=`, which never raises for any type.
    `route.module`/`route.sub`/`default.module`/`default.sub` feed
    `Vault.block_of`, which already converts an unhashable value via its own
    `_boundary` guard, and any other wrong-but-hashable value (e.g. a number)
    resolves to `""` there and is refused downstream, gracefully, by
    `resolve_classification_destination`'s existing `_is_registry_id` check
    (`InvalidModule` -> `E-DEST`). Validating those here would not retype an
    existing crash — it would invent a new, broader one (aborting the whole
    classifier for a rule that was never going to be reached), which is
    exactly the fatality change this fix must not make.
    """
    rules = cfg.get("rules") or []
    if not isinstance(rules, list):
        raise DestinationRegistryError("classifier `rules:` must be a list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise DestinationRegistryError("classifier rule must be a mapping")
        match = rule.get("match")
        _require_mapping(match, "classifier rule `match:` must be a mapping")
        if isinstance(match, dict):
            any_kw = match.get("any") or []
            if isinstance(any_kw, list) and not all(
                isinstance(k, str) for k in any_kw
            ):
                raise DestinationRegistryError(
                    "classifier rule `match.any:` must be a list of strings"
                )

    default = cfg.get("default")
    _require_mapping(default, "classifier `default:` must be a mapping")


@dataclass(frozen=True)
class Classification:
    module: str
    sub: str
    block: str
    rule_id: str | None
    confident: bool


class Classifier:
    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._path = vault.system_path("classifier", "rules.yaml")
        self._cfg = self._load()

    @structured_reader(category="registry")
    def _load(self) -> dict:
        if not self._path.is_file():
            # Absent rules => everything is unclassified — a safe default,
            # not an error. The tolerance is deliberate and preserved.
            return {"rules": [], "default": dict(_DEFAULT)}
        try:
            cfg = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except UnicodeDecodeError as exc:
            raise DestinationRegistryError(
                "classifier rules registry is not valid UTF-8"
            ) from exc
        except OSError as exc:
            raise DestinationRegistryError(
                "classifier rules registry could not be read"
            ) from exc
        except yaml.YAMLError as exc:
            raise DestinationRegistryError(
                "classifier rules registry is invalid YAML"
            ) from exc
        if not isinstance(cfg, dict):
            raise DestinationRegistryError(
                "classifier rules registry must be a mapping"
            )
        cfg.setdefault("rules", [])
        cfg.setdefault("default", dict(_DEFAULT))
        _validate_rules_shape(cfg)
        return cfg

    def classify(self, title: str, summary: str, source: str | None) -> Classification:
        haystack = f"{title}\n{summary}".lower()
        for rule in self._cfg.get("rules") or []:
            match = rule.get("match") or {}
            want_source = match.get("source")
            if want_source and want_source != source:
                continue
            keywords = [k.lower() for k in (match.get("any") or [])]
            if keywords and any(k in haystack for k in keywords):
                # C1 (S6 review, fix batch): `route:` is validated here, at
                # the access site, only once this rule has matched — not
                # eagerly for every rule in `_validate_rules_shape`. A
                # falsy `route:` is already tolerated by `or {}`, matching
                # every other access in this class; only a truthy
                # non-mapping reaches `.get(...)` and raises, and only then
                # does `_boundary` retype it.
                route = rule.get("route") or {}
                module = _boundary(
                    lambda: route.get("module", _DEFAULT["module"]),
                    "classifier rule `route:` must be a mapping",
                )
                sub = _boundary(
                    lambda: route.get("sub", _DEFAULT["sub"]),
                    "classifier rule `route:` must be a mapping",
                )
                return Classification(
                    module=module,
                    sub=sub,
                    block=self._vault.block_of(module),
                    rule_id=rule.get("id"),
                    confident=True,
                )
        d = self._cfg.get("default") or _DEFAULT
        module = d.get("module", _DEFAULT["module"])
        return Classification(
            module=module,
            sub=d.get("sub", _DEFAULT["sub"]),
            block=self._vault.block_of(module),
            rule_id=None,
            confident=False,
        )

    def add_rule(self, keywords: list[str], module: str, sub: str,
                 source: str | None = None, rule_id: str | None = None) -> None:
        """Persist a correction as a new rule. Direct registry write (like
        add/edit, spec §2.2b) — the file is the source of truth."""
        match: dict = {"any": list(keywords)}
        if source:
            match["source"] = source
        rule = {
            "id": rule_id or f"rule-{len(self._cfg.get('rules') or []) + 1}",
            "match": match,
            "route": {"module": module, "sub": sub},
            "created": date.today().isoformat(),
        }
        self._cfg.setdefault("rules", []).append(rule)
        self._cfg.setdefault("default", dict(_DEFAULT))
        self._cfg.setdefault("version", "1.0")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.safe_dump(self._cfg, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
