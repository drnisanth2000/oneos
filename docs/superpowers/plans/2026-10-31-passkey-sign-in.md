# Passkey (Touch ID) sign-in and keep-me-signed-in — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner signs in on the Mac with a Touch ID passkey, stays signed in until they sign out, and keeps the password + code as a backup.

**Architecture:** `app/auth.py` (`AuthStore`) gains kept sessions, a passkey table and single-use challenges in the existing private `owner.sqlite3`, and uses the `webauthn` library to verify registrations and sign-ins. `app/auth_web.py` (`OwnerAuthMiddleware`, which already serves `/login` and `/logout` before routing) gains the passkey sign-in, setup, remove and sign-out-everywhere endpoints; no FastAPI route is added. The sign-in page carries a small inline script; Settings uses a vendored `static/passkey.js`.

**Tech Stack:** Python 3.12, FastAPI/Starlette middleware, SQLite (stdlib), `webauthn` (py_webauthn 2.x, owner-approved), `argon2-cffi`, `pyotp`, browser `navigator.credentials`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-passkey-sign-in-design.md` (owner-approved 2026-10-31).

## Global Constraints

- New runtime dependency: `webauthn>=2.2,<3` only (plus what it pulls in); add to `pyproject.toml` and lock with `uv lock`. No JS library, no CDN.
- RP ID = the hostname of `ONEOS_ORIGIN` (`localhost`); expected origin = `ONEOS_ORIGIN` exactly (`https://localhost:8443`); RP name `OneOS`; user id = the fixed bytes `b"oneos-owner"`, user name `owner`, display name `OneOS owner`.
- Registration: `resident_key=REQUIRED`, `user_verification=REQUIRED`, attestation `none`, algorithms ES256 (-7) and RS256 (-257), `exclude_credentials` = stored passkeys. Sign-in: discoverable (empty `allow_credentials`), `user_verification=REQUIRED`. Every verify call passes `require_user_verification=True`.
- Challenges: 32 random bytes, stored only as SHA-256 digests with a purpose (`"register"` or `"login"`) and an expiry of 300 s; single-use (deleted when verified, refused when expired or reused).
- A sign counter of 0 on both stored and received values is accepted (synced passkeys); otherwise the received counter must be greater than the stored one, else refuse.
- Every passkey sign-in failure counts toward the existing `throttle` row exactly like a failed password; a blocked throttle refuses passkey sign-in; success resets it.
- Kept sessions (`kept=1`) have no idle or absolute limit; unkept sessions keep `IDLE_SECONDS = 1800` and `ABSOLUTE_SECONDS = 43200`. Kept-session cookie `max_age = 34560000` (400 days), re-issued when the last issue is older than 86400 s. Cookie flags unchanged: `__Host-` prefix, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`.
- Schema: reads never write. A database from before this change (no `kept` column, no `passkeys`/`passkey_challenges` tables) must still pass `available()` and serve unkept sessions; the new column and tables are created inside the first write transaction that needs them (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN` guarded by a column check).
- `recover` (via `_save_owner`) deletes every session, every passkey and every challenge.
- CSRF, Origin, Host, body-size and cookie checks in `OwnerAuthMiddleware` apply unchanged to every new endpoint. Pre-sign-in endpoints use the existing login cookie nonce as their CSRF token; signed-in endpoints use the session CSRF token (`X-CSRF-Token`).
- Nothing touches the vault. No network call leaves the Mac.
- Copy (exact): "Sign in with Touch ID", "Keep me signed in on this Mac", "Use password and code instead", "Sign-in", "Touch ID on this Mac: not set up", "Touch ID set up · {d Mon YYYY}", "Set up Touch ID", "Remove Touch ID", "Sign out everywhere".
- Never write "/home/" followed by a path segment anywhere. Run tests as `env -u GIT_EDITOR uv run pytest ...`.

## Review Focus

1. **An existing install's database (no new column or tables)** — sign-in, `available()`, `session()` and doctor keep working, and nothing is written until a write needs the new schema. Pinned in Task 1.
2. **A passkey answer replayed, for the wrong origin/RP ID, without user verification, or after Remove/recover** — refused and counted by the throttle. Pinned in Tasks 3 and 4.
3. **The password + code backup** — still works with passkeys stored, with "Keep me signed in" ticked or not. Pinned in Tasks 2 and 4.
4. **A kept session across days** — survives idle and 12 h, its cookie is renewed, and Sign out / Sign out everywhere / recover end it. Pinned in Task 2.
5. **Setup on an unlocked Mac** — without a fresh, correct password + code no passkey can be added; a code already used for sign-in is refused with a clear message. Pinned in Tasks 3 and 4.

---

### Task 1: Dependency and schema

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `app/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces in `app/auth.py`:
  - `_ensure_schema(db) -> None` — inside a write transaction: adds `sessions.kept INTEGER NOT NULL DEFAULT 0` if missing; creates `passkeys(id TEXT PRIMARY KEY, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created REAL NOT NULL)` and `passkey_challenges(digest TEXT PRIMARY KEY, purpose TEXT NOT NULL, expires REAL NOT NULL)` if missing.
  - `_has_column(db, table, column) -> bool`, `_has_table(db, table) -> bool`.
  - `_validate_session` accepts the 5-column row `(token, csrf, created, seen, kept)` with `kept in (0, 1)`; `available()` reads `kept` only when the column exists, and validates `passkeys` rows (id base64url 16–1024 chars, `public_key` non-empty bytes, `sign_count` int ≥ 0, `created` finite ≥ 0) only when the table exists.
  - `_save_owner` (enroll and recover) calls `_ensure_schema` and deletes all `sessions`, `passkeys`, `passkey_challenges`.

- [ ] **Step 1: Failing tests** — `test_old_schema_database_still_signs_in_and_writes_nothing_on_read` (build a DB with today's three tables only; `available()`, `session()` on a real token, and `login()` work; after `available()` the file bytes are unchanged; after `login()` the `kept` column exists); `test_recover_deletes_passkeys_challenges_and_sessions`; `test_invalid_passkey_row_makes_store_unavailable` (parametrise: empty key, negative counter, NaN created); `test_webauthn_is_importable` (`import webauthn`).
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_auth.py` — new tests fail.
- [ ] **Step 3: Implement**; add `"webauthn>=2.2,<3"` to dependencies and run `uv lock`.
- [ ] **Step 4: Run** `tests/test_auth.py tests/test_local_service.py -k "auth or diagnostic"` — pass (doctor's authentication check must still read an upgraded DB).
- [ ] **Step 5: Commit** — `Auth: schema for kept sessions and passkeys`.

---

### Task 2: Kept sessions

**Files:**
- Modify: `app/auth.py`, `app/auth_web.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: Task 1 schema.
- Produces:
  - `AuthStore.login(password, code, *, keep: bool = False, now=None) -> str | None` — creates the session with `kept=int(keep)`; its purge deletes only expired **unkept** sessions.
  - `@dataclass(frozen=True) class SessionInfo: csrf: str; kept: bool` and `AuthStore.session_info(token, *, now=None) -> SessionInfo | None` — kept sessions skip the idle/absolute checks; `session()` keeps its signature and returns `session_info(...).csrf` or `None`.
  - `AuthStore.logout_everywhere() -> None` — deletes every session.
  - Middleware: `POST /login` reads an optional `keep` field (`"1"` = kept; the form's checkbox is ticked by default); a kept login sets the cookie with `max_age=34560000`; a kept session's cookie is re-issued on a response when the cookie's issue time is older than 86400 s (track issue time in a second `__Host-oneos-issued` cookie or the session row — builder's choice); `POST /logout/everywhere` (signed-in, CSRF) calls `logout_everywhere()` and redirects to `/login` with `HX-Redirect`.

- [ ] **Step 1: Failing tests** — `test_kept_session_outlives_idle_and_absolute_limits` (`now` +1 h idle and +13 h absolute → still valid); `test_unkept_session_limits_unchanged`; `test_login_purge_keeps_kept_sessions`; `test_kept_login_sets_long_cookie_and_renews_it` (TestClient: `max_age` 34560000; a request after +1 day re-issues it); `test_logout_and_logout_everywhere_end_kept_sessions`; `test_logout_everywhere_requires_csrf`.
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_auth.py` — pass, including every existing test unchanged.
- [ ] **Step 5: Commit** — `Auth: keep me signed in until sign out`.

---

### Task 3: Passkey registration and verification in the store

**Files:**
- Create: `app/passkeys.py` (thin wrapper over `webauthn`: options and verification only; no SQL)
- Modify: `app/auth.py` (challenge and passkey rows, throttle accounting)
- Create: `tests/passkey_fake.py` (a synthetic authenticator: makes an EC P-256 key with `cryptography`, builds `none` attestation and assertions for a given challenge, origin and RP ID, with flags UP/UV settable)
- Test: `tests/test_passkeys.py`

**Interfaces:**
- Produces:
  - `app/passkeys.py`: `rp_from_origin(origin: str) -> tuple[str, str]` (RP ID, expected origin); `registration_options(rp_id: str, challenge: bytes, exclude: list[bytes]) -> str` (JSON); `verify_registration(response_json: str, challenge: bytes, rp_id: str, origin: str) -> tuple[bytes, bytes, int]` (credential id, public key, sign count); `authentication_options(rp_id: str, challenge: bytes) -> str`; `verify_authentication(response_json: str, challenge: bytes, rp_id: str, origin: str, public_key: bytes, sign_count: int) -> int` (new sign count). All raise `PasskeyRejected(ValueError)` on any verification failure.
  - `AuthStore.begin_registration(password, code, *, origin, now=None) -> str | None` — the same password + code check as `login` (counter advance, throttle) without creating a session; on success stores a `"register"` challenge and returns options JSON; `None` on failure.
  - `AuthStore.finish_registration(response_json, *, origin, now=None) -> bool` — consumes a live `"register"` challenge (found by the response's `clientDataJSON.challenge`), verifies, stores the passkey (`created=now`); `False` on any failure, never raises for bad input.
  - `AuthStore.begin_login(*, origin, now=None) -> str | None` — `None` if no passkey is stored or the throttle blocks; else stores a `"login"` challenge and returns options JSON.
  - `AuthStore.finish_login(response_json, *, origin, keep: bool, now=None) -> str | None` — consumes the challenge, looks up the credential, verifies with UV, checks the counter rule, updates `sign_count`, resets the throttle, creates a session (`kept=int(keep)`) and returns its token; any failure increments the throttle and returns `None`.
  - `AuthStore.passkeys() -> list[tuple[str, float]]` (id, created) and `AuthStore.remove_passkey(credential_id: str) -> bool`.
  - Expired challenges are deleted inside any write transaction that touches challenges.

- [ ] **Step 1: Failing tests** (all with the fake authenticator, origin `https://localhost:8443`): register then sign in → token; sign in refused for: UV flag off, wrong origin, wrong RP ID, reused challenge, expired challenge (`now` +301 s), unknown credential, tampered signature, counter not increasing (stored 5, received 5), passkey removed, after `recover`; each refusal increments `throttle.failures`; five refusals block both passkey and password sign-in; zero counters accepted; `begin_registration` refused with a wrong code, with a code already used for `login` in the same 30 s window, and while blocked — and stores no challenge then; `begin_login` returns `None` with no passkeys; `finish_registration` refuses a `"login"` challenge and vice versa.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_passkeys.py` — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_passkeys.py tests/test_auth.py` — pass.
- [ ] **Step 5: Commit** — `Auth: passkey registration and sign-in in the store`.

---

### Task 4: Passkey endpoints in the auth middleware

**Files:**
- Modify: `app/auth_web.py`
- Create: `static/passkey.js` (vendored, no dependencies: base64url ⇄ ArrayBuffer helpers, `register()` and `remove()` for Settings)
- Test: `tests/test_passkeys.py`

**Interfaces:**
- Consumes: Task 3 store methods; Task 2 kept sessions.
- Produces (all JSON bodies `application/json`, all through the existing Origin/Host/size checks):
  - Pre-sign-in (CSRF = login cookie nonce in `X-CSRF-Token`): `POST /login/passkey/options` → `200` options JSON or `409 {"error":"no-passkey"}`; `POST /login/passkey` body `{"credential": {...}, "keep": true|false}` → `200 {"redirect":"/"}` with the session cookie (kept rules from Task 2) or `401 {"error":"failed"}`.
  - Signed-in (session CSRF): `POST /settings/passkey/begin` form `password`, `code` → options JSON or `401 {"error":"check"}` with the message text "Password or code not accepted. If you just used this code to sign in, wait for the next one."; `POST /settings/passkey/finish` JSON credential → `200 {"ok":true}` or `400 {"error":"failed"}`; `POST /settings/passkey/remove` form `id` → `303` back to the referring page (or `HX-Redirect`).
  - `login` path handling extends from `== "/login"` to the set `{"/login", "/login/passkey/options", "/login/passkey"}`; everything else still requires a session.
  - The sign-in page (still built inline, no external resources except one inline `<script>`): when `store.passkeys()` is non-empty it shows the **Sign in with Touch ID** button, the **Keep me signed in on this Mac** checkbox (checked), and a **Use password and code instead** link that reveals the existing form (which also gets the checkbox as `keep`); otherwise the existing form plus the checkbox. Without JavaScript the password form remains usable.
  - `request.state.passkeys` = `store.passkeys()` for signed-in requests (for the Settings template in Task 5).

- [ ] **Step 1: Failing tests** (TestClient at `https://localhost:8443`, fake authenticator): full setup flow (begin with fresh code → finish) then sign out → sign in with passkey → `/` loads; kept vs unkept cookie from the passkey path; each endpoint refuses a missing/wrong CSRF token (403), a foreign Origin (403), and an oversized body (413); `/login/passkey/options` returns 409 with no passkeys; `/settings/passkey/*` require a session (401); remove → the passkey no longer signs in; the sign-in page contains the three copy strings only when a passkey exists and always keeps the password form; no static file is needed before sign-in (the page has no `src=` or `href=` to `/static`).
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_passkeys.py tests/test_auth.py tests/test_app.py` — pass.
- [ ] **Step 5: Commit** — `Auth: passkey sign-in, setup and removal endpoints`.

---

### Task 5: Settings → Sign-in

**Files:**
- Modify: `templates/_sidebar.html` (the "Password & two-factor" section becomes "Sign-in"; the stale Backup note "once a day while the Mac is awake" becomes "only when you run it"), `static/app.css`, `templates/_head.html` (load `static/passkey.js` with `defer` on signed-in pages)
- Test: `tests/test_passkeys.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `request.state.passkeys`, `/settings/passkey/*`, `/logout/everywhere` (Tasks 2 and 4).
- Produces: the Sign-in section — status line ("Touch ID on this Mac: not set up" or "Touch ID set up · {d Mon YYYY}" per passkey), **Set up Touch ID** (opens a small form for password and 6-digit code, then calls the browser), **Remove Touch ID** per passkey, **Sign out everywhere**, and the existing Terminal `recover` note (adding "which also removes Touch ID").

- [ ] **Step 1: Failing tests** — Settings renders "not set up" with no passkeys and one "Touch ID set up · 31 Oct 2026" line per stored passkey (`created` at that date); Remove and Sign out everywhere forms post to the right endpoints with the CSRF token; the Backup note no longer says "once a day".
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement**; light and dark, 375 px without sideways scroll.
- [ ] **Step 4: Run** the full suite `env -u GIT_EDITOR uv run pytest -q --ignore=tests/test_local_backup.py --ignore=tests/test_local_service.py` and `uv run python -m tools.public_repo_audit --repo .` (CLEAN).
- [ ] **Step 5: Commit** — `Settings: Sign-in section with Touch ID`.

---

## Before merge

- Private integration gate from `BUILD.md` §3 (vault untouched; `public_repo_audit --vault … --history`).
- Owner check on the running install after update: Set up Touch ID with a real fingerprint, Sign out, Sign in with Touch ID, confirm still signed in after 30 minutes away, Remove Touch ID, sign in with password + code. A real Touch ID prompt cannot be automated.

## Rulings in this plan

1. Passkey endpoints live in the auth middleware beside `/login` and `/logout`, not as FastAPI routes, so the single auth gate stays the only pre-sign-in surface.
2. The user id is the fixed `b"oneos-owner"` (one owner), so a re-registered passkey on the same Mac replaces the old one in the Mac's passkey store.
3. Challenges are kept server-side as digests rather than in a cookie, so they are single-use.
4. The stale "once a day" Backup note in Settings is corrected in Task 5 because the same section is being edited.
