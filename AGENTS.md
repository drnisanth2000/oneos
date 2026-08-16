# OneOS

A read-and-approve surface over a git-backed markdown vault.
FastAPI + Jinja + HTMX + Alpine. No build step, no npm, no bundler.

> Place at the root of `~/code/oneos/`.
>
> This is the canonical agent file. `CLAUDE.md` and `.cursorrules` are symlinks
> to it — Claude Code, Codex, Cursor and Hermes all discover one of those names.
> Edit this file only.
>
> Set `ONEOS_VAULT` to the vault root before working with private files.
>
> Vault state assumed: commit `2aa8b14` or later. If
> `git -C "$ONEOS_VAULT" log --oneline -1` shows something older, stop and ask —
> this file cites counts and paths that changed on that commit.

## Read before building

Specifications live in the vault, outside this repo:

```
$ONEOS_VAULT/_system/conventions.md                  v2.0.0 — AUTHORITATIVE
$ONEOS_VAULT/_system/conventions-v2.1-additions.md   what was added on top
$ONEOS_VAULT/_system/archetypes.yaml                 flags, modules, blocks, sub ids
$ONEOS_VAULT/_system/hermes-context.md               the agent layer's grounding
$ONEOS_VAULT/_system/scripts/policy_enforcer.py      working, tested — extract from it
$ONEOS_VAULT/_system/scripts/check_v2.py             structural checks
$ONEOS_VAULT/_system/blueprint/                      the module scaffold
$ONEOS_VAULT/_system/docs/oneos-spec.md              what to build, in order
```

**Where `conventions.md` v2 and anything in `docs/` disagree, v2 wins.**
Where v2 is silent and `conventions-v2.1-additions.md` speaks, v2.1 wins.

Current task: the remaining **Safety Foundation** work marked in `BUILD.md`,
required before live Phase 1 gate trials. S1-S5 are merged; S6 is next. This
hardening does not unlock a new phase or authorize deferred screens.

Product direction and naming are frozen in `PRODUCT-THESIS.md`.

## Layers and ownership

- **OneOS** is the complete system.
- **OneOS** is this FastAPI/Jinja/HTMX human surface.
- **Command Center** is the deterministic orchestration boundary inside the
  app. It owns request scope, validation, proposals, policy gates, and approved
  Git transactions. It starts as a service boundary, not another daemon.
- **Grey Matter** is the private Markdown/SQLite/Git system of record.
- **Hermes** is an asynchronous worker for schedules, delivery, and judgement.
  It proposes through the outbox and never becomes approval authority.

## Cloud and pull-request boundary

Cloud work may proceed only from a complete sanitized public task contract:
outcome, in-scope files, out-of-scope changes, acceptance tests, private-gate
requirements, dependencies, and stop conditions must all be explicit. Cloud
agents use this repository and synthetic fixtures only. They never receive the
live Grey Matter vault, its registries, databases, paths, Git history, or
private decision authority. If work requires any private authority or material,
the cloud agent stops and returns the task to the trusted local boundary.

The following conditions are binding cloud-agent stops: dependency changes,
convention or schema changes, security-boundary changes, destructive actions,
deployment, and unresolved product decisions. Every write task uses a `codex/`
branch and a pull request. CI is necessary but not sufficient: changes that
read or interpret vault structure also require the local private integration
gate in `BUILD.md` before merge.

The authenticated GitHub owner is never written into tracked source or
configuration. GitHub-generated remote and merge metadata may include it; that
is the sole owner exception and does not relax the tracked-content rule.

## Repository and task hygiene

The canonical development baseline is the current fetched `origin/main`, not a
local `main` name or another clone's object graph. Before creating a worktree,
fetch and record the exact `origin/main` SHA. Create the branch from that ref,
then prove the new worktree starts at the recorded SHA. A local checkout that
has not fetched the merged predecessor is stale even when its own `main` is
clean.

Safety Foundation steps are sequential integration boundaries:

- do not begin the next step until its predecessor is merged into
  `origin/main` and the fresh merged baseline passes;
- use one task/session and one branch per step; do not continue the next step
  inside the previous step's task or pull request;
- a handoff must state repository root, recorded `origin/main`, branch,
  worktree, head, merge/PR state, public and private gate results, and whether
  Grey Matter had preserved pre-existing edits; and
- completed design and implementation-plan files are historical records. Their
  old branch commands, test counts, and stop conditions are not current
  instructions unless `BUILD.md` explicitly reactivates them.

Gitleaks' Git mode scans every reachable local ref, not only the checked-out
branch. When a finding appears in one clone but not another, identify the exact
retaining ref before changing `.gitleaksignore`. Prune only a proven-obsolete
ref; add an exact fingerprint only when that retained history is intentional.

## The one rule

**No instance-specific value ever appears in this repo.** This prohibits
organization, person, entity, module, vault-path, and credential values.

Everything comes from registries read at runtime:

```
_system/archetypes.yaml             flags, modules, blocks, sub ids
_system/entities.yaml               bundles + flags — THE MANIFEST
_system/products.yaml               product: vocabulary
_system/members.yaml                member: vocabulary
_system/workspaces.yaml             saved scopes
_system/phase.yaml                  capability ceiling
_system/scripts/action-policy.yaml  actor and action rules
```

Note the path on the last one — `action-policy.yaml` lives under `scripts/`,
not directly under `_system/`. An earlier version of this file had it wrong.

`_system/classifier/rules.yaml` is referenced by the spec but **does not exist
yet** — it is created in step 6. Do not read it before then, and do not treat
its absence as a fault.

**Test:** swap in a different `entities.yaml` and you get a different system with
no code change. If that is not true, it is wrong.

This repository is AGPL-3.0 licensed and intended for eventual public release.
The vault is never public.

## Invariants — do not violate

1. **Curated content changes go through the outbox.** The sole intake exception
   is creation of a redacted receipt in `<entity>/00-inbox/active/` with
   `sub: triage`; ingestion immediately commits only that receipt as one
   `ingest:` commit. Raw source content never enters the vault. Approval moves
   the tracked receipt and commits the curated change.
2. **Git is the audit trail.** No side effect without a revertible diff.
3. **No LLM call in the request path.** Classification is rule-based. Judgement
   belongs to Hermes, asynchronously, via the outbox.
4. **`scope.current_entity()` wraps every query and path resolution**, from the
   first commit. It is the future tenant boundary.

## Information residence and migration

The vault is the **working brain, not the bulk-file warehouse**. Put a file in
the vault when the system must edit it, version it, or drive a workflow from
it. Keep large, inactive, or externally collaborative files in their existing
storage and represent them with metadata and a resolvable source reference.

Use four residence patterns without creating a second taxonomy:

- **Working document:** lives in the applicable module's `active/` directory
  while it is being edited or versioned.
- **Shared document:** remains in its collaborative store; the vault holds a
  metadata stub and, where needed, verified milestone snapshots.
- **Data carrier:** structured facts go to `books.db`; the source is retained
  outside the vault with provenance linking every extracted row back to it.
- **Knowledge carrier:** a concise, sourced entry is proposed for an existing
  curated note; disposal of the original remains a destructive action.

Migration starts with a **read-only inventory**. Execution is always copy,
SHA-256 verify, then quarantine — never move-and-delete. Missing, duplicate,
or unaccounted items are hard failures, and completion requires proof that
every inventoried source still exists at its original location, its approved
destination, or in quarantine.

Batch repetitive decisions into one review where practical, but every batch
must still go through the outbox, produce one revertible commit on approval,
and preserve per-item provenance. A migration or classifier may not invent
physical sub-folders: the existing `sub:` front-matter rule and 15-file earned
folder threshold remain authoritative.

## Vault structure — the parts that bite

**Entity discovery is `entities.yaml`.** Not `index.md` (stale, lists one of
five bundles) and not a directory scan — a scan cannot distinguish a bundle from
a stray folder, and cannot supply labels or flags. An earlier draft of spec §10
step 2 said "directory scan"; that predated `entities.yaml` and is withdrawn.

**Module activation reads `flags:` only.** `entities.yaml` carries both
`archetype:` and `flags:`. `archetype:` is a creation-time preset that seeded
the flag list once; it is **never merged at read time** (`decisions.md`
2026-08-05). `check_v2.load_expected_modules` follows the same rule — match it,
or the sidebar and the validator will disagree on which modules exist.

**Every module has the lifecycle layer:**

```
<entity>/<NN-module>/
  _templates/   active/   archive/   status.md
```

`12-archive` is the exception (no `active/`, no `archive/`).
`13-analytics` adds `snapshots/`, `dashboard.md`, `kpis.yaml`.

**Sub-modules are front-matter, not folders.** A file in `09-marketing/active/`
carries `sub: content`. A physical sub-folder is *earned* — proposed through the
outbox once one `sub:` value **reaches 15 files or more**. **Never scaffold
sub-folders.**

A legal matter is `<entity>/14-legal/active/<slug>.md` with `sub: matters` —
not `14-legal/matters/<slug>/`.

**Blocks are lowercase and derived from the module number**, defined in
`archetypes.yaml`. Never hardcode the map, never store `block:` per file.

**Inbox items live in `00-inbox/active/`** with `sub: triage`.

**A module the flags require but the disk lacks is an error, not a missing
link.** `check_v2` raises `E4` for exactly this. The sidebar should surface it,
not quietly render a shorter list. The standing synthetic fixture regression
covers this condition because validators that iterate only over existing paths
cannot report an absent module.

## Stack — decided, do not substitute

Python 3.12 · `uv` · FastAPI · Jinja2 · HTMX · Alpine.js · `alpine-morph`
(required) · `alpine-persist` · Pydantic v2 · `python-frontmatter` · GitPython ·
SQLite · Caddy · Docker Compose

Excluded, with reasons in the spec: React, Next, Postgres, Supabase, Redis,
Celery, Kubernetes, Tailwind build step, n8n.

Vendor JS into `static/vendor/`. No CDN links.

## Console terminology

The workspace switcher selects an entity or a saved scope. **Blocks / Modules**
are two views inside that scope: blocks are registry-defined purpose groupings;
modules are the actual registry-defined vault modules. Do not label the second
view "Entity" or expose arbitrary directory browsing. Display labels may be
uppercase, but stored block values remain lowercase.

The main canvas is the **Command Center** screen. It may summarize scoped data
and surface triage, proposals, errors, and approvals, but every value comes from
runtime registries or vault state. Never copy instance values from a mockup.

## Gotchas

- `alpine-morph` is mandatory — a default HTMX swap destroys Alpine state on
  every auto-refresh
- `outbox/` and `staging/` are `system` — exclude from block-mapping validation.
  They are absent from `archetypes.yaml` `modules:` by design
- Git does not track empty directories — scaffolded folders need `.gitkeep`
- `books.db` sits at entity root and serves all modules, not just `07-finance`
- A **pre-commit hook** runs `policy_enforcer.py` on staged files. Every markdown
  file needs valid front-matter or the commit is blocked. `_system/` docs use
  `type: system-doc` — required fields: `type`, `title`, `version`, `status`,
  `created`, `updated`
- `check_v2.py` skips `_system/`; `policy_enforcer.py` does not. Check both
- `check_v2.py` takes the vault path as `argv[1]`, defaulting to `.`. Run it from
  the vault root as `python3 _system/scripts/check_v2.py .` — running it from
  inside `scripts/` makes it look for `_system/_system/archetypes.yaml`

## Testing

Every step ends in a working, committed state. Two tests matter more than
coverage:

- **Revert test** — an approved action produces exactly one commit and
  `git revert` undoes it with no manual cleanup
- **Rename test** — create a throwaway entity, rename it, assert every reference
  resolves. Must also assert a `.sensitive/` read is still denied afterwards

Existing suite: `cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover`
— 37 tests, all passing. Keep them passing. `check_v2` is at 0 errors and
0 warnings; keep it there.

## Do not

- Build blocks or screens outside the current step
- Add a dependency the spec does not already cover
- Put an LLM anywhere near the request path
- Write curated content outside the outbox flow, or write intake outside the
  redacted receipt + isolated `ingest:` commit exception
- Scaffold sub-folders
- Hardcode a slug, a block map, or a module list
- Merge `archetype:` into `flags:` at read time
