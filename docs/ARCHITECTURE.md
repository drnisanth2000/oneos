# OneOS architecture — code map

A map of the code as built: where each responsibility lives and which module
owns each rule. Rules and invariants are defined in `AGENTS.md`; build state is
in `docs/STATUS.md`. This file points at the owners and does not restate their
rules. If the code and this map disagree, the code is right and this file is
the bug.

## Layers

| Layer | Owner in this repository |
|---|---|
| Human surface | `app/main.py` routes, `templates/`, `static/` |
| Command Center | scope, destinations, outbox, review tokens, Git transactions (below) |
| Grey Matter (vault) | external; reached only through `ONEOS_VAULT` and `Scope` |
| Hermes | external; no code here. It proposes through the outbox |

## Module map

**Request scope and registries**

- `app/config.py` — reads `ONEOS_VAULT`, pins the vault root's identity, builds
  the entity catalog and a `Scope`.
- `app/entities.py` — loads `_system/entities.yaml`, the only entity authority.
- `app/scope.py` — immutable per-request entity scope. Every path resolution
  goes through `Scope.resolve` / `resolve_stored` (invariant 4).
- `app/vault.py` — reads `archetypes.yaml`, derives active modules from
  `flags:` only, derives blocks, and surfaces missing modules (`E4`).
- `app/identifiers.py` — the single registry-identifier length rule.
- `app/schema.py` — Pydantic front-matter model mirroring `policy_enforcer`.

**Intake (the sole direct-write exception)**

- `app/ingest/envelope.py` — the normalised intake envelope.
- `app/ingest/pii.py` — deterministic PII filter.
- `app/ingest/base.py` — `commit_inbox_item`, the one ingest write path: one
  redacted receipt, one `ingest:` commit.
- `app/ingest/adapters/folder.py`, `email.py` — source normalisers. They call
  `commit_inbox_item` and own no other vault write.
- `app/ingest/document_archive.py` — Phase 3 slice 1: archives one unlocked
  PDF outside the vault, with checksum quarantine and a redacted receipt.

**Triage, proposals and approval**

- `app/inbox.py` — reads `00-inbox/active/` and owns `split_front_matter` and
  the no-follow lifecycle-directory and file guards reused by the Board.
- `app/classifier.py` — deterministic rules from `_system/classifier/rules.yaml`.
- `app/destinations.py` — canonical, server-derived destinations.
- `app/outbox.py` — proposals: the single curated-content write path.
- `app/proposal_identity.py`, `app/review_tokens.py` — collision-safe ids and
  the exact-byte review contract shared by approve, reject and registry delete.
- `app/git_transaction.py` — exact-path alternate-index commits with rollback.
- `app/action_receipts.py` — committed receipts that make spent ids
  non-actionable.
- `app/registry.py` — reference counts and outbox-governed product deletion.
- `app/lifecycle_*.py` — governed lifecycle repair and rollback through
  exact-byte proposals (see `docs/LIFECYCLE-REPAIR-IMPLEMENTATION.md`).

**Phase 3 public slices** (owner-authorized; scope in `docs/STATUS.md`)

- `app/phase3.py` — parser hook (redacted fields only), learned rules without
  Trash authority, encrypted-PDF unlock through a password store outside the
  vault, frozen exact-message cleanup with confirmation and recovery, and the
  mailbox screens. Mail workspaces are held in process memory over a synthetic
  provider; no live mailbox is connected.

**Console presentation**

- `app/board.py` — the read-only Board (see `docs/STATUS.md`, "Board screen").
- `app/console_errors.py` — the operator-facing error vocabulary.
- `app/console_render.py` — error fragment selection.
- `app/console_routing.py` — route and reader failure-contract metadata,
  checked structurally by `tests/test_console_invariants.py`.

**Deployment and owner administration**

- `app/auth.py`, `app/auth_web.py`, `app/auth_admin.py` — single-owner
  sign-in (password + TOTP, or a Touch ID passkey once one is set up in
  Settings), optional kept sessions ("Keep me signed in on this Mac": no idle
  or 12-hour limit, marked in a separate `kept_sessions` table so `sessions`
  keeps its original columns), middleware that runs before routing, and
  interactive enrollment/recovery. Recovery ends every session and removes
  every passkey.
- `app/passkeys.py` — WebAuthn checks for passkey setup and sign-in (wraps
  the `webauthn` library); `static/passkey.js` — Settings-side setup and
  removal.
- `app/deployment_health.py` — `/healthz` and authenticated `/readyz`.
- `app/rename.py`, `app/cutover*.py` — owner-run admin operations with their own
  one-commit rules.

**Tools** — `tools/gate3_audit.py` (Gate 3), `tools/public_repo_audit.py` and
`tools/run_gitleaks.sh` (publication gates), `tools/local_service.py`,
`tools/local_backup.py`, `tools/local_metadata.py` (local deployment and
backups; see `docs/LOCAL-OPERATIONS.md`).

## HTTP routes

Owner authentication (`app/auth_web.py`) runs before routing. It lets
`GET /healthz` and the three sign-in steps (`/login`,
`POST /login/passkey/options`, `POST /login/passkey`) through without a
session; every other path needs one. The middleware answers the sign-in,
passkey Settings and sign-out paths itself (no FastAPI route), after the same
Host, Origin and form-token checks as every other request.
With `ONEOS_AUTH_MODE=disabled` and `ONEOS_DEPLOYMENT` not `local`, it lets
every route through unauthenticated; use that only for loopback development
and tests.

| Method | Path | Effect |
|---|---|---|
| GET | `/` | Console shell with the entity sidebar |
| GET | `/blocks/pulse` | auto-refresh fragment; reads no registry |
| GET | `/board`, `/board/{entity}` | Board (read-only) |
| POST | `/board/{entity}/capture` | one redacted inbox receipt via ingest |
| GET | `/triage`, `/triage/{entity}` | triage list |
| POST | `/triage/{entity}/propose` | writes a classification proposal only |
| GET | `/outbox/{entity}`, `/outbox/{entity}/review/{proposal_id}` | review |
| POST | `/outbox/{entity}/approve`, `/outbox/{entity}/reject` | reviewed action, one commit |
| GET | `/mail/{entity}`, `/mail/{entity}/cleanup` | Phase 3 mailbox and Email cleanup screens |
| POST | `/mail/{entity}/cleanup/freeze` | freeze the exact visible message list |
| POST | `/mail/{entity}/cleanup/confirm` | confirmed Trash move of the frozen list (synthetic provider) |
| POST | `/mail/{entity}/cleanup/recover` | explicit recovery of selected messages |
| GET | `/registry/{entity}/products` | product registry |
| POST | `/registry/{entity}/product/delete-preview` | reference count and proposal |
| GET | `/registry/{entity}/product/review/{proposal_id}` | review a pending deletion or its receipt |
| POST | `/registry/{entity}/product/delete-execute` | reviewed deletion, one commit |
| GET, POST | `/login` | owner sign-in with password + code (optional "keep me signed in") |
| POST | `/login/passkey/options` | Touch ID sign-in options (no session; sign-in form token; 409 without a passkey, 429 while blocked) |
| POST | `/login/passkey` | Touch ID sign-in with the authenticator's answer (no session; counts toward the throttle) |
| POST | `/settings/passkey/begin` | fresh password + code check, then setup options (401 wrong check, 429 while blocked) |
| POST | `/settings/passkey/finish` | store the new passkey |
| POST | `/settings/passkey/remove` | remove one passkey; open sessions stay |
| POST | `/logout` | owner sign-out (this session) |
| POST | `/logout/everywhere` | end every session, kept ones included |
| GET | `/healthz`, `/readyz` | liveness; authenticated readiness |

`app/main.py` is the source of truth for this table, and `app/auth_web.py`
for the sign-in, passkey and sign-out rows.

## Configuration

| Variable | Read by | Purpose |
|---|---|---|
| `ONEOS_VAULT` | `app/config.py` | vault root; required |
| `ONEOS_AUTH_MODE` | `app/auth_web.py` | `required` (default) or `disabled`; under `ONEOS_DEPLOYMENT=local`, `disabled` yields 503 rather than open access |
| `ONEOS_DEPLOYMENT` | `app/auth_web.py` | `local` selects the production runtime |
| `ONEOS_AUTH_STATE_DIR` | `app/auth_web.py`, `app/auth_admin.py` | private authentication state |
| `ONEOS_ORIGIN` | `app/auth_web.py` | sole HTTPS browser origin; default `https://localhost:8443` |
| `ONEOS_BACKUP_STATUS_FILE` | `app/deployment_health.py` | read-only backup status for `/readyz` |
| `ONEOS_LIFECYCLE_JOURNAL`, `ONEOS_LIFECYCLE_ANCHOR` | `tools/gate3_audit.py` | external lifecycle evidence for the audit |

Compose inputs (`ONEOS_STATE_DIR`, `ONEOS_UID`, `ONEOS_GID`,
`ONEOS_GIT_NAME`, `ONEOS_GIT_EMAIL`) are documented in `deploy/README.md`.
Instance values never enter the repository.

## Single sources of truth

| Fact | Owner |
|---|---|
| Entities and their flags | `_system/entities.yaml` via `app/entities.py` |
| Modules, blocks, lifecycle shape | `_system/archetypes.yaml` via `app/vault.py` |
| Identifier length | `app/identifiers.py` |
| Front-matter required fields | `policy_enforcer.py` (vault); mirrored by `app/schema.py`, guarded by Gate 4 |
| Operator error wording | `app/console_errors.py` |
| Dependencies | `pyproject.toml` + `uv.lock` |
| Build state and evidence | `docs/STATUS.md` |
| Agent rules and invariants | `AGENTS.md` |
