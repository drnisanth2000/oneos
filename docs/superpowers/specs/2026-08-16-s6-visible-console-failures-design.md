# S6 — Visible Console Failures

**Status:** Review Pending — not approved for implementation planning

**Base:** `origin/main` at `3585938` (merged S5 and the S1-S5 documentation
reconciliation). Public baseline: 603 tests. Private baseline: 37 tests.

**History:** this document is a rewrite. Two independent reviews of the previous
structure each returned Critical findings, and the recurring cause was
architectural rather than local. All prior findings are treated here as claims
re-verified against current code, not as settled fixes.

## Objective

Every refusal the Command Center already makes must reach the operator as a
specific, safe, actionable message. No route may swallow a failure, return a raw
server fault, or render a screen that hides the condition it is protecting
against.

S6 changes presentation. It does not change when an operation is refused, what
is validated, or what is committed.

Spec authority: `oneos-spec.md` §10.1 item 6 requires stale, invalid,
cross-scope, and Git failures to be visible. §5.2 places the mapping inside the
Command Center boundary. §6 governs the HTMX/Alpine split.

---

## 1. The architectural problem

OneOS narrows exception types at every service boundary. This is deliberate and
correct — it is what keeps `git_transaction` internals from becoming part of the
outbox contract. But narrowing destroys outcome information that is
safety-relevant, and the previous design treated each instance as a separate
bug. Four are reachable today:

| Boundary | Narrowing | Consequence if unrecovered |
|---|---|---|
| `app/outbox.py:409` | every `GitTransactionError` → `OutboxTransactionError` | a committed action reports "rolled back, nothing changed, retry" |
| `app/registry.py:350` | same, → `RegistryTransactionError` | same |
| `app/main.py:53-57` | `EntitySelectionError` → `HTTPException(404)` | an unknown entity reports a malformed form |
| `app/outbox.py:342-343` | `DestinationRegistryError` → `OutboxDestinationError` | a broken `archetypes.yaml` tells the operator to recreate every proposal |

The first of these is a Gate 2 break: an operator told to retry a committed
approval produces a second commit for one reviewed action.

S6 therefore defines **one** recovery rule rather than four exceptions to a rule.

### Rule 1 — Outcome recovery

An exception is described by resolving an **outcome** across its cause chain,
not by matching a single class.

The resolver collects one candidate description per link, then returns the
candidate with the highest precedence:

| Tier | Name | Meaning |
|---|---|---|
| 0 | `committed` | the vault changed durably; retrying would duplicate it |
| 1 | `recovery` | vault state is indeterminate; a human must resolve it |
| 2 | `integrity` | scope, redirection, or registry-validity condition |
| 3 | `refusal` | the operation did not happen; the vault is unchanged |
| 4 | `unknown` | nothing proven |

Ties resolve to the **innermost** candidate, because an outer wrapper is by
construction a generalization of what it wraps.

Three constraints make the walk safe:

- **Allowlisted.** Only classes explicitly declared chain-bearing are traversed.
  A class not on the allowlist is described by itself, and its cause is never
  read. The allowlist is `OutboxTransactionError`, `RegistryTransactionError`,
  `OutboxDestinationError`, and `GitTransactionFailure`. Membership is by
  **exact class identity**, with one stated exception: the two private
  subclasses of `GitTransactionFailure` (`app/git_transaction.py:44,50`) are
  members too, and a test over `GitTransactionFailure.__subclasses__()`
  transitively fails if a new one is added without being listed. Silent
  membership by `isinstance` would let a future subclass stop the walk and
  re-hide a committed outcome — the Gate 2 break Rule 2 exists to prevent.
- **`__cause__` only, never `__context__`.** Python sets `__context__`
  automatically for any exception raised while handling another, so traversing
  it would surface unrelated in-flight failures. Only explicit `raise ... from`
  expresses intent.
- **Bounded, failing closed.** Depth 4. Exceeding it yields `E-UNKNOWN` at
  `stop` / `committed = unknown`, never the outermost description. An overrun
  chain has not been proven free of a committed outcome, and returning the outer
  wrapper could report `retry`/`no` over a deeper `committed`. Overflow fails
  closed for the same reason a closed family does.

`GitTransactionFailure` is allowlisted because S5 normalizes every
in-transaction failure into it after rollback (`app/git_transaction.py:418-419`)
while preserving the original — `raise transaction_error from transaction_cause`
at `:459`. Without traversing it, **any** condition detected inside a
transaction, including a redirected reviewed path, resolves to `E-GIT` at
`retry`. Allowlisting recovers the real outcome and requires no change to S5
code.

The resolver never reads exception text, `args`, or attributes. Only the class
identity of each link selects a curated description.

### Rule 2 — Outcome-sensitive families fail closed

MRO inheritance is convenient and, for `GitTransactionError`, dangerous: a
future subclass would silently inherit `E-GIT` at `refusal`/`retry`/`no`. Both
of the previous design's structural tests would still pass. That is the Gate 2
break reachable by adding one class and touching nothing else.

`GitTransactionError` is therefore declared a **closed family**. Within a closed
family, MRO inheritance does not apply: a subclass without its own entry
resolves to `E-UNKNOWN` at `stop`/`unknown`, and a test walks
`__subclasses__()` transitively to name it.

Failing closed is the correct default precisely because the missing information
is whether anything was committed.

---

## 2. The error model

### `app/console_errors.py`

```text
ConsoleError(code, tier, severity, message, retry, committed)
describe(exc) -> ConsoleError
```

`ConsoleError` is frozen. `tier` is one of the five above. `severity` is
`refusal` or `attention`. `retry` is `retry`, `reload`, `recreate`, `stop`, or
`none`. `committed` is `no`, `yes`, or `unknown`.

Two structural invariants, both asserted:

- `severity = refusal` implies `committed = no`.
- `tier = committed` implies `committed = yes` and `retry = stop`;
  `tier = recovery` implies `committed = unknown` and `retry = stop`.

The table keys on imported exception classes, not dotted strings: strings are
not refactor-safe and a renamed class would degrade silently to `E-UNKNOWN`.
The boundary is one-way and asserted — no application service, route helper, or
registry module imports `console_errors` **or** `console_render`.

### Codes

| Code | Tier | Severity | Retry | Committed | Page status |
|---|---|---|---|---|---|
| `E-COMMITTED` | committed | attention | stop | yes | 500 |
| `E-RECOVER` | recovery | attention | stop | unknown | 500 |
| `E-CONFIG` | integrity | attention | none | no | 500 |
| `E-SCOPE` | integrity | refusal | none | no | 404 |
| `E-TAMPER` | integrity | attention | stop | no | 409 |
| `E-STALE` | refusal | refusal | recreate | no | 409 |
| `E-MISSING` | refusal | refusal | recreate | no | 409 |
| `E-INVALID` | refusal | refusal | recreate | no | 422 |
| `E-UNREADABLE` | refusal | attention | stop | no | 422 |
| `E-DEST` | refusal | refusal | recreate | no | 422 |
| `E-BUSY` | refusal | refusal | retry | no | 409 |
| `E-CONFLICT` | refusal | refusal | reload | no | 409 |
| `E-GIT` | refusal | refusal | retry | no | 500 |
| `E-REGISTRY` | refusal | refusal | reload | no | 422 |
| `E-REQUEST` | refusal | refusal | recreate | no | 422 |
| `E-ENTITY` | refusal | refusal | none | no | 404 |
| `E-INGEST` | refusal | refusal | none | no | 500 |
| `E-ADMIN` | refusal | refusal | none | no | 500 |
| `E-UNKNOWN` | unknown | attention | stop | unknown | 500 |

`E-TAMPER` is new. The previous design collapsed redirection and
integrity findings into `E-SCOPE` and `E-CONFLICT`, telling an operator whose
vault may have been tampered with to "reload and review again."
`CrossScopeError` and `ReviewedStateConflict` are each raised for two distinct
conditions, and the redirection family is a security finding:

| Condition | Example sites | Code |
|---|---|---|
| a path resolved outside the bound entity | `app/outbox.py:325` | `E-SCOPE` |
| a path is redirected, not a regular file, or type-swapped | `app/outbox.py:99,122,140`, `app/git_transaction.py:172,192,198,228` | `E-TAMPER` |
| the reviewed file genuinely changed underneath | `app/git_transaction.py` conflict sites | `E-CONFLICT` |

Distinguishing these requires the services to raise distinguishable types. The
redirection sites live under two different bases, so the refinement is two
subclasses, both described as `E-TAMPER`:

| Base | New subclass | Sites |
|---|---|---|
| `CrossScopeError` | `RedirectedPathError` | `app/outbox.py:99,102,107,109,122,125,140,144`, `app/inbox.py:46,57`, `app/scope.py:41,64` |
| `ReviewedStateConflict` | `ReviewedPathIntegrityError` | `app/git_transaction.py:172,192,198,228,816,993,995,1001,1009` |

The earlier draft of this table listed four sites per base and missed twelve.
The omissions were not incidental: `app/git_transaction.py:993,995,1001` are
**verbatim duplicates** of `:190,192,198`, differing only in that the mutation
half of the transaction noticed the condition rather than the capture half.
Under the short table, one physical condition — a symlinked reviewed path —
produced "Do not retry. Inspect the vault" or "Reload and review again"
depending purely on timing.

A hand-written enumeration cannot report the sites it omits, which is the
`BUILD.md` §5 failure this project keeps hitting. So the inventory is closed by
test rather than by review: **every `raise CrossScopeError` and every `raise
ReviewedStateConflict` site under `app/` must appear in an explicit
classification table, and the test enumerates the raise sites from source.** A
new site fails the test until it is classified.

Both are **type refinements, not behavior changes**: the same inputs are refused
at the same points, and every existing `except CrossScopeError` or `except
GitTransactionError` continues to catch them. Each converted site is
characterized by test before conversion.

`ReviewedPathIntegrityError` must be an **exact** closed-family entry, not an
inherited one. It is raised inside `_execute_locked`, so it reaches a route
wrapped in `GitTransactionFailure` inside `OutboxTransactionError` — a depth-3
chain that the allowlist traverses and precedence resolves to `E-TAMPER` at
tier `integrity`, outranking the wrapper's `refusal`.

### Class mapping

The table keys on these classes. `exact` means the entry applies only to that
class; `mro` means subclasses without their own entry inherit it. Every member
of the closed `GitTransactionError` family is `exact` by Rule 2.

| Class | Code | Match |
|---|---|---|
| `git_transaction.GitTransactionCommittedError` | `E-COMMITTED` | exact |
| `git_transaction.GitTransactionRecoveryError` | `E-RECOVER` | exact |
| `git_transaction.ReviewedPathIntegrityError` | `E-TAMPER` | exact |
| `git_transaction.ReviewedStateConflict` | `E-CONFLICT` | exact |
| `git_transaction.VaultBusyError` | `E-BUSY` | exact |
| `git_transaction.GitTransactionFailure` | `E-GIT` | exact |
| `git_transaction.GitTransactionError` | `E-GIT` | exact |
| `git_transaction._ApprovalLockCleanupFailure` | `E-GIT` | exact |
| `git_transaction._ReviewedIndexOwnershipConflict` | `E-CONFLICT` | exact |
| `scope.RedirectedPathError` | `E-TAMPER` | mro |
| `scope.CrossScopeError` | `E-SCOPE` | mro |
| `outbox.OutboxScopeError` | `E-SCOPE` | mro |
| `outbox.StaleProposalSource` | `E-STALE` | exact |
| `outbox.MissingProposalSource` | `E-MISSING` | exact |
| `outbox.ProposalFreshnessError` | `E-STALE` | exact |
| `outbox.OutboxTransactionError` | `E-GIT` | exact |
| `outbox.OutboxDestinationError` | `E-INVALID` | mro |
| `outbox.OutboxError` | `E-INVALID` | mro |
| `proposal_identity.ProposalIdentityError` | `E-INVALID` | mro |
| `destinations.UnsafeDestinationPath` | `E-TAMPER` | exact |
| `destinations.DestinationError` | `E-DEST` | mro |
| `vault.DestinationRegistryError` | `E-CONFIG` | mro |
| `entities.SystemRegistryPathError` | `E-TAMPER` | exact |
| `entities.RecipientConfigurationError` | `E-CONFIG` | exact |
| `entities.EntityManifestError` | `E-CONFIG` | mro |
| `entities.EntitySelectionError` | `E-ENTITY` | mro |
| `registry.RegistryTransactionError` | `E-GIT` | exact |
| `registry.RegistryError` | `E-REGISTRY` | mro |
| `ingest.base.IngestError` and all subclasses | `E-INGEST` | mro |
| `rename.RenameError` | `E-ADMIN` | mro |
| `fastapi.RequestValidationError` | `E-REQUEST` | exact |

`HTTPException` is deliberately **absent** from this table and from the
traversal allowlist. `fastapi.HTTPException` subclasses
`starlette.exceptions.HTTPException`, and the framework raises that same class
for unmatched URLs, wrong methods, and `StaticFiles` misses — so a mapping would
return 422 with copy about a form for a missing vendor script, which Rule 6
explicitly forbids. After Rule 6 removes the conversion at `app/main.py:57`, no
application code raises `HTTPException` at all, leaving the row with no producer
and only that harmful effect. Starlette's and FastAPI's own handlers are left
intact. `RequestValidationError` alone carries `E-REQUEST`, and
`EntitySelectionError` reaches its own handler as `E-ENTITY`.

`UnsafeDestinationPath` and `SystemRegistryPathError` are `E-TAMPER` rather than
`E-DEST`/`E-CONFIG` because both fire on redirected or non-canonical paths,
which is an integrity condition rather than an ordinary resolution failure. Both
are `exact` so their siblings keep the ordinary code.

Two refinements are required before that is true:

**`UnsafeDestinationPath` must stop covering plain absence.**
`app/destinations.py:70-75` raises it for two conditions in one statement —
`resolved != lexical` (redirection) **or** `not lexical.is_dir()` (the directory
simply is not there). A module the flags require but the disk lacks is a
first-class expected state in this system: `app/vault.py:41-43` models it as
`Module.missing`, `check_v2` reports it as E4, and `BUILD.md:134-143` makes it a
standing regression. Today such an item renders "invalid destination — needs a
call" (`templates/triage.html:70`). Under an unsplit `E-TAMPER` the operator
would instead be told to go hunting for tampering because a scaffolding step was
skipped. Crying wolf here costs more than under-classifying, because the code's
entire value is that it is rare. Absence raises the ordinary `DestinationError`;
only `resolved != lexical` raises `UnsafeDestinationPath`.

**`SystemRegistryPathError` must be reachable.** `app/scope.py:60-64` converts
it to a plain `CrossScopeError`, so on the service path it describes as
`E-SCOPE` and survives as `E-TAMPER` only through `Vault.system_path`
(`app/vault.py:69-70`) — one condition, two codes, decided by caller. That
conversion site joins the `RedirectedPathError` list below.

`E-UNREADABLE` is produced by the projection, not by a class mapping — no
service raises it.

### Exact message text

These strings are the contract.

| Code | Message |
|---|---|
| `E-COMMITTED` | The commit succeeded; only the cleanup afterwards failed. Do not retry — retrying would commit this action twice. Inspect vault state with git status. |
| `E-RECOVER` | Rollback was blocked by a change made at the same time. Do not retry. Inspect vault state with git status and resolve it before continuing. |
| `E-CONFIG` | The vault registries could not be read. The Console cannot operate here until they are valid. |
| `E-SCOPE` | Refused: the request resolved outside the selected entity. |
| `E-TAMPER` | Refused: a file involved in this action is not where it should be. Do not retry. Inspect the vault before continuing. |
| `E-STALE` | Approval refused: source changed since this proposal was created. Create a fresh proposal. |
| `E-MISSING` | Approval refused: source is missing. Restore it or reject the proposal. |
| `E-INVALID` | This proposal record is not valid and cannot be approved. Create a new proposal. |
| `E-UNREADABLE` | A file in the outbox could not be read as a proposal. Creating another proposal will not clear it — repair or remove it outside the Console. |
| `E-DEST` | The destination could not be resolved from the registries. Re-classify this item. |
| `E-BUSY` | Another approval is in progress. Nothing was changed. Try again in a moment. |
| `E-CONFLICT` | The reviewed files changed since this proposal was previewed. Reload and review again. |
| `E-GIT` | The commit failed and was rolled back. Nothing was changed. |
| `E-REGISTRY` | The registry operation was refused. Review the impact report and try again. |
| `E-REQUEST` | The form could not be read. Reload the screen and try again. |
| `E-ENTITY` | That entity is not in the manifest. |
| `E-INGEST` | Intake failed. Nothing was written to the vault. |
| `E-ADMIN` | The administrative operation was refused. |
| `E-UNKNOWN` | An unexpected error was not handled. Inspect vault state with git status before continuing. |

`E-STALE` and `E-MISSING` are **byte-identical to the strings already asserted**
in `tests/test_app.py:434` and `:439`. The previous design reworded them and
thereby forced an S1-S5 test edit it had itself defined as a scope breach.

Wording invariants, asserted by test: the `E-COMMITTED` message must contain
both an affirmative statement that the commit succeeded and an explicit
instruction not to retry; no `attention` message may invite a retry.

Retry guidance is **text only**. S6 renders no retry control, because an
affordance would invite exactly the second commit `E-COMMITTED` prevents.

---

## 3. Rule 3 — Read projection versus mutation authority

`load_proposals` raises on the first malformed record and abandons the listing,
so one unreadable file hides every valid proposal. `preview_diff` compounds it:
rendering any valid row calls `get_proposal`, which re-enters the same strict
loader, so the alert renderer fails on the error it is reporting.

S6 leaves every mutation path untouched. `approve`, `reject`, `get_proposal`,
and `load_proposals` keep their exact current behavior and fail-closed
semantics.

### The projection

```text
project_outbox(scope) -> OutboxListing
OutboxListing(rows, blocked)
OutboxRow(kind, proposal | None, diff | None, error | None)
```

`kind` is `readable`, `unreadable`, or `skipped`. `blocked` is true when any row
is `unreadable`.

The strict loader's per-file body is extracted into three shared helpers —
`_read_record(path)`, `_validate_record(scope, path, record)`, and
`_render_diff(scope, proposal)` — and the strict loader and the projection both
call them. The validation logic moves; it does not change or fork.

`_render_diff` takes an **already-validated** record and performs only the
`difflib` work. `preview_diff` keeps its public contract unchanged: it performs
its strict reload first, then delegates to `_render_diff`. The projection calls
`_render_diff` directly on the record it has just validated.

`project_outbox` never calls `get_proposal`, `load_proposals`, or
`preview_diff`. That is the whole point of the extraction, and it is asserted by
test.

### Delete proposals are skipped, exactly as today

Registry delete proposals live in the same `outbox/` directory the projection
globs (`app/registry.py:200-245`). The strict loader tolerates them with
`if action == "delete": continue` (`app/outbox.py:281-282`), placed after the
identity check.

The projection preserves that skip precisely, yielding `kind = skipped`. This
matters because `app/main.py:211` calls `propose_delete` on **every**
delete-preview click, so an abandoned preview leaves a valid delete proposal
behind permanently. A well-formed delete record must therefore render nothing,
count as nothing, and block nothing — exactly its behavior today.

Only a **malformed** delete record — one failing the read or identity check that
precedes the skip — is unreadable. The skip follows validation; it does not
bypass it.

### S4 revalidation is preserved

S4 required a stored record to be revalidated before its diff is shown, and
achieved it by re-reading the whole listing — which is why one bad file poisons
every row. The projection performs the same revalidation on the same record
without the global loop. What is removed is the global re-entry, not a check.

The displayed diff carries **no approval authority**. Approval revalidates from
scratch through the untouched strict path, so a row rendering successfully is
never evidence that it will approve. That sentence is honest about the
implementation and, read carefully, concedes something S6 must not paper over:
**nothing binds an approval to the bytes the operator reviewed.** See §11.

### An unreadable record blocks the whole listing, visibly

Because `approve` and `reject` resolve through the untouched strict loader, a
single malformed record makes **every** action in that entity fail — including
actions on proposals that are perfectly valid. The failure surfaces as
`E-INVALID`, "create a new proposal", which cannot possibly resolve the
condition.

Rendering approve and reject controls beside valid rows in that state would be
worse than today's blank screen. Today the operator sees a hard failure and
knows something is wrong. A listing full of buttons that are all silently
guaranteed to fail is a legibility regression inside the step whose purpose is
legibility.

So the projection is **all-or-nothing**:

- When `blocked` is false, valid rows render with their normal approve and
  reject controls.
- When `blocked` is true, valid rows still render — with their id, destination,
  and diff, so the operator can see what is pending — but **no classification
  controls at all**, and the listing carries one `E-UNREADABLE` notice stating
  that a file in the outbox cannot be read and that no proposal in this entity
  can be approved or rejected until it is repaired or removed outside the
  Console.

This states the coupling instead of hiding it behind controls that cannot work,
and it does so without weakening a single check: the strict loader still refuses
everything, exactly as it does today. The projection only stops lying about it.

An unreadable row itself carries no filename. Per Rule 9 the filename is
attacker-controlled text and is not echoed; the row is generic.

### The taxonomy stays out of the service

The projection is a service function, so it must not name a presentation code.
`OutboxRow` carries a **domain-neutral** kind plus the raw exception, and
`app/main.py` — the presentation composition root — selects `E-UNREADABLE` for
the unreadable kind and for the blocked-listing notice, and calls `describe()`
for anything else. No service module imports the taxonomy.

`E-UNREADABLE` is distinct from `E-INVALID` because the two demand opposite
actions. `E-INVALID` says the proposal you tried to approve is bad — create a
new one, and rejecting it clears it. `E-UNREADABLE` says a file in the outbox
cannot be parsed at all — creating another proposal does not remove it, and it
cannot be rejected through the Console. One code carrying both `recreate` and
`stop` would have told the operator to take an action that cannot resolve the
condition.

Withholding reject is deliberate. `reject` resolves through `get_proposal` and
the strict loader, so a malformed record cannot be rejected without a new
service contract that deletes a path the domain never validated. That is a
change to what the Console may destroy, not to how a refusal is shown, and it
is out of scope. The operator repairs or removes such a file outside the
Console; the blocked notice says so without naming it.

## 4. Rule 4 — HTMX configuration reaches every document

Returning 200 for a handled refusal is sound — the refusal is expected and the
body carries it. It is not sound for an unhandled exception, which must stay 500
so a defect is visible to tests and monitoring. But HTMX 2.0.4 does not swap
non-2xx by default, so a 500 leaves the operator staring at an unchanged screen.

The vendored bundle resolves this by configuration. Its default is
`[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`;
overriding the last entry to `swap:true` keeps `error:true`, still fires
`htmx:responseError`, and routes the swap through each element's declared
`hx-target`/`hx-swap`. No existing target is affected and no existing test
asserts a non-2xx HTMX response.

**Placement is the part the previous design got wrong.** There is no template
inheritance here: `shell.html`, `triage.html`, `outbox.html`, and
`registry.html` are four independent documents with duplicated heads. `shell.html`
is served only by `GET /`, which contains no HTMX mutation at all — so a
shell-only override would cover none of the screens S6 exists to fix, while a
shell-only test passed green.

S6 adds `templates/_head.html`, included by all four documents, carrying the
vendored script tags and:

```html
<meta name="htmx-config" content='{"responseHandling":[
  {"code":"204","swap":false},
  {"code":"[23]..","swap":true},
  {"code":"[45]..","swap":true,"error":true}]}'>
```

The meta form is required, not stylistic: every vendor script loads with
`defer`, so an inline statement would execute before `htmx` is defined. HTMX
reads this meta tag at `DOMContentLoaded`, independent of script order.

The test **enumerates every full-page route** and asserts the configuration in
each rendered response. Asserting it on one page is the BUILD.md §5 failure
mode inside the guard written to prevent it.

### The override must not swap framework error bodies

`{"code":"[45]..","swap":true}` matches every non-2xx, including the responses
Rule 6 deliberately keeps out of the taxonomy. An HTMX request to an unmatched
URL or with a wrong method would swap Starlette's raw `Not Found` or
`Method Not Allowed` body into `#outbox-list` or `#diff-{index}` — framework
text reaching the operator through the swap path, which §6 forbids through the
render path.

A `default` exception handler for `StarletteHTTPException` therefore renders any
framework status the taxonomy does not own as a described `E-UNKNOWN` fragment
when `HX-Request` is present, and leaves the framework's own plain response
untouched otherwise. The status is preserved in both cases; only the swapped
body is replaced. This keeps Rule 6's exclusion — those responses are still not
*mapped* to `E-REQUEST` — while closing the swap that the exclusion overlooked.

---

## 5. Rule 5 — Typed route handling only

Routes catch **declared domain families**, never bare `Exception`. A blanket
catch would launder programmer errors into 200 fragments, which is the opposite
of S6's purpose.

The global handler catches only what escapes a route, describes it, and returns
its page status — 500 for `E-UNKNOWN`. It never returns 200. A test asserts it
is not reached for any described error, so relying on it is a failure rather
than a silent default.

Two renderers share the one table, selected by the `HX-Request` header. Fragment
status follows **severity**, not code:

- `severity = refusal` → **200**. The refusal is expected and the body carries
  it.
- `severity = attention` → the code's **declared page status**. HTMX still swaps
  it under the `responseHandling` override, so the operator sees the message
  while monitoring sees an honest status.

A full page always returns the code's page status.

Keying on severity resolves `E-COMMITTED`, `E-RECOVER`, `E-CONFIG`, `E-TAMPER`,
`E-UNREADABLE`, and `E-UNKNOWN` uniformly instead of naming `E-UNKNOWN` alone
and leaving every other attention outcome undefined. It also guarantees the one
class of outcome that must not look routine never returns 200.

### The Gate 1 stopwatch must not count refusals

`templates/triage.html:130-136` increments the triage counter on
`htmx:afterRequest` when `e.detail.successful` is true. HTMX derives that from
the matched `responseHandling` entry's `error` flag, so today an uncaught 500
from a failed `propose` is not counted. Under Rule 5, `E-DEST`, `E-INVALID`, and
`E-REQUEST` are refusals returning **200**, and the counter would increment on a
proposal that was refused and wrote nothing — silently corrupting the spec §11
Gate 1 measurement that the whole Safety Foundation exists to make trustworthy.

The fix does **not** change any HTTP status. Refusals keep 200 and the S1-S5
HTTP contracts stay intact. Instead the counter stops inferring success from
transport: `propose` emits an `HX-Trigger` event **only after the proposal is
persisted**, and the stopwatch listens for that event rather than for
`htmx:afterRequest`. A success-only server signal cannot be produced by a
refusal, so the metric is truthful by construction rather than by a client-side
guard that a later edit could drop.

### Rule 6 — Framework wrapping is explicit

`entity_scope` stops converting `EntitySelectionError` into `HTTPException`. It
raises `EntitySelectionError`, and a dedicated handler describes it as
`E-ENTITY` at 404 — the same status the route returns today.

`E-REQUEST` covers **`RequestValidationError` only**. An earlier draft added
"`HTTPException` raised by application code" and then excluded the framework's
own instances in the next sentence — but they are the same class, with no
discriminator available at handler time, so the exclusion was unenforceable.
After this rule removes the conversion at `app/main.py:57`, no application code
raises `HTTPException` at all, which settles it: the class is absent from the
table and the allowlist, and Starlette's automatic 404, its 405, and
`StaticFiles` misses keep their own handlers untouched.

Their *bodies* still need handling once the `responseHandling` override makes
non-2xx swap; that is covered in Rule 4, and it replaces the swapped body
without mapping the status into the taxonomy.

### Route inventory

All eleven registered routes, the scope dependency, and the global handler.
Reading routes are listed even where currently silent.

| Site | Current | Required |
|---|---|---|
| `shell` | `Vault().bundles()` uncaught | `E-CONFIG` page |
| `pulse` | reads no vault state | unchanged |
| `triage_default` | `Vault().bundles()` uncaught | `E-CONFIG` page |
| `triage` | `except (DestinationError, DestinationRegistryError): destination = None`; `CrossScopeError` escapes the tuple entirely | per-row code and message; tuple extended to cover scope and tamper conditions |
| `propose` | uncaught | described alert into the existing `#diff-{index}` target |
| `outbox_screen` | strict loader; one bad record blanks the screen | the projection |
| `outbox_approve` | `except OutboxError: pass` — this **already swallows every transaction failure**, including committed outcomes, since `OutboxTransactionError` subclasses `OutboxError` | described inline |
| `outbox_reject` | `except OutboxError: pass` | described inline |
| `registry_products` | `products_for` uncaught; raises `yaml.YAMLError`, not `RegistryError` | `E-CONFIG` inline |
| `registry_delete_preview` | uncaught; `reference_count` raises `sqlite3.DatabaseError` and `yaml.YAMLError` | `E-CONFIG` inline |
| `registry_delete_execute` | unescaped f-string on **both** branches | template on both branches |
| `entity_scope` | `HTTPException(404)`, empty body | `EntitySelectionError` to its dedicated handler |
| global handler | absent | describes, returns 500 |

`E-CONFIG` renders **inline on a fragment request and as a page on a full-page
request**, like every other code. Surface follows the request, not the code; the
previous design's per-code surface column contradicted its own `triage` row.

### Boundary conversions

Registry-validity conditions currently surface as stdlib exceptions and would
reach the operator as "an unexpected error was not handled." These convert to
the existing `DestinationRegistryError`, which already means "a registry could
not be read as valid":

| Site | Raises today |
|---|---|
| `app/vault.py:84` | `ValueError` — no `modules:` key |
| `app/vault.py:106` | `ValueError` — unknown flag |
| `app/vault.py:74-78` | `FileNotFoundError` — absent registry |
| `app/registry.py:168-176` | `yaml.YAMLError`, **`AttributeError`, `TypeError`** — `products.yaml` |
| `app/registry.py:110` | `AttributeError`, `TypeError` — front-matter shaped wrongly |
| `app/registry.py:128-152` | `sqlite3.DatabaseError`, `yaml.YAMLError`, `AttributeError`, `TypeError` |

`AttributeError` and `TypeError` are not incidental. A registry that is
**syntactically valid YAML but wrongly shaped** — a list where a mapping is
expected, a scalar where a list is — parses cleanly and then fails on attribute
or subscript access. `yaml.safe_load(...) or {}` guards only the empty case, not
the wrong-type case. Converting only `yaml.YAMLError` would leave the more
likely hand-editing mistake reported as "an unexpected error was not handled",
contradicting the `E-CONFIG` outcome this table promises.

Each conversion narrows to the specific parse or access it guards. A blanket
`except (AttributeError, TypeError)` around a whole function would mask genuine
programmer errors, which is the catch-all Rule 5 forbids.

`app/vault.py:101` (unknown archetype) is **not** converted: its only caller
passes `archetype=None`, so it is unreachable and a characterization test would
characterize dead code.

No other bare raise is converted. Each converted site is characterized by test
first, so the conversion is proven to preserve behavior.

---

## 6. Disclosure boundary

Curated messages may not contain an entity slug, an absolute or vault-relative
path, a filename, a module or registry value, a commit id, Git stderr, a stack
trace, or any echoed request value. Raw exception text never reaches HTML.

**Rule 8 — never reflect a submitted value, and never hand-build request
values.** Two distinct defects:

*Reflected copy.* `registry_delete_execute` builds its success message from the
submitted `slug` form field, never compared against the proposal's own slug, and
interpolates it into an unescaped `HTMLResponse`. Both branches move to
templates, and success copy is built from the **server-derived** slug.

`execute_delete` returns `None` (`app/registry.py:292`) and removes the proposal
file as an owned change (`:346`), so the record cannot be read afterwards. The
route therefore calls `get_delete_proposal` **before** executing and holds the
validated slug from it. Changing `execute_delete`'s signature would be a service
change and is not authorized here; the requirement is satisfied entirely in the
route. The submitted `slug` field is then unused for display and the execute
request stops sending it at all, since the server derives kind and slug from the
validated proposal.

*Request rebinding.* Both registry templates hand-build `hx-vals` JSON around an
interpolated value:

```
templates/registry.html:31             hx-vals='{"slug": "{{ slug }}"}'
templates/blocks/delete_impact.html:14 hx-vals='{"id": "{{ prop.id }}", "slug": "{{ slug }}"}'
```

A crafted slug can close the string and inject a second `id` key after the
browser decodes HTML entities, so the approval request refers to a proposal
other than the one previewed — a preview/approve mismatch, not merely a display
bug. The complete mapping must be built server-side and passed through `tojson`,
which is the pattern `templates/triage.html:83` already uses
(`hx-vals='{{ proposal_values | tojson }}'`) and which these two templates did
not follow.

S6 converts both, and a test asserts that a slug containing quotes, braces, and
a second `id` key yields exactly one `id` in the parsed `hx-vals` and that it
equals the previewed proposal.

**Rule 9 — no filename disclosure.** An unreadable outbox record renders a
generic row. Its filename is attacker-controlled and is not echoed.

`E-SCOPE` states only that the request resolved outside the selected entity,
never what it resolved toward. `E-ENTITY` distinguishes an absent entity from a
present one; this is accepted for a single local operator whose sidebar already
lists every entity, and is recorded because `scope.current_entity()` is the
future tenant boundary. Before the Console serves more than one operator,
`E-ENTITY` and `E-SCOPE` must become indistinguishable.

Describing and rendering an error performs no mutation: no file written, nothing
staged, no directory created, no lock acquired.

The error page cannot include the sidebar when the described error is
`E-CONFIG`, because `_sidebar.html` iterates bundles and `Vault.bundles()` is
what failed. The same re-entrancy argument as the outbox listing applies.

---

## 7. Test matrix

### Resolver

- Every application exception class resolves to a description other than
  `E-UNKNOWN`, by enumerating classes from the module hierarchy and subtracting
  the table.
- Every direct or indirect subclass of `GitTransactionError` has its **own**
  entry; a synthetic subclass added in-test resolves to `E-UNKNOWN`, not `E-GIT`.
- All five S5 outcomes resolve correctly through **both real service paths** —
  the failure is injected into the transaction layer and propagates through the
  actual `approve` and `execute_delete` wrappers. A hand-built wrapper would
  pass even if a service stopped chaining with `from exc`.
- `EntitySelectionError` inside `HTTPException` resolves to `E-ENTITY`;
  `DestinationRegistryError` inside `OutboxDestinationError` resolves to
  `E-CONFIG`.
- A class not on the allowlist never has its cause read.
- `__context__` is never traversed: an error raised while handling another
  resolves to itself.
- Chain depth is bounded at 4.
- Both structural invariants hold across the whole table.
- No **domain or service** module imports `console_errors` or `console_render`.
  The presentation composition root — `app/main.py` and the templates it renders
  — must import them; it is the layer that turns an exception into a response.
  The test asserts on `app/` excluding that root, so it forbids the cycle
  without forbidding the intended dependency.

### Routes

- Each site in the inventory, with its error injected, returns the expected
  status, renders the expected code and message, and no raw exception text.
- Fragment responses match their route's **declared swap shape**, so the swap is
  proven rather than inferred from status. The shape is per route, not global:
  an `outerHTML` route must reproduce the target root element, while an
  `innerHTML` route must **not** — reproducing it there would nest a duplicate
  id inside itself. The current shapes are `#outbox-list` / `outerHTML` for
  approve and reject, `#diff-{index}` / `innerHTML` for propose, and
  `#impact-{index}` / `innerHTML` for delete preview, and `closest
  .delete-impact` / `innerHTML` for delete execute — a relative selector rather
  than an id, and the route S6 rewrites most heavily. The test asserts the
  declared shape for each and never echoes the client-supplied `HX-Target`
  header, which is attacker-controlled.
- **Route-level totality:** for each route, every exception in that route's
  **declared conversion set** — the stdlib and third-party types listed in
  Boundary conversions below — is injected, and the global handler is asserted
  *not* to be the responder. The test enumerates the conversion set, not "every
  exception reachable anywhere": the design deliberately routes unconverted
  stdlib failures to `E-UNKNOWN` through the handler, so an unbounded claim
  would contradict §10 and be unsatisfiable. Adding a boundary conversion means
  adding it to the set, which is what makes the test grow with the code.
- Every full-page route contains the `htmx-config` meta tag.
- An unhandled exception during an HTMX request returns 500 and a visible body.
  This requires `TestClient(app, raise_server_exceptions=False)`, as three
  existing tests already do.
- A missing or malformed form field renders `E-REQUEST` without echoing the
  field name or submitted value; an unmatched URL still returns a plain 404.

### Projection

- With no unreadable record, every valid proposal renders with its normal
  approve and reject controls and remains approvable.
- With one unreadable record, the listing is `blocked`: the unreadable row is
  generic and echoes no filename, valid rows still render with id, destination,
  and diff, **no classification control appears anywhere in the listing**, and
  one `E-UNREADABLE` notice states that nothing in this entity can be approved
  or rejected until the file is repaired outside the Console.
- Posting approve or reject in the blocked state is still refused by the
  unchanged strict loader — proving the projection describes the coupling rather
  than creating it.
- A **well-formed** delete proposal renders nothing, does not block the listing,
  and never appears as unreadable, matching `load_proposals` exactly. A
  malformed delete record does block it.
- An outbox containing only delete proposals renders as empty, not as blocked.
- `project_outbox` calls neither `get_proposal`, `load_proposals`, nor
  `preview_diff`; asserted by patching all three to raise.
- Approval of a row rendered by the projection still performs full strict
  revalidation.
- The Gate 1 counter does not increment on a refused `propose` and does
  increment on a persisted one.

### State proof

Snapshots before and after using the existing `conftest.py` fingerprint helpers.

**`committed` means "a Git commit was created". It does not mean "no state
changed."** These are different claims, and conflating them makes the contract
unsatisfiable for two routes that persist before they render:

- `propose` calls `propose_classification`, which writes the proposal file, and
  only then evaluates `preview_diff` while building the template context
  (`app/main.py:126-135`).
- `registry_delete_preview` calls `propose_delete`, which writes its proposal,
  and only then calls `reference_count` (`app/main.py:211-216`).

A described failure in that second phase returns `committed = no` while a
newly written proposal remains on disk. That is **correct behavior, not a
defect**: the domain action succeeded, and S6 must not roll back a successful
write merely because rendering failed. The operator sees the error, and the
proposal is in the outbox where it can be reviewed or rejected normally.

Each state-proof test therefore declares an expected **persistence** outcome
alongside `committed`, and the assertion is selected by both:

- `committed = no`, persistence `none` — every snapshotted value identical.
- `committed = no`, persistence `proposal-written` — `HEAD`, the index, and all
  tracked content identical; exactly one new untracked proposal under the bound
  entity's `outbox/`, and nothing else. Only the two routes above may declare
  this.
- `yes` — exactly the reviewed paths committed at one new `HEAD`, unrelated
  state identical. Injection must be a real post-commit cleanup `OSError` so
  `app/git_transaction.py:466` fires after a genuine commit; monkeypatching
  `execute_transaction` to raise produces no commit and cannot pass.
- `unknown` — unrelated state identical, and the owned path matches either its
  pre-request state or the concurrent writer's state and nothing else.

### Regression

S1-S5 **service and state-safety tests remain unchanged**. Route tests whose
explicit purpose was to defer S6 presentation are expected to change, and are
listed individually rather than discovered during implementation:

| Test | Why it changes |
|---|---|
| `tests/test_app.py:477-503` | asserts `role="alert"` is absent for an injected transaction error — S6 makes it present |
| `tests/test_app.py:588` | asserts the raw string `"registry deletion transaction failed"` renders — the disclosure boundary forbids it |
| `tests/test_app.py:468` | asserts `status_code == 200` for a stale-source refusal. No existing test sends `HX-Request`, so it takes the full-page branch, where `E-STALE` declares 409 |
| `tests/test_app.py:538` | asserts `status_code == 200` for a cross-entity outbox action. That id reaches the plain `OutboxError` at `app/outbox.py:354`, described `E-INVALID` at 422 |

The last two were **not** in the previous draft, which listed two tests and then
declared that any other change was a scope breach. That claim was false against
this design's own text, and `tests/test_app.py:468` sits inside the very test
the previous draft named as one that does *not* change. Recording them is the
point: an enumeration that quietly omits its inconvenient members is the failure
this project keeps re-learning.

`tests/test_app.py:538` deserves particular care. It is an **S2 cross-entity
isolation test** — a state-safety test, the category that is supposed to survive
untouched. What changes is only the status code carrying the refusal; the
refusal itself, and the isolation it proves, are unchanged. The updated test
must continue to assert that entity A's action cannot touch entity B's proposal,
and only its status expectation moves.

An alternative worth weighing during planning: route `propose`, `outbox_approve`,
`outbox_reject`, and the two registry POSTs through the fragment renderer
regardless of `HX-Request`. None has a full-page template, so the full-page
branch is unreachable in production and exercised only by tests. That would keep
both status assertions at 200 and shrink this table back to two rows. It is
recorded as an option, not chosen here, because it makes the renderer selection
depend on route shape as well as request headers.

The rule this table replaces: **an S1-S5 service or state-safety test whose
subject is a refusal decision, an isolation guarantee, or a state proof must not
change.** A route test's status or markup expectation may change where S6
deliberately alters presentation, and every such test is listed above. Any test
not listed here requiring modification is a scope breach.

`tests/test_app.py:434` and `:439` do **not** change: `E-STALE` and `E-MISSING`
are byte-identical to the strings they assert.

Any other S1-S5 test requiring modification is a scope breach, not an expected
consequence.

---

## 8. Completion gates

- Focused resolver, route, projection, outbox, registry, triage, and scope tests.
- `uv run pytest tests/test_vault.py -q` — the `BUILD.md` standing E4 regression,
  required after any change to `vault.py`, the sidebar, or `scope.py`. S6 converts
  three `vault.py` sites and touches `scope.py`, so it is named rather than left
  to the full suite incidentally.
- `uv run pytest tests/test_app.py -q`
- `uv run pytest tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py -q`
- `uv run python -m pytest -q`
- Private unittest discovery; `check_v2` at zero errors and zero warnings;
  policy-enforcer self-test; pinned Gitleaks; public and combined audits.
- `git diff --check` and a whole-branch review for safe disclosure, HTMX
  behavior, typed outcome accuracy, S1-S5 preservation, instance leakage, and
  non-goal drift.
- Grey Matter `HEAD`, porcelain status, unstaged binary diff, and staged binary
  diff captured to an external `/private/tmp` proof directory before and after
  the private gates, requiring exact byte equality. The vault carries
  pre-existing uncommitted edits; they are preserved, never cleaned.

## 9. Non-goals

No new route, screen, dashboard card, drag-drop UI, batch UI, or workflow. No
change to any validation, refusal, or commit decision. No new dependency,
JavaScript framework, schema, daemon, or deployment behavior. No adapter or
ingest surface. No LLM or external-service call in the request path. No new
logging subsystem. Existing OneOS, Command Center, workspace, and Blocks /
Modules terminology preserved exactly.

## 10. Known limitations

- `catalog = build_catalog()` runs at module scope (`app/main.py:50`), so an
  `EntityManifestError` or the `RuntimeError` from `config.vault_root()` aborts
  import before any handler exists. `E-CONFIG` cannot describe that instance.
- `E-ENTITY` distinguishes an absent entity from a present one; see §6.
- `OutboxScopeError` is reachable only for a stored record whose `entity:` field
  disagrees with the bound scope. The cross-entity request the S2 suite
  exercises never reaches it — `load_proposals` yields only the bound entity's
  records, so `get_proposal` raises plain `OutboxError` and the operator sees
  `E-INVALID`. S6 does not change this; it is recorded so the `E-SCOPE`
  coverage claim is not overstated.
- Adapter and ingest failures have no Console surface. `E-INGEST` is described
  but unreachable until the deferred upload route exists.
- Stdlib exceptions outside the converted boundary sites resolve to `E-UNKNOWN`
  by design, so a programmer error is never described as a routine refusal.
- Keying the table on classes pulls `app/rename.py` and `app/ingest/` into the
  web app's import graph for the first time, to reach `RenameError` and
  `IngestError`. Both are import-light, but this widens the module-scope failure
  surface noted above, where an exception during import precedes any handler.

---

## 11. Unresolved: the review gate does not bind reviewed content

Round-four review identified a Critical gap that S6 exposes but does not close.
It is recorded here rather than absorbed, because closing it is a domain change.

### The gap

A proposal id names a **mutable file**, not the bytes an operator reviewed.
`require_proposal_identity` (`app/proposal_identity.py:42-46`) checks only id
grammar and filename equality. All three actions take an id and nothing else:

```
outbox.approve(scope, proposal_id)
outbox.reject(scope, proposal_id)
registry.execute_delete(scope, proposal_id)
```

Each re-reads the record and compares it only against another read made during
the **same request** (`app/outbox.py:387-390`, `app/registry.py:309-328`). No
service knows what the browser rendered.

So between preview and approval, another process may rewrite a proposal while
preserving its id and filename. Approval then moves the source to a different
valid destination, deletes a different product, or rejects a different record —
each passing every existing check, because every check validates the *current*
record's internal consistency, never its correspondence to what was reviewed.

`tojson` (Rule 8) closes request rebinding — the browser now submits the id that
was rendered. It cannot close this, because the id is not the content.

This defeats the human approval gate, which is the product's central claim.

### Why it is not fixed here

The fix is well understood and bounded: hash the validated proposal snapshot
used to render, submit `id + review_sha256`, pass the expected digest into all
three actions, compare against the exact snapshot before the first mutation, and
refuse visibly on mismatch. The registry execute request then drops `slug`
entirely, since the server derives kind and slug from the validated proposal.

But it changes `approve`, `reject`, and `execute_delete` signatures and **adds a
new refusal condition**. S6's defining constraint is that it changes no refusal
decision, and that line has already been breached twice in this design's history
and caught in review both times. Absorbing a third breach — a larger one, in the
approval path — because it arrived labelled "bounded" would be the same mistake.

The precedent is exact. S4 bound the **source receipt** bytes with a SHA-256 and
refused stale approvals. This binds the **proposal record** bytes with a SHA-256
and refuses stale reviews: the same mechanism, one artifact further out. S4 was
its own step, with its own design, plan, and review. This deserves the same.

### Disposition

Proposed as **S7 — bound review tokens**, to be designed after S6 merges.

Until then the gap is live. It is not introduced by S6 and is not widened by it:
the projection revalidates each record with the same checks the strict loader
applies, and approval still runs the full strict path. S6 leaves the exposure
exactly where it found it, and now states it plainly instead of implying the
displayed diff means more than it does.

If the gap is judged too severe to leave open across an S6 merge, the correct
response is to **suspend S6 and design S7 first**, not to widen S6.
