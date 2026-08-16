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
  `OutboxDestinationError`, `HTTPException`, and `GitTransactionFailure`.
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
| `CrossScopeError` | `RedirectedPathError` | `app/outbox.py:99,122,140`, `app/inbox.py:46,57` |
| `ReviewedStateConflict` | `ReviewedPathIntegrityError` | `app/git_transaction.py:172,192,198,228` |

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
| `outbox.ProposalFreshnessError` → `StaleProposalSource` | `E-STALE` | exact |
| `outbox.MissingProposalSource` | `E-MISSING` | exact |
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
| `fastapi.HTTPException` | `E-REQUEST` | exact |

`UnsafeDestinationPath` and `SystemRegistryPathError` are `E-TAMPER` rather than
`E-DEST`/`E-CONFIG`: both fire specifically on redirected or non-canonical
paths, which is the integrity condition, not an ordinary resolution failure.
Both are `exact` so their siblings keep the ordinary code.

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

S6 adds a **presentation projection** that validates one record at a time:

```text
project_outbox(scope) -> list[OutboxRow]
OutboxRow(kind, proposal | None, diff | None, error | None)
```

The strict loader's per-file body is extracted into two shared helpers —
`_read_record(path)` and `_validate_record(scope, path, record)` — and both the
strict loader and the projection call them. The validation logic moves; it does
not change or fork.

This preserves S4's revalidation authority. S4 required that a stored record be
revalidated before its diff is shown; it achieved that by re-reading the whole
listing, which is why one bad file poisons every row. The projection performs
the same revalidation on the same record, without the global loop. What is
removed is the global re-entry, not a check.

The displayed diff carries **no approval authority**. Approval revalidates from
scratch through the untouched strict path, so a row rendering successfully is
never evidence that it will approve.

A row that fails validation is rendered as an **unreadable record**: no approve
control, no reject control, and no filename. Per Rule 9 the filename is
attacker-controlled text and is not echoed; the row is generic and carries
`E-UNREADABLE`.

`E-UNREADABLE` is a distinct code from `E-INVALID` because the two demand
opposite actions. `E-INVALID` says the proposal you tried to approve is bad —
create a new one, and the bad one disappears when you reject it. `E-UNREADABLE`
says a file in the outbox cannot be parsed at all — creating another proposal
does not remove it, and it cannot be rejected through the Console. One code
carrying both `recreate` and `stop` would have told the operator to perform an
action that cannot resolve the condition.

Withholding reject is deliberate. `reject` resolves through `get_proposal` and
the strict loader, so a malformed record cannot be rejected without a new
service contract that deletes a path the domain never validated. That is a
change to what the Console may destroy, not to how a refusal is shown, and it
is out of scope. The operator repairs or removes such a file outside the
Console; the row says so without naming it.

---

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

---

## 5. Rule 5 — Typed route handling only

Routes catch **declared domain families**, never bare `Exception`. A blanket
catch would launder programmer errors into 200 fragments, which is the opposite
of S6's purpose.

The global handler catches only what escapes a route, describes it, and returns
its page status — 500 for `E-UNKNOWN`. It never returns 200. A test asserts it
is not reached for any described error, so relying on it is a failure rather
than a silent default.

Two renderers share the one table, selected by the `HX-Request` header. A
fragment returns 200 for a handled refusal and the code's page status for
`E-UNKNOWN`; a full page returns the code's page status.

### Rule 6 — Framework wrapping is explicit

`entity_scope` stops converting `EntitySelectionError` into `HTTPException`. It
raises `EntitySelectionError`, and a dedicated handler describes it as
`E-ENTITY` at 404 — the same status the route returns today.

`E-REQUEST` covers `RequestValidationError` and `HTTPException` **raised by
application code only**. Starlette's automatic 404 for an unmatched URL, its 405,
and `StaticFiles` 404s keep their default handling; mapping them would return
422 with copy about a form for a missing vendor script.

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
templates, and success copy is built from the **validated service result**.

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
- Fragment responses carry the element named by the route's `hx-target`, so the
  swap is proven rather than inferred from status.
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

- A malformed record renders one generic unreadable row while every valid
  proposal renders and remains approvable.
- The unreadable row offers neither control, echoes no filename, and posting
  either action for it is still refused by the untouched strict loader.
- Approval of a row rendered by the projection still performs full strict
  revalidation.

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

`tests/test_app.py:434` and `:439` do **not** change: `E-STALE` and `E-MISSING`
are byte-identical to the strings they assert.

Any other S1-S5 test requiring modification is a scope breach, not an expected
consequence.

---

## 8. Completion gates

- Focused resolver, route, projection, outbox, registry, triage, and scope tests.
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
