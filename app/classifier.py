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

from .vault import Vault

_DEFAULT = {"module": "00-inbox", "sub": "triage"}


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
        self._path = vault.scope.system_path("classifier", "rules.yaml")
        self._cfg = self._load()

    def _load(self) -> dict:
        if not self._path.is_file():
            return {"rules": [], "default": dict(_DEFAULT)}
        cfg = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        cfg.setdefault("rules", [])
        cfg.setdefault("default", dict(_DEFAULT))
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
                route = rule.get("route") or {}
                module = route.get("module", _DEFAULT["module"])
                return Classification(
                    module=module,
                    sub=route.get("sub", _DEFAULT["sub"]),
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
