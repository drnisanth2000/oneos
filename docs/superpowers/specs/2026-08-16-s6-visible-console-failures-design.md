# S6 — Visible Console Failures

**Status:** Approved

**Base:** `origin/main` at `3585938` (merged S5 and the S1-S5 documentation
reconciliation). Public baseline: 603 tests.

## Objective

Every refusal the Command Center already makes must reach the operator as a
specific, safe, actionable message. No route may swallow an error, return a raw
server fault, or render a screen that hides the failure it is protecting
against.

S6 defines the public error contract across the Command Center, the FastAPI
routes, HTMX fragments, templates, and the S2-S5 typed failures. It is an
architectural change in that sense, but it changes presentation only: when an
operation is refused, what is validated, and what is committed are all
unchanged. The S2-S5 taxonomy is stable and is treated here as input.

Spec authority: `oneos-spec.md` §10.1 item 6 requires stale, invalid,
cross-scope, and Git failures to be visible in OneOS. §5.2 places the mapping
inside the Command Center service boundary. §6 governs the HTMX/Alpine split.

## Approved scope

- One description table mapping every application exception to a stable code,
  severity, operator message, retry guidance, and commit outcome.
- Error presentation on all eleven routes, the scope dependency, and a global
  fallback.
- Per-record degradation of the outbox listing so one bad record cannot hide
  the rest.
- Escaped rendering, replacing the one route that interpolates an exception
  string into HTML.

## Non-goals

- No new route, screen, dashboard card, drag-drop UI, batch UI, or workflow.
- No change to any validation, refusal, or commit decision.
- No new dependency, JavaScript framework, schema, daemon, or deployment
  behavior.
- No adapter or ingest surface. Adapters run outside any HTTP request and have
  no Console surface to fail into.
- No LLM or external-service call in the request path.
- No new logging subsystem.
- Existing OneOS, Command Center, workspace, and Blocks / Modules terminology
  is preserved exactly.

## Error description model

### `app/console_errors.py`

A new module owns the entire operator-facing error vocabulary. No route,
template, or service writes error copy.

```text
ConsoleError(code, severity, message, retry, committed, transparent=False)
describe(exc) -> ConsoleError
```

`ConsoleError` is frozen.

- `severity` is `refusal` or `attention`.
- `retry` is `retry`, `reload`, `recreate`, `stop`, or `none`.
- `committed` is `no`, `yes`, or `unknown`.

`describe` walks the exception's method resolution order and returns the first
matching entry, so a subclass inherits its family's description unless it
carries its own. The private transaction failures inside `git_transaction.py`
resolve through their parent by this rule and need no entry.

`describe` is total. An exception with no match anywhere in its MRO returns
`E-UNKNOWN`. It never raises, never returns `None`, and never falls through to
a bare `except`.

### Import direction

`console_errors.py` imports the application's exception classes directly and
keys the table on the classes themselves. String-qualified class names were
considered and rejected: they are not refactor-safe, and a renamed class would
degrade silently to `E-UNKNOWN` rather than failing at import.

The boundary is therefore one-way and asserted, not avoided: presentation
imports domain, and **no application service, route helper, or registry module
imports `console_errors`.** A test walks `app/` and fails if any module other
than the route layer and the renderer imports it.

This is why the degradation in the outbox listing produces no `ConsoleError`
inside the service (see below). Had the service needed to describe its own
failures, the cycle would be real.

### Transparent wrappers

`approve` and `execute_delete` each collapse the whole S5 error family into one
wrapper — `OutboxTransactionError` and `RegistryTransactionError` — with the
real outcome retained only as `__cause__`. Busy, reviewed-state conflict,
rolled-back failure, recovery-blocked, and committed-with-cleanup-warning are
therefore indistinguishable above the service boundary today.

Left alone, this would make `E-COMMITTED` and `E-RECOVER` unreachable: every
transaction outcome would render as `E-GIT`, telling an operator whose commit
succeeded to retry it and producing a second commit for one reviewed action.
That is the Gate 2 break this design exists to prevent.

Re-raising S5 types through the routes would breach the typed service boundary
S5 established. Instead, the two wrapper entries are marked `transparent`.
When `describe` resolves to a transparent entry it recurses into `__cause__`
exactly once and returns that description instead. A transparent entry that has
no cause resolves to itself, so the behavior is total.

Transparency is declared per entry, never inferred. `describe` never walks a
chain of arbitrary depth and never reads a non-transparent exception's cause,
so no internal failure can surface through an error that was not designed to
carry it. This changes no domain behavior, no service boundary, and no refusal.

No separate `category` field is defined. The code is the category identifier
and severity already carries the grouping a renderer needs; a third axis would
be unused state that can drift out of agreement with the other two.

### Why `committed` and `retry` are structured fields

The two dangerous S5 outcomes differ from every other failure in exactly one
way an operator must not have to infer from prose: whether the vault changed.
Encoding it as data makes retry-safety machine-checkable and testable rather
than a property of wording that a later edit could quietly destroy.

This yields a structural invariant that S6 tests directly: **every entry with
severity `refusal` must carry `committed = no`.** An entry claiming an action
was refused while reporting a commit is a contradiction, and the table cannot
express one without failing its own test.

### Codes

| Code | Severity | Retry | Committed | Surface | Page status | Covers |
|---|---|---|---|---|---|---|
| `E-STALE` | refusal | recreate | no | inline | 409 | `StaleProposalSource` |
| `E-MISSING` | refusal | recreate | no | inline | 409 | `MissingProposalSource` |
| `E-INVALID` | refusal | recreate | no | inline | 422 | `OutboxError`, `OutboxDestinationError`, `ProposalIdentityError` |
| `E-DEST` | refusal | recreate | no | inline | 422 | `DestinationError` and subclasses |
| `E-SCOPE` | refusal | none | no | inline | 404 | `CrossScopeError`, `OutboxScopeError` |
| `E-BUSY` | refusal | retry | no | inline | 409 | `VaultBusyError` |
| `E-CONFLICT` | refusal | reload | no | inline | 409 | `ReviewedStateConflict` |
| `E-GIT` | refusal | retry | no | inline | 500 | `GitTransactionError`, `GitTransactionFailure`, and the two transparent wrappers when they carry no cause |
| `E-RECOVER` | attention | stop | unknown | inline | 500 | `GitTransactionRecoveryError` |
| `E-COMMITTED` | attention | stop | yes | inline | 500 | `GitTransactionCommittedError` |
| `E-REGISTRY` | refusal | reload | no | inline | 422 | `RegistryError` |
| `E-REQUEST` | refusal | recreate | no | inline | 422 | `RequestValidationError`, `HTTPException` below 500 |
| `E-CONFIG` | attention | none | no | page | 500 | `DestinationRegistryError`, `EntityManifestError` and subclasses |
| `E-ENTITY` | refusal | none | no | page | 404 | `EntitySelectionError` |
| `E-INGEST` | refusal | none | no | inline | 500 | `IngestError` and subclasses |
| `E-ADMIN` | refusal | none | no | inline | 500 | `RenameError` |
| `E-UNKNOWN` | attention | stop | unknown | page | 500 | anything unmapped |

`E-SCOPE` returns 404 rather than 403 deliberately: a distinct authorization
status would itself confirm that the resolved-toward resource exists. See the
disclosure boundary below.

### Exact message text

These strings are the contract. They are versioned with the code and may not be
reworded casually, because an operator learns them.

| Code | Message |
|---|---|
| `E-STALE` | Approval refused: the source changed after this proposal was created. Create a fresh proposal. |
| `E-MISSING` | Approval refused: the source is missing. Restore it or reject the proposal. |
| `E-INVALID` | This proposal record is not valid and cannot be approved. Create a new proposal. |
| `E-DEST` | The destination could not be resolved from the registries. Re-classify this item. |
| `E-SCOPE` | Refused: the request resolved outside the selected entity. |
| `E-BUSY` | Another approval is in progress. Nothing was changed. Try again in a moment. |
| `E-CONFLICT` | The reviewed files changed since this proposal was previewed. Reload and review again. |
| `E-GIT` | The commit failed and was rolled back. Nothing was changed. |
| `E-RECOVER` | Rollback was blocked by a change made at the same time. Do not retry. Inspect vault state with git status and resolve it before continuing. |
| `E-COMMITTED` | The commit succeeded; only the cleanup afterwards failed. Do not retry — retrying would commit this action twice. Inspect vault state with git status. |
| `E-REGISTRY` | The registry operation was refused. Review the impact report and try again. |
| `E-REQUEST` | The form could not be read. Reload the screen and try again. |
| `E-CONFIG` | The vault registries could not be read. The Console cannot operate on this entity until they are valid. |
| `E-ENTITY` | That entity is not in the manifest. |
| `E-INGEST` | Intake failed. Nothing was written to the vault. |
| `E-ADMIN` | The administrative operation was refused. |
| `E-UNKNOWN` | An unexpected error was not handled. Inspect vault state with git status before continuing. |

Two wording invariants are asserted by test rather than left to review:

- The `E-COMMITTED` message must contain both an affirmative statement that the
  commit succeeded and an explicit instruction not to retry.
- No `attention` message may contain the word "again" in a retry sense; both
  `E-RECOVER` and `E-COMMITTED` must direct the operator to inspect state.

Retry guidance is **text only**. S6 renders no retry button, because an
affordance would invite exactly the second commit `E-COMMITTED` exists to
prevent. `retry` drives test assertions and future affordances, not markup.

Codes are stable identifiers. They may be added but never renamed or reused,
because an operator's screenshot must stay resolvable against a later build.

`E-INGEST` and `E-ADMIN` describe classes no current route raises. They are
mapped anyway so the table stays total without an exemption list, and so the
deferred upload route inherits a description rather than inventing one.

### The two outcomes that must not be retried

`E-COMMITTED` carries `committed = yes`. Its message states plainly that the
commit succeeded and only cleanup failed, because an operator who assumes
failure and retries would produce a second commit for one reviewed action and
break Gate 2. Its retry guidance is `stop` and the surface offers no retry
affordance.

`E-RECOVER` carries `committed = unknown`. Rollback was blocked by a concurrent
same-path change, so S5 deliberately declined to overwrite newer state. Its
message directs the operator to inspect and resolve vault state before any
further action, never to retry.

## Severities, surfaces, and HTTP status

**`refusal`** means the operation did not happen and the vault is unchanged.
**`attention`** means a human must inspect vault state before continuing. It is
reserved for the two S5 outcomes above and for `E-UNKNOWN`, whose safety is by
definition unproven.

The vendored HTMX is **2.0.4**, which does not swap non-2xx responses. A
refusal returned as 4xx would therefore leave the operator staring at an
unchanged screen with no message — the exact failure S6 exists to remove.

The resolution is one mapping with two renderers, selected by the `HX-Request`
header:

- **Fragment path** (`HX-Request` present): renders the alert into the
  fragment the route already swaps and returns **HTTP 200**, so HTMX applies
  it. Transport status carries no meaning here; the body carries the outcome.
  This generalizes the pattern S4 established for freshness refusals rather
  than introducing a second mechanism.
- **Page path** (no `HX-Request`): renders a full page and returns its **true
  status** — 404 for `E-ENTITY`, 500 for `E-CONFIG` and `E-UNKNOWN`. Nothing
  is being swapped, so honest status codes cost nothing.

Both paths read the same `ConsoleError`. Message text exists in one place and
is never duplicated per surface.

### Fragments must stay visible without laundering a fault

Returning 200 for a handled domain refusal is sound: the refusal is an expected
outcome and the body carries it. It is **not** sound for an unhandled
exception, which must stay a 500 so monitoring and tests can see a defect. But
a 500 that HTMX will not swap leaves the operator staring at an unchanged
screen — the exact failure S6 exists to remove.

HTMX 2.0.4 resolves this in configuration rather than status laundering. Its
`htmx.config.responseHandling` defaults to
`[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`.
The Console overrides the last entry so 4xx and 5xx responses swap while still
being recorded as errors:

```js
htmx.config.responseHandling = [
  {code: "204", swap: false},
  {code: "[23]..", swap: true},
  {code: "[45]..", swap: true, error: true},
];
```

This is a configuration line in the existing shell template. It adds no
dependency, no extension, and no new JavaScript file, and it is set once for
every screen so no route can forget it.

With it, the two statuses stop competing: handled refusals return 200, unhandled
faults return their true 500, and both are swapped and visible. The fragment
renderer therefore returns the code's page status for `E-UNKNOWN` even on an
HTMX request.

A test asserts the configuration is present in the rendered shell, because a
silent revert would make every unhandled HTMX failure invisible again while
every other test continued to pass.

### Framework validation failures

Every mutating route declares required `Form(...)` parameters. A missing or
malformed field is rejected by FastAPI before any handler runs, producing a 422
JSON body that HTMX will not render and no route can catch.

`RequestValidationError` and `HTTPException` below 500 therefore map to
`E-REQUEST` through dedicated FastAPI exception handlers. Those handlers use the
same description table and the same renderers.

`E-REQUEST` never echoes the rejected value, the field name, or FastAPI's
validation detail. A malformed field is a client-side or tampering condition,
and reflecting the submitted value back into HTML is precisely the leak the
disclosure boundary forbids.

`static/app.css` gains `.alert` and `.alert-attention`. S4's borrowed
`.diff-head` styling for `approval_error` is replaced. Every alert carries
`role="alert"` and renders its code alongside its message.

## Alpine state and swap targets

Per `oneos-spec.md` §6, the error content is server-owned and therefore HTMX's
responsibility, while triage keyboard navigation, multi-select, and the
stopwatch are Alpine's. A swap that replaces an element containing `x-data`
destroys that state.

Two concrete rules follow from the current templates:

- The `propose` alert renders into the existing `#diff-{index}` target with
  `hx-swap="innerHTML"`. That target sits **inside** `x-data="triage(...)"`, so
  the triage scope survives. S6 must not introduce a new target that encloses
  the `x-data` root.
- `#outbox-list` swaps `outerHTML` and contains no Alpine state today. S6
  introduces none, so the swap stays safe. Any future Alpine state inside that
  fragment requires `hx-swap="morph"` first.

Targets are stable and pre-existing. S6 adds no new `hx-target`.

## Route inventory

S6 is complete when every one of these sites presents a described error. The
list covers all eleven routes registered in `app/main.py`, the scope
dependency, and the global handler. Enumerating only the routes with visible
symptoms would repeat the failure this project keeps hitting, so the reading
routes are listed even where they are currently silent.

| Site | Current behavior | Required behavior |
|---|---|---|
| `shell` | `Vault().bundles()` uncaught | `E-CONFIG` page notice |
| `pulse` | reads no vault state | Unchanged; no vault error reachable |
| `triage_default` | `Vault().bundles()` uncaught | `E-CONFIG` page notice |
| `triage` | `except (DestinationError, DestinationRegistryError): destination = None` | Render the affected row with its code and message instead of a silently blank destination |
| `propose` | uncaught; raw server fault | Described alert into the existing `#diff-{index}` target |
| `outbox_screen` | `load_proposals` and `preview_diff` uncaught; one bad record blanks the screen | Degraded listing, below |
| `outbox_approve` | `except OutboxError: pass`; the S5 family is uncaught | Describe every outbox, destination, scope, identity, and transaction error inline |
| `outbox_reject` | `except OutboxError: pass` | Describe inline |
| `registry_products` | `products_for` uncaught | Describe inline |
| `registry_delete_preview` | uncaught; raw server fault | Describe inline |
| `registry_delete_execute` | interpolates the exception into an unescaped f-string | Render through a template with a described error |
| `entity_scope` | `HTTPException(404)`, empty body | 404 with a rendered `E-ENTITY` page |
| global handler | does not exist | Describe an unhandled exception and render a safe notice at 500 |

The global handler is a backstop, not the mechanism. A route that relies on it
instead of catching its own error family has not satisfied S6.

### Programmer errors stay uncaught

S6 must not become a catch-all that hides defects. Three rules keep it honest:

- Stdlib exception types are **not** in the table. Mapping `ValueError` would
  silently capture every application error inheriting from it, and describing
  an unanticipated programming fault as a routine refusal would be dishonest.
  `vault.py` raises bare `ValueError` at exactly three reachable sites, all
  registry-validity conditions the Console should name:

  | Site | Condition |
  |---|---|
  | `app/vault.py:84` | `archetypes.yaml` has no `modules:` key |
  | `app/vault.py:101` | an entity declares an unknown archetype |
  | `app/vault.py:106` | an entity declares an unknown flag |

  All three convert to the existing `DestinationRegistryError`, which already
  means "a registry could not be read as valid" and already maps to `E-CONFIG`.
  No new error type is introduced and no refusal changes: the same inputs are
  rejected at the same points. Each site gets a characterization test capturing
  its current behavior before the type changes, so the conversion is proven to
  preserve it. No other bare `raise` in the application is converted.
- The global handler returns **500**, never 200. A programmer error must not be
  laundered into a successful-looking response.
- A test asserts the global handler is not reached for any described error, so
  reliance on it is a failure rather than a silent default.

## Outbox per-record degradation

`load_proposals` currently raises on the first malformed record and abandons
the whole listing. One unreadable file therefore hides every valid proposal,
and because the error-rendering path calls `load_proposals` again, rendering
the alert raises the same exception and produces a server fault instead of the
message.

Under S6 the strict loader is left exactly as it is. Every mutating path —
`approve`, `reject`, `get_proposal` — keeps calling `load_proposals` and keeps
its fail-closed behavior. S6 adds a **presentation-only tolerant projection**
used solely to render the listing.

The projection yields, per file, either a usable proposal or a refused row
identified by filename alone. A refused row is **neither approvable nor
rejectable**, and carries `retry = stop`.

Rejection is deliberately withheld. `reject` resolves its target through
`get_proposal`, which calls the strict loader, so a malformed record fails
before a proposal can be returned. Making such a file deletable would require a
new destructive service contract that deletes a path the domain has never
validated — a change to what the Console is allowed to destroy, not to how a
refusal is displayed. That contradicts this design's central promise and is out
of scope. If Console deletion of malformed records is wanted, it needs its own
design with explicit lexical-path and ownership rules.

The projection produces no `ConsoleError` inside the service. It reports the
failure as data the route layer describes, which is what keeps the import
boundary one-way.

The operator's recovery path for a malformed record is therefore to repair or
remove the file outside the Console. The refused row names the file and says so.

This narrows the blast radius without relaxing a refusal: valid proposals stay
visible and approvable, the bad record stays unusable, and every identity,
destination, scope, and freshness check keeps its existing authority. Reporting
the bad record is also strictly safer than a blank screen, which conceals the
tampering it is reacting to.

## Disclosure boundary

Curated messages may not contain an entity slug, an absolute or vault-relative
path, a module or registry value, a commit id, Git stderr, a stack trace, or
any echoed request value. This is why the existing internal strings are not
passed through: registry errors embed the submitted slug and identity errors
embed the submitted proposal id.

`E-SCOPE` in particular states only that the request resolved outside the
selected entity. It never names the path, module, or entity it resolved
toward, so a cross-scope refusal cannot be used to test whether another
entity's resource exists.

One disclosure is accepted knowingly: `E-ENTITY` distinguishes an entity absent
from the manifest from one present in it. In Phase 1 the Console is a single
local operator whose sidebar already lists every entity from `entities.yaml`,
so this reveals nothing the same session cannot read directly. It is recorded
here because `scope.current_entity()` is the future tenant boundary; if the
Console ever serves more than one operator, `E-ENTITY` and `E-SCOPE` must
become indistinguishable before that happens.

Raw exception text never reaches HTML. Only curated `ConsoleError.message`
values are rendered. Template autoescaping is asserted by test, not assumed,
and the one route that builds HTML by string interpolation is converted to a
template.

Describing and rendering an error performs no mutation. It writes no file,
stages nothing, creates no directory, and acquires no lock.

## Required behavioral tests

### Table

- Every exception class defined under `app/` resolves to a description other
  than `E-UNKNOWN`. The test enumerates classes from the module hierarchy and
  subtracts the table, so a future exception added without a description fails
  here rather than reaching an operator as `E-UNKNOWN`.
- Every entry with severity `refusal` carries `committed = no`.
- `E-COMMITTED` carries `committed = yes` and retry `stop`; `E-RECOVER` carries
  `committed = unknown` and retry `stop`.
- A class absent from the table resolves through its MRO to its parent.
- Each S5 outcome resolves to its own code, not to `E-GIT`, asserted for all
  five outcomes through **both real service paths**. The failure is injected
  into the transaction layer and allowed to propagate through the actual
  `approve` and `execute_delete` wrappers; the test never constructs a wrapper
  by hand, because a hand-built wrapper would still pass if the service stopped
  chaining with `from exc`.
- No application service, route helper, or registry module imports
  `console_errors`. A test walks `app/` and asserts the boundary is one-way.
- A transparent wrapper with no `__cause__` resolves to `E-GIT`.
- A transparent wrapper whose cause is itself transparent resolves after one
  step and does not recurse further.
- A non-transparent entry never consults `__cause__`, proven by wrapping a
  described error inside a non-transparent one and asserting the outer
  description is returned.
- An exception unrelated to the application resolves to `E-UNKNOWN` without
  raising.
- `severity`, `retry`, and `committed` never hold a value outside their
  permitted sets.

### Routes

- Each site in the inventory, with its error injected, returns the expected
  status, renders the expected code and message, and renders no raw exception
  text.
- Fragment requests return 200 and carry the target element the route's
  `hx-target` names, so the swap is proven rather than inferred from status
  alone; page requests return their true status.
- The same error yields the same message on both paths.
- Every S5 transaction outcome — busy, reviewed-state conflict, rolled-back
  failure, blocked recovery, and committed-with-failed-cleanup — is visible
  with correct severity, retry, and commit outcome.
- A rejection that fails is visible rather than silent.
- The reading routes render an `E-CONFIG` page when registries are unreadable,
  rather than a raw fault or a shorter list.
- The global handler is not reached for any described error.

### Rendering safety

- Rendering an alert never raises, including when the outbox contains a
  malformed record alongside valid ones.
- A malformed record renders as one refused row while every valid proposal
  still renders and remains approvable. The refused row offers neither an
  approve nor a reject control, and posting either action for that filename is
  still refused by the unchanged strict loader.
- A missing or malformed form field renders `E-REQUEST` on both surfaces and
  echoes neither the field name nor the submitted value.
- The rendered shell contains the `responseHandling` override, so an unhandled
  HTMX failure is swapped and visible while still returning 500.
- An unhandled exception during an HTMX request returns 500 **and** a body the
  operator can see.
- An error whose internal text contains markup renders escaped.
- Rendered alerts contain no path separators, no entity slug from the fixture
  manifest, no commit id, and no echoed request value.
- A triage row whose destination fails to resolve renders its code rather than
  an empty destination.
- Every alert carries `role="alert"`.
- The triage Alpine scope survives a `propose` error swap.

### State proof keyed to the declared outcome

For every injected failure, state is snapshotted immediately before the request
and compared after: `HEAD`, the Git index, tracked and untracked status,
proposal bytes, source and destination bytes, and registry bytes.

The assertion is keyed to the entry's `committed` value rather than assuming
every failure leaves nothing behind:

- `committed = no` requires every snapshotted value to be identical. This
  covers every `refusal`.
- `committed = yes` requires exactly the reviewed paths committed at one new
  `HEAD`, with all unrelated state identical. `E-COMMITTED` reports a durable
  commit, so a test asserting nothing changed would be asserting the wrong
  thing and could pass only if S5 rollback were broken.
- `committed = unknown` requires unrelated state to be identical and records
  the observed owned-path state without demanding a particular value, because
  `E-RECOVER` exists precisely where S5 declined to overwrite a concurrent
  change.

In all three cases, presenting the error adds no mutation of its own. This is
what makes `committed` load-bearing rather than descriptive: it selects the
assertion, so a wrong value fails a test instead of misleading an operator.

### Regression

Every S1-S5 test passes unmodified. S6 changes no refusal decision, so any test
requiring modification indicates a scope violation rather than an expected
consequence.

## Completion gates

- Focused console-error, route, outbox, registry, triage, and scope tests.
- Full public pytest suite.
- Full private unittest suite.
- `check_v2`: zero errors and zero warnings.
- Policy-enforcer self-test.
- Pinned Gitleaks.
- Public and combined repository audits.
- `git diff --check` and a final whole-branch review confirming no S7,
  instance-specific, private-vault, or unrelated change.
- Grey Matter status, worktree diff, and cached diff byte-identical before and
  after all private gates. The vault carries pre-existing uncommitted edits;
  they are preserved, not cleaned.

Per `AGENTS.md`, S6 uses one task, one branch, and one worktree created from the
recorded base SHA above, and remains local until publication is explicitly
authorized.
