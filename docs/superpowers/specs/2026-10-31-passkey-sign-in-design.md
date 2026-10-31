# Passkey (Touch ID) sign-in and "keep me signed in" — design

Date: 2026-10-31

Status: draft for owner approval.

## Why

The owner uses OneOS on their MacBook. Signing in today means typing a
password and a 6-digit authenticator code, and the owner is signed out after
30 minutes of not using OneOS and after 12 hours in any case
(`app/auth.py`: `IDLE_SECONDS = 1800`, `ABSOLUTE_SECONDS = 43200`). So they
re-type both several times a day.

The owner wants to sign in with their fingerprint (a passkey unlocked by
Touch ID) and to stay signed in on the Mac until they choose to sign out.

## Decisions the owner made (2026-10-31)

1. **Device.** The MacBook. OneOS only answers at `https://localhost:8443` on
   that Mac, so this is about one device.
2. **Passkey with Touch ID** replaces typing the password and code for normal
   sign-in.
3. **Stay signed in until signed out.** No idle or 12-hour limit for a
   session the owner chose to keep. It ends only when the owner presses
   **Sign out**, **Sign out everywhere**, or runs `recover`.
4. **Nothing else asks for the fingerprint.** Once signed in, approvals and
   every other action work as today, with no second fingerprint prompt.
5. **The `webauthn` Python library may be added** (with its supporting
   libraries, including `cryptography`) to check passkeys. Hand-written
   passkey cryptography is not acceptable.
6. **The screens as mocked up** (accepted 2026-10-31):
   - Sign-in page: a **Sign in with Touch ID** button, a **Keep me signed in
     on this Mac** checkbox (ticked by default), and a **Use password and
     code instead** link that shows the existing password + code form.
   - Settings (bottom-left sheet) → **Sign-in** section: status line
     ("Touch ID on this Mac: not set up" / "Touch ID set up · 31 Oct 2026"),
     **Set up Touch ID**, **Remove Touch ID**, **Sign out everywhere**, and
     the existing note that the password and authenticator change only in
     Terminal (`OneOS.command recover`).

## Goals

- Signing in on the Mac takes one touch of the sensor.
- After that, the owner stays signed in until they sign out.
- The owner can never be locked out by a lost or broken passkey.
- The sign-in stays at least as hard to fake as today from anywhere other
  than the owner's own unlocked Mac.

## Non-goals

- Signing in from the iPhone, iPad, or another computer (OneOS only answers
  on `localhost`).
- Asking for the fingerprint before approvals or other actions (decision 4).
- Changing the password or authenticator from the browser (stays Terminal
  only).
- Multi-user anything.

## Anti-goals (would make this a failure even if it works)

- Any way in without either a valid passkey with user verification (Touch ID)
  or the password + code.
- A passkey that keeps working after `recover`, or after **Remove Touch ID**.
- The owner locked out because a passkey was lost, the Mac was reset, or the
  browser's passkey store was cleared.
- A passkey private key, password, or authenticator secret stored anywhere
  but where it is today (the passkey's private key never leaves the Mac's
  passkey store; OneOS keeps only its public key).
- Anything written to the vault, or a network call outside this Mac.
- Weakening the existing throttle, CSRF, Origin, Host, or cookie protections.

## Constraints

- Stack is fixed (`AGENTS.md`): FastAPI, Jinja, HTMX, Alpine, vendored JS
  only, no build step. The only new runtime dependency is `webauthn`
  (decision 5), pinned in `uv.lock`. The browser side uses the built-in
  `navigator.credentials` API; no CDN, no new JS library.
- Credentials live only in the private authentication store
  (`<ONEOS_AUTH_STATE_DIR>/owner.sqlite3`), which the owner-run backup
  already copies.
- The relying party is exactly the configured origin `ONEOS_ORIGIN`
  (`https://localhost:8443`): RP ID `localhost`, origin checked exactly, as
  the middleware already checks Host and Origin.
- User verification is required on registration and on every passkey
  sign-in (that is what makes it "fingerprint", not just "this Mac").
- `.sensitive/` rules, the approval protocol, and the vault are untouched.
- The auth middleware (`app/auth_web.py`) stays the single gate for every
  request; no route is reachable without a session except the sign-in steps
  themselves.

## How it works (decided parts)

### Setting up Touch ID

From Settings → Sign-in → **Set up Touch ID** while signed in:

1. OneOS asks for the password and 6-digit code again (a fresh check, so an
   unlocked Mac left open for a moment cannot quietly add a passkey).
2. The browser asks for Touch ID and creates a passkey for OneOS on this Mac.
3. OneOS stores the passkey's public key, its id, and the date it was added.

More than one passkey may be stored (for example after a new Mac); Settings
lists them with their dates and each can be removed. *(My call.)*

### Signing in

- The sign-in page offers **Sign in with Touch ID** first when at least one
  passkey is stored; otherwise it shows the password + code form as today.
- Touch ID → the browser returns a signed answer to a one-time challenge →
  OneOS checks it against the stored public key → signed in.
- The challenge is single-use and expires after a few minutes, like the
  existing sign-in form token.
- Failed passkey attempts count toward the existing failed-sign-in throttle.
- **Use password and code instead** always works (the backup).

### Keep me signed in

- The checkbox (ticked by default) applies to both ways of signing in.
  *(My call: one rule for both.)*
- Ticked: the session has no idle or 12-hour limit. The browser cookie is
  refreshed as the owner uses OneOS so it does not expire on its own
  (browsers cap cookie lifetimes, so it must be renewed). The session ends
  only on **Sign out**, **Sign out everywhere**, or `recover`.
- Unticked: today's 30-minute idle and 12-hour limits apply.

### Removing and recovering

- **Remove Touch ID** deletes that passkey; the next sign-in uses another
  passkey or the password + code. It does not end sessions already open.
  *(My call: use **Sign out everywhere** for that.)*
- **Sign out everywhere** ends every session, including kept ones.
- `OneOS.command recover` (Terminal) sets a new password and authenticator,
  as today, and also deletes every passkey and every session.

## Left to the builder

- Where the passkey records live in `owner.sqlite3` (a new table) and how an
  existing install's database gains it without a write on read.
- How the sign-in page (built inline in `app/auth_web.py` today) carries the
  small passkey script before sign-in, given that other static files need a
  session.
- The exact challenge lifetime, cookie renewal interval, and how a kept
  session is marked.
- Handling of passkeys whose signature counter stays at zero (synced
  passkeys do this).
- Test layout, using synthetic passkeys generated in the tests, never a real
  device.
- Existing code that must learn the "kept" marker: `login()` purges sessions
  past the idle/12-hour limits, and `available()` validates every session
  row; neither may delete or reject a kept session.
- The fresh password + code check for setup reuses the authenticator's
  one-use-per-code rule, so a code just used to sign in is refused until the
  next code appears; the setup screen should say so.

## Tests the build must prove

- A valid passkey with user verification signs in; one without user
  verification, with the wrong origin or RP ID, a reused or expired
  challenge, an unknown credential, or a bad signature does not.
- Failed passkey attempts are throttled like failed password attempts.
- Setting up Touch ID requires a fresh, correct password + code; without it
  nothing is stored.
- A kept session survives past 30 minutes idle and 12 hours; an unticked one
  does not; **Sign out**, **Sign out everywhere**, and `recover` end kept
  sessions.
- **Remove Touch ID** and `recover` make the removed passkeys unusable.
- The password + code sign-in still works with or without passkeys stored.
- CSRF, Origin, Host, and cookie flags are unchanged for every new route.
- Nothing is written to the vault; the existing auth and doctor tests pass.

## Approval

Implementation waits for the owner to approve this document.
