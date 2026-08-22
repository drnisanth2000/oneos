# S6 — Visible Console Failures

**Status:** Approved for implementation planning

**Base:** `origin/main` at `a42ee12`. Public baseline: 603 tests. Private
baseline: 37 tests.

**History:** eight review rounds plus a closure pass. The document was rewritten
twice — once when the recurring cause proved architectural rather than local,
once when the recurring cause proved to be hand-maintained enumerations. Every
finding was verified against source before being accepted, several by executing
probes rather than reading. The closure review's three conditions are applied.

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
  **exact class identity**, with one stated exception: the private subclasses of
  `GitTransactionFailure` are members too. Invariant 2 walks
  `GitTransactionFailure.__subclasses__()` transitively, so a new one fails a
  test until it is listed — the membership is closed in code, not here. Silent
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
ConsoleError(code, tier, severity, message, retry, committed, page_status)
describe(exc) -> ConsoleError
```

`ConsoleError` is frozen. `tier` is one of the five above. `severity` is
`refusal` or `attention`. `retry` is `retry`, `reload`, `recreate`, `stop`, or
`none`. `committed` is `no`, `yes`, or `unknown`.

Structural invariants, all asserted:

- `severity = refusal` implies `committed = no`.
- `tier = committed` implies `committed = yes` and `retry = stop`;
  `tier = recovery` implies `committed = unknown` and `retry = stop`.
- `page_status` is one of the statuses the codes table uses (404, 409, 422,
  500), carried on the value itself so no renderer owns a parallel
  code-to-status map.

The table keys on imported exception classes, not dotted strings: strings are
not refactor-safe and a renamed class would degrade silently to `E-UNKNOWN`.
The boundary is one-way and asserted — no **domain or service** module imports
`console_errors` or `console_render`. `app/main.py` is the presentation
composition root and must import both; helpers that live inside it, such as the
outbox list renderer, are part of that root. The test asserts on `app/`
excluding the root, so it forbids the cycle without forbidding the intended
dependency.

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
| `E-UNAVAILABLE` | refusal | refusal | retry | no | 500 |
| `E-INTERNAL` | refusal | attention | stop | no | 500 |
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

| Condition | Code |
|---|---|
| a path resolved outside the bound entity | `E-SCOPE` |
| a path is redirected, is not a regular file, or was type-swapped | `E-TAMPER` |
| the reviewed file genuinely changed underneath | `E-CONFLICT` |
| a required path is simply absent | the ordinary code for that operation |

The fourth row is what the earlier split of `UnsafeDestinationPath` and
`InvalidSourceLeaf` exists to protect: absence is a routine condition and must
never raise a tampering alarm.

Distinguishing these requires the services to raise distinguishable types.
Earlier drafts enumerated the raise sites in this document. That failed three
review rounds running: the list said nine sites where source had eighteen, and
one paragraph argued that two raises were duplicates while citing a line the
table itself omitted. A hand-maintained inventory cannot report what it omits —
the `BUILD.md` §5 failure, committed inside a design about not committing it.

So no site inventory appears here. The rule is stated once and closed in code:

**Ambiguous base exceptions are abstract. Every raise site names a refined
subtype.**

These four types each cover two materially different conditions — an integrity
finding and an ordinary one — and are therefore never raised directly:

| Ambiguous base | Subtypes |
|---|---|
| `CrossScopeError` | `RedirectedPathError` → `E-TAMPER`; `OutOfScopeError` → `E-SCOPE`; `ProposalSourceUnavailable` → `E-UNAVAILABLE` |
| `ReviewedStateConflict` | `ReviewedPathIntegrityError` → `E-TAMPER`; `ReviewedStateChanged` → `E-CONFLICT`; `ReviewedPathUnavailable` → `E-CONFLICT`; `InvalidTransactionPath` → `E-INVALID` |
| `UnsafeDestinationPath` | `RedirectedDestination` → `E-TAMPER`; `MissingDestination` → `E-DEST` |
| `InvalidSourceLeaf` | `RedirectedSourceLeaf` → `E-TAMPER`; `MissingSourceLeaf` → `E-DEST`; `NonCanonicalLeaf` → `E-DEST` |

A two-subtype split was not enough. `InvalidSourceLeaf` carries three distinct
conditions — a leaf **name** that is not canonical (empty, `.`, `..`, a dotfile,
or containing a separator), a source **location** that is not canonical, and a
receipt that is missing or redirected. `ReviewedStateConflict` carries four: an
integrity or type change, a genuine concurrent change, an invalid transaction
path rejected before any I/O, and ordinary unavailability such as a read error.

Forcing four conditions into two buckets produces exactly the harm this rule
exists to prevent: a false tamper alarm on an unreadable file, or "reload and
review again" advice for a path that will never become valid. The taxonomy is
sized to the conditions that exist, and invariant 3 fails on any raise site that
has not chosen one.

**Where one `except OSError` covers two conditions, the site must
discriminate.** `_read_no_follow_bytes` re-raises `FileNotFoundError` and
collapses every other `OSError` into one `CrossScopeError` — so a receipt whose
permission bit is wrong arrives indistinguishable from a symlinked one. Choosing
the integrity subtype there would tell an operator with an unreadable file that
their vault has been tampered with; choosing the ordinary one would silence a
real redirection.

The rule at every such site:

| Observed | Subtype |
|---|---|
| `ELOOP`, `O_NOFOLLOW` rejection, or a non-regular `fstat` | integrity subtype → `E-TAMPER` |
| any other `OSError` | unavailable subtype → `E-UNAVAILABLE` |

`ProposalSourceUnavailable` is a `CrossScopeError` subclass, so every existing
`except CrossScopeError` still catches it and no refusal changes. The same rule
settles the transaction-side site whose message is "could not be opened safely",
which has the identical two-conditions-one-site shape.

Every existing `except` clause continues to catch all of them, so this is a type
refinement and no refusal changes.

The classification lives at each raise site, in code, where it cannot drift from
the condition it describes. An AST test over `app/` fails on any direct raise of
an ambiguous base (§7). Adding a raise site without choosing a subtype is a red
test, not a review finding.

The last two rows matter more than they look. `UnsafeDestinationPath` currently
fires when a module directory is merely absent — a first-class expected state
with a standing E4 regression — which would raise a tampering alarm for a
skipped scaffolding step. `InvalidSourceLeaf` conflates a **symlinked inbox
receipt** with a missing one, and `triage` resolves a destination for every
inbox row, making it the most reachable redirection site in the application.

### Class mapping — normative

This table is a **product contract**, not a source inventory, and belongs here.
The previous revision deleted it while removing line-number inventories. That
was an over-correction: invariant 1 only proves a class does not resolve to
`E-UNKNOWN`, so an implementation mapping `SystemRegistryPathError` to
`E-CONFIG` instead of `E-TAMPER` would pass every test while telling an operator
"the registries cannot be read" about a redirected system path.

The distinction the previous revision missed: **which code a class maps to is a
decision; which line raises that class is a fact about layout.** Decisions belong
in this document. Facts about layout belong in tests.

`exact` means the entry applies only to that class. `mro` means subclasses
without their own entry inherit it. Every member of the closed
`GitTransactionError` family is `exact` by Rule 2.

| Class | Code | Match |
|---|---|---|
| `git_transaction.GitTransactionCommittedError` | `E-COMMITTED` | exact |
| `git_transaction.GitTransactionRecoveryError` | `E-RECOVER` | exact |
| `git_transaction.ReviewedPathIntegrityError` | `E-TAMPER` | exact |
| `git_transaction.ReviewedPathUnavailable` | `E-UNAVAILABLE` | exact |
| `git_transaction.ReviewedStateChanged` | `E-CONFLICT` | exact |
| `git_transaction.InvalidTransactionPath` | `E-INTERNAL` | exact |
| `git_transaction.VaultBusyError` | `E-BUSY` | exact |
| `git_transaction.GitTransactionFailure` | `E-GIT` | exact |
| `git_transaction.GitTransactionError` | `E-GIT` | exact |
| `git_transaction._ApprovalLockCleanupFailure` | `E-GIT` | exact |
| `git_transaction._ReviewedIndexOwnershipConflict` | `E-CONFLICT` | exact, unreachable |
| `scope.RedirectedPathError` | `E-TAMPER` | mro |
| `outbox.ProposalSourceUnavailable` | `E-UNAVAILABLE` | exact |
| `scope.OutOfScopeError` | `E-SCOPE` | mro |
| `outbox.OutboxScopeError` | `E-SCOPE` | mro |
| `outbox.UnreadableProposalRecord` | `E-UNREADABLE` | mro |
| `outbox.StaleProposalSource` | `E-STALE` | exact |
| `outbox.MissingProposalSource` | `E-MISSING` | exact |
| `outbox.ProposalFreshnessError` | `E-STALE` | exact |
| `outbox.OutboxTransactionError` | `E-GIT` | exact |
| `outbox.OutboxDestinationError` | `E-INVALID` | mro |
| `outbox.OutboxError` | `E-INVALID` | mro |
| `proposal_identity.ProposalIdentityError` | `E-INVALID` | mro |
| `destinations.RedirectedDestination` | `E-TAMPER` | exact |
| `destinations.RedirectedSourceLeaf` | `E-TAMPER` | exact |
| `destinations.NonCanonicalLeaf` | `E-DEST` | exact |
| `destinations.MissingSourceLeaf` | `E-DEST` | exact |
| `destinations.MissingDestination` | `E-DEST` | exact |
| `destinations.DestinationError` | `E-DEST` | mro |
| `vault.DestinationRegistryError` | `E-CONFIG` | mro |
| `entities.SystemRegistryPathError` | `E-TAMPER` | exact |
| `entities.RecipientConfigurationError` | `E-CONFIG` | exact |
| `entities.EntityManifestError` | `E-CONFIG` | mro |
| `entities.EntitySelectionError` | `E-ENTITY` | mro |
| `registry.RegistryTransactionError` | `E-GIT` | exact |
| `registry.RegistryError` | `E-REGISTRY` | mro |
| `ingest.base.IngestError` | `E-INGEST` | mro |
| `rename.RenameError` | `E-ADMIN` | mro |
| `fastapi.RequestValidationError` | `E-REQUEST` | exact |

`HTTPException` is deliberately absent from this table and from the traversal
allowlist. `fastapi.HTTPException` subclasses `starlette.exceptions.HTTPException`,
which the framework raises for unmatched URLs, wrong methods, and `StaticFiles`
misses, so a mapping would return 422 with copy about a form for a missing
vendor script. After Rule 6 removes the conversion in `entity_scope`, no
application code raises it at all.

Invariant 1 additionally asserts that **every class in this table resolves to the
code named here**, not merely to something other than `E-UNKNOWN`.

**Abstract ambiguous bases are exempt, and the exemption is earned.**
`CrossScopeError`, `ReviewedStateConflict`, `UnsafeDestinationPath`, and
`InvalidSourceLeaf` carry no entry, because a class that is never raised has no
honest single description — that is precisely why they were split. Invariant 1
skips exactly these four; invariant 3 proves they are never raised. The
exemption is therefore backed by a test rather than asserted, and removing
invariant 3 would break invariant 1.

`E-UNAVAILABLE` and `E-INTERNAL` exist because two refined subtypes had no
truthful code. `ReviewedPathUnavailable` is an ordinary read failure, so telling
the operator "the reviewed files changed — reload and review again" would be a
fabrication. `InvalidTransactionPath` is raised by path validation before any
I/O and serves registry transactions and internal plan paths as well as
proposals, so `E-INVALID`'s "this proposal record is not valid" would be wrong
in most of its uses and would send the operator after their data for a defect in
ours.

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
| `E-UNAVAILABLE` | A file involved in this action could not be read. Nothing was changed. Try again; if it persists, check that the file is readable. |
| `E-INTERNAL` | The action was refused by an internal safety check. Nothing was changed. This indicates a defect rather than a problem with your data. |
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

### Rows carry capabilities, not kinds

An earlier draft classified each row as `readable` / `unreadable` / `skipped`.
That model cannot express the states the code actually produces. A source
receipt containing one non-UTF-8 byte, for example, yields a record that:

- **cannot be approved** — `approve` decodes the receipt and refuses;
- **can be rejected** — `reject` never reads the source; and
- **does not poison the listing** — `load_proposals` reads only the record.

No single kind describes that. So a row carries **capabilities**:

```text
project_outbox(scope) -> OutboxListing
OutboxListing(rows, blocked)
OutboxRow(proposal | None, diff | None, error | None,
          can_approve: bool, can_reject: bool)
```

`blocked` is reserved for conditions that **actually poison `load_proposals`**,
because those are the conditions under which no action in the entity can
succeed. A row that merely cannot be diffed or cannot be approved withholds its
own controls and leaves every other row alone.

This keeps the §7 claim honest: the projection describes coupling that the
strict loader already enforces, and never invents coupling of its own. Under the
kind model, an undiffable row would have blocked an entity whose other proposals
would have approved perfectly well — the projection *creating* the coupling.

### The three helpers

The strict loader's per-file body is extracted into `_read_record(path)`,
`_validate_record(scope, path, record)`, and `_render_diff(scope, proposal)`,
called by both the strict loader and the projection. The validation logic moves;
it does not change or fork.

`_render_diff` takes an **already-validated** record and performs only the
`difflib` work. `preview_diff` keeps its public contract: strict reload first,
then delegate. The projection calls `_render_diff` directly on the record it has
just validated, and calls neither `get_proposal`, `load_proposals`, nor
`preview_diff` — asserted by patching all three to raise.

`_render_diff` reads the source receipt, so it may raise `UnicodeDecodeError`,
`OSError`, and the scope errors from `scope.resolve_stored`. A row whose diff
fails renders with a described error in place of the diff, keeps `can_reject`,
loses `can_approve` (the same decode refuses in `approve`), and does not set
`blocked`.

### Three phases, three handling rules

The projection does three separable things per file, and conflating them
produced a contradiction in the previous revision: diff failures became row
errors while "every other exception propagates", yet a broken registry was also
said to participate in multi-row aggregation — which propagation makes
impossible, because it aborts the projection.

Each phase is handled explicitly.

**Phase 1 — record read and schema.** A file that is not a proposal at all.
Caught, per row, as an unreadable record. This is the family that poisons the
strict loader, so its rows set `blocked`.

The family must be **exact and complete**, because anything it misses escapes
the promised blocked listing. `UnreadableProposalRecord` therefore covers every
way reading or shaping a record can fail:

| Condition | Escapes today as |
|---|---|
| unparseable YAML | `yaml.YAMLError` |
| non-mapping record | `OutboxDestinationError` |
| **non-UTF-8 record bytes** | `UnicodeDecodeError` |
| **record read failure** | `OSError` |
| identity failure | `ProposalIdentityError` |
| unknown action | `OutboxDestinationError` |
| **missing or malformed required field** | `OutboxDestinationError` from `_to_proposal` |

The three in bold were absent from the previous revision's family and would have
escaped as `E-UNKNOWN` at 500 — a blank screen, from one non-UTF-8 byte in a
proposal file. The strict loader raises them too, so they genuinely poison, and
they belong here.

**Phase 2 — destination validation.** Registry and path conditions. These
**propagate**. They are not record-local: a broken `archetypes.yaml` or a
redirected outbox is a property of the vault, not of one file, and Rule 1 exists
to recover `E-CONFIG` and `E-TAMPER` from them. The projection aborts and the
route renders the described condition as a **listing-level** alert with no rows.

This replaces the previous revision's "aggregate the highest-precedence
description across rows", which could not work: a propagating exception ends the
projection, so there is no completed listing to aggregate over. One broken
registry reads as one broken registry because it is reported once, from the
abort — not because rows were compared.

**Phase 3 — diff rendering.** These are **row-local and non-poisoning**: the
record is valid, the strict loader is unaffected, and every other proposal still
approves. Such a row renders with a described error in place of its diff, keeps
`can_reject`, loses `can_approve`, and does **not** set `blocked`.

`_render_diff` must read the receipt **through the same safe-read boundary
`approve` uses**, and translate failures into the same domain types. Otherwise
the row and the button describe different conditions for one physical cause:
raw `UnicodeDecodeError` and `OSError` resolve to `E-UNKNOWN`, while `approve`
turns the identical conditions into `MissingProposalSource`, `CrossScopeError`,
and `OutboxDestinationError`. The listing would say "an unexpected error was not
handled" about a file whose approve button says "source is missing".

The translation is therefore normative, not incidental:

| Condition | Type | Code |
|---|---|---|
| receipt absent | `MissingProposalSource` | `E-MISSING` |
| receipt redirected or unsafe | `RedirectedPathError` | `E-TAMPER` |
| receipt not decodable as UTF-8 | `OutboxDestinationError` | `E-INVALID` |
| receipt unreadable for any other reason | `ProposalSourceUnavailable` | `E-UNAVAILABLE` |

Row and button then agree by construction: both read through one boundary and
both describe what that boundary raised. The boundary's types are
`CrossScopeError` subtypes, not `InvalidSourceLeaf` subtypes — it is the shared
safe-read helper that raises here, and keeping its base unchanged is what keeps
every existing `except` clause catching it. `RedirectedSourceLeaf` remains the
integrity subtype for the destination-resolution sites, which have a different
base.

### Delete proposals are skipped, exactly as today

Registry delete proposals live in the same `outbox/` directory the projection
globs, and the strict loader tolerates them by skipping `action: delete` **after**
the identity check. The projection preserves that skip precisely: a well-formed
delete record renders nothing, counts as nothing, and blocks nothing.

This matters because a delete proposal is written on every delete-preview click,
so abandoned previews accumulate. Only a **malformed** delete record — one
failing the read or identity check that precedes the skip — is unreadable.

### S4 revalidation is preserved

S4 required a stored record to be revalidated before its diff is shown, and
achieved it by re-reading the whole listing — which is why one bad file poisons
every row. The projection performs the same revalidation on the same record
without the global loop. What is removed is the global re-entry, not a check.

The displayed diff carries **no approval authority**. Approval revalidates from
scratch through the untouched strict path. That is honest about the
implementation and concedes something S6 must not paper over: **nothing binds an
approval to the bytes the operator reviewed.** See §12.

### The blocked listing

When a genuinely poisoning record exists, every action in the entity fails
through the untouched strict loader. Rendering controls in that state would
present buttons silently guaranteed to fail, with `E-INVALID` advising the
operator to create a new proposal — advice that cannot resolve the condition.
Today's blank screen is at least honest; a listing of dead buttons is not.

So when `blocked` is true, valid rows render read-only — id, destination, and
diff, so the operator can see what is pending — with no classification control
anywhere, and one listing-level notice carrying the **described** condition
stating that nothing in this entity can be approved or rejected until it is
resolved outside the Console.

No check is weakened; the strict loader still refuses everything, exactly as
today. The projection only stops lying about it.

An unreadable row carries no filename. Per Rule 9 the filename is
attacker-controlled text and is not echoed.

### The taxonomy stays out of the service

`OutboxRow.error` carries the **raw exception**, never a code. `app/main.py` —
the presentation composition root — calls `describe()` on it, so a propagating
`E-CONFIG` or `E-TAMPER` keeps its own description and its own wording.
`E-UNREADABLE` reaches rows through the normative map, since phase 1 raises the
typed `UnreadableProposalRecord`. The composition root does not select codes by
hand; it calls `describe()` on whatever the row carries.

There is no cross-row aggregation. Phase 2 conditions abort the projection and
are reported once from that abort, so a broken registry reads as a broken
registry because it is the only thing reported — not because rows were compared.
A completed listing contains only record-local and diff-local conditions, each
described on its own row.

`E-UNREADABLE` is distinct from `E-INVALID` because the two demand opposite
actions. `E-INVALID` says the proposal you tried to approve is bad — create a
new one, and rejecting it clears it. `E-UNREADABLE` says a file cannot be parsed
at all — creating another proposal does not remove it, and it cannot be rejected
through the Console, because `reject` resolves through `get_proposal` and the
strict loader. Making such a file deletable would be a new destructive contract
over a path the domain never validated, and is out of scope.

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

The test **enumerates every full-page route** from `app.routes` and asserts the
configuration in each rendered response. One response is exempt and must be
fixed rather than excused: `triage_default` returns a bare `HTMLResponse` with
no `<head>` when no bundles exist, so it cannot carry the tag. It becomes a
template. Asserting it on one page is the BUILD.md §5 failure
mode inside the guard written to prevent it.

### The override must not swap framework error bodies

`{"code":"[45]..","swap":true}` matches every non-2xx, including the responses
Rule 6 deliberately keeps out of the taxonomy. An HTMX request to an unmatched
URL or with a wrong method would swap Starlette's raw `Not Found` or
`Method Not Allowed` body into `#outbox-list` or `#diff-{index}` — framework
text reaching the operator through the swap path, which §6 forbids through the
render path.

A `default` exception handler for `StarletteHTTPException` therefore replaces
the **body** of any framework status the taxonomy does not own, when
`HX-Request` is present, and leaves the framework's own plain response untouched
otherwise.

**The framework's status is preserved, not the code's.** An unmatched URL under
`HX-Request` returns 404 with a safe body — not the 500 that `E-UNKNOWN`'s page
status would imply. This is a deliberate exception to the severity rule and the
only one: that rule governs outcomes the taxonomy owns, and these are precisely
the responses Rule 6 keeps out of it. Applying both would require one response
to be 404 and 500 at once.

The body is described text, so nothing framework-authored reaches HTML; the
status stays truthful to what the framework decided. Rule 6's exclusion holds —
these are still not *mapped* to `E-REQUEST` — while the swap the exclusion
overlooked is closed.

---

## 5. Rule 5 — Typed route handling only

Routes catch **declared domain families**, never bare `Exception`. A blanket
catch would launder programmer errors into 200 fragments, which is the opposite
of S6's purpose.

Every route declares its family explicitly in code, and invariant 6 enumerates
routes from `app.routes` to check them. Two are worth stating here because they
are not obvious from the route body:

- The outbox routes need **more than `OutboxError`**. `load_proposals` raises
  bare `CrossScopeError` for a redirected outbox or proposal leaf, which today
  escapes `except OutboxError: pass` entirely. Their family is
  `(OutboxError, CrossScopeError, DestinationRegistryError)`.
- `triage`'s existing tuple omits `CrossScopeError`, which
  `resolve_classification_destination` can raise through `scope.resolve`. Adding
  it is required, not optional — without it the per-row handling is bypassed and
  the whole page fails.

A route whose declared family does not cover something invariant 6 can make it
raise is a failing test, not a runtime surprise.

The global handler catches only what escapes a route, describes it, and returns
its page status — 500 for `E-UNKNOWN`. It never returns 200. A test asserts it
is not reached for any described error, so relying on it is a failure rather
than a silent default.

Two renderers share the one table. Selection is by **route shape first, then
`HX-Request`**: a route with no full-page template always uses the fragment
renderer. `propose`, `outbox_approve`, `outbox_reject`, and the two registry
POSTs have no full-page template, so their full-page branch is unreachable in
production and would exist only to be exercised by tests.

Choosing this resolves an ambiguity two rounds left open and keeps every
existing route test at its current status, since no test sends `HX-Request`. It
costs one added condition in the selector and removes a class of finding where
a test asserts a status the design never intended to serve.

Fragment status follows **severity**, not code:

- `severity = refusal` → **200**. The refusal is expected and the body carries
  it.
- `severity = attention` → the code's **declared page status**. HTMX still swaps
  it under the `responseHandling` override, so the operator sees the message
  while monitoring sees an honest status.

A full page always returns the code's page status.

**A successful render is 200 even when it carries an attention code.** A blocked
outbox listing that successfully lists proposals, or a triage page with one
per-row `E-TAMPER`, returns 200: the request succeeded and the page is the
correct response. Status describes the request, not the worst condition the page
happens to describe. Only a response whose *whole content* is an error notice
carries that error's status.

When one response carries several codes — an approve refusal rendered into a
blocked listing — the status is the refusal's, because that is the outcome of
the request being answered. The listing's condition is already visible in its
own notice.

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

| Site | Required outcome |
|---|---|
| `shell` | `E-CONFIG` page |
| `pulse` | unchanged |
| `triage_default` | `E-CONFIG` page |
| `triage` | per-row code and message; tuple extended to cover scope and tamper conditions |
| `propose` | described alert into the existing `#diff-{index}` target |
| `outbox_screen` | the projection |
| `outbox_approve` | described inline |
| `outbox_reject` | described inline |
| `registry_products` | `E-CONFIG` inline |
| `registry_delete_preview` | `E-CONFIG` inline |
| `registry_delete_execute` | template on both branches |
| `entity_scope` | `EntitySelectionError` to its dedicated handler |
| global handler | describes, returns the code's page status |

The **Current** column is deleted. It described what each route does today — a fact about source that drifts the moment anything changes, and the last such
inventory in this document. What each route must *do* is a contract and stays;
what it does *now* is discoverable by reading it.

The site list itself is not an inventory to maintain: invariant 6 enumerates
routes from `app.routes`, so a route added later is covered without editing
this table, and a route present here but absent from the app fails the same
test.

`E-CONFIG` renders **inline on a fragment request and as a page on a full-page
request**, like every other code. Surface follows the request, not the code; the
previous design's per-code surface column contradicted its own `triage` row.

### Boundary conversions

Registry-validity conditions currently surface as stdlib exceptions and would
reach the operator as "an unexpected error was not handled."

Earlier drafts listed the raising lines. Two rounds of review found the ranges
wrong — one cited a function containing no YAML at all, while the reader that
actually raises the promised error was cited by no row. The list is therefore
replaced by a rule and a shape test:

**Every shared registry reader normalizes failures that already escape it, and
changes nothing else.**

The previous revision said "unparseable, absent, or wrongly shaped → `E-CONFIG`
for every reader". That was wrong, and it violated this design's own core
constraint by inventing refusals:

| Reader | Existing contract on absence |
|---|---|
| `products_for` | returns `[]` |
| workspace counting | counts zero |
| `books.db` counting | counts zero |
| `split_front_matter` | returns `{}` for malformed or absent front matter |
| front-matter scanning | skips a file it cannot read |

These tolerances are deliberate. Converting them to `DestinationRegistryError`
would make absent registries fatal where they are currently neutral — a new
refusal decision, which S6 does not make. Whether those registries should be
mandatory is a real question and belongs outside S6.

So the rule is narrower and strictly presentational: **a failure that already
escapes a reader is normalized to `DestinationRegistryError`; a failure the
reader already absorbs continues to be absorbed.** S6 changes the *type* of
something already fatal, never the *fatality* of something already tolerated.

What escapes today, and therefore converts:

| Shape | Example |
|---|---|
| unparseable | invalid YAML in a registry the reader does parse; a corrupt SQLite file |
| **wrongly shaped but valid** | a list where a mapping is expected, a scalar where a list is |

The second is the one enumerations keep missing. `yaml.safe_load(...) or {}`
guards the empty case, not the wrong-type case, so a syntactically valid file
parses cleanly and then raises `AttributeError` or `TypeError` on access — the
likelier hand-editing mistake, and the one that would otherwise reach the
operator as `E-UNKNOWN`.

Each conversion narrows to the specific parse or access it guards. A blanket
`except (AttributeError, TypeError)` around a whole function would mask genuine
programmer errors, which Rule 5 forbids.

The closing test injects each escaping shape through each reader and asserts
`E-CONFIG` rather than `E-UNKNOWN`. It also asserts each reader's **absorbed**
cases still return their tolerant value, so a future change that quietly makes
an absent registry fatal fails here.

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

The rule is **no template hand-builds an `hx-vals` mapping**, closed by a test
over `templates/` rather than by a list of offenders. An earlier draft named two
templates; there are four, and the two it missed are in `outbox_list.html`, the
template S6 rewrites most heavily. Those two are not exploitable today because
the proposal id grammar constrains them — but Rule 8 is a pattern rule, and a
pattern rule enforced by a two-row list is not enforced.

A further test asserts that a slug containing quotes, braces, and a second `id`
key yields exactly one `id` in the parsed `hx-vals`, equal to the previewed
proposal.

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

## 7. Source-derived invariants

Six review rounds confirmed the rules and rejected the enumerations. Every
inventory this design once carried in prose — tamper sites, boundary-conversion
lines, `hx-vals` offenders, route lists — was wrong at least once, and each was
wrong in the direction of omission, which is the one direction a written list
cannot detect.

So the design carries **rules and normative contracts**; the enumerations live
in tests that derive them from source at run time. An omission becomes a red
test rather than a review finding.

Nothing here is a second manifest. Each invariant reads the thing it constrains.

**1. The map is canonical.** `app/console_errors.py` is the single
exception-class → outcome map. No dotted-string keys, no parallel list. A test
walks the exception hierarchy under `app/` and fails on any class that resolves
to `E-UNKNOWN`, so a new exception is unmapped until someone maps it.

**2. Closed families are exhaustive.** A test walks
`GitTransactionError.__subclasses__()` transitively and fails on any subclass
without its own exact entry, **except the abstract ambiguous bases of §2**,
which invariant 3 proves are never raised. Without that clause the test is red
on day one: `ReviewedStateConflict` is a direct subclass and deliberately
carries no entry. Same walk for the allowlist's membership.

**3. Ambiguous bases are never raised directly.** An AST test over `app/` parses
every `raise` statement and fails when the raised type is one of the four
ambiguous bases. This is what closes the tamper classification: the choice is
made at the raise site, in code, and a new site is unclassified until it picks a
subtype. No line numbers anywhere.

**4. Structured readers declare their category.** The guard cannot be "anything
that calls `yaml.safe_load`" — that also finds proposal records, delete
proposals, front matter, and the rename database, none of which are registries
and none of which may become `E-CONFIG`. A malformed proposal is
`E-UNREADABLE`; a malformed `archetypes.yaml` is `E-CONFIG`; conflating them
reproduces the defect Rule 1 exists to fix.

So every structured reader declares a **category** at its definition:

| Category | Failure becomes |
|---|---|
| `registry` | `DestinationRegistryError` → `E-CONFIG` |
| `proposal` | `UnreadableProposalRecord` → `E-UNREADABLE` |
| `front-matter` | absorbed, per its existing tolerant contract |
| `admin-db` | its existing administrative error |

The structural guard finds candidates by what they do — parsing YAML, opening a
`system_path` result, connecting to a SQLite file — and fails on any that
carries no category. It does not infer which category; it only refuses silence.
Conversion tests then apply to the `registry` category alone, and separately
assert that a `proposal`-category failure yields `E-UNREADABLE` rather than
`E-CONFIG`.

**5. No template hand-builds `hx-vals`.** A test scans `templates/` for
`hx-vals` attributes and fails on any whose value is not a single
`{{ ... | tojson }}` expression.

**6. Route coverage derives from the app.** Route tests enumerate `app.routes`,
filtered to routes whose endpoint function is defined in `app.main` — which
excludes FastAPI's OpenAPI and docs routes and the mounted `StaticFiles`
application, none of which can satisfy a Console requirement.

A route declares its catch family with a `@console_route(catches=(...))`
decorator on the handler, and the same structural guard applies: an AST test
fails on any handler in `app.main` that is registered with FastAPI but carries no
declaration, on any handler whose body contains a bare `except Exception`, and on any
declaration whose tuple contains `Exception` or `BaseException` — checking only
the body would still permit a declared catch-all, which is the same laundering
by another route.
Tests then read each declaration, inject each member of the declared family, and
assert the route describes it rather than reaching the global handler.

This does not claim tests can *discover* which exceptions a route can reach —
they cannot. It claims that whatever a route declares it handles, it must
actually handle, and that a route cannot silently declare nothing.

If a future finding is "the design's list omits X", the correct fix is to delete
the list and add the invariant that would have caught X.

## 8. Test matrix

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
- `DestinationRegistryError` inside `OutboxDestinationError` resolves to
  `E-CONFIG`, not to the wrapper's `E-INVALID`. (`EntitySelectionError` inside
  `HTTPException` is no longer a case: Rule 6 removes that wrapper, and the
  exception reaches its own handler directly.)
- A class not on the allowlist never has its cause read.
- `__context__` is never traversed: an error raised while handling another
  resolves to itself.
- Chain depth is bounded at 4.
- Every structural invariant in §2 holds across the whole table.
- No **domain or service** module imports `console_errors` or `console_render`.
  The presentation composition root — `app/main.py` and the templates it renders
  — must import them; it is the layer that turns an exception into a response.
  The test asserts on `app/` excluding that root, so it forbids the cycle
  without forbidding the intended dependency.

### Routes

- Each route enumerated by invariant 6, with each member of its declared catch
  family injected, returns the expected status, renders the expected code and
  message, and no raw exception text.
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
- Every full-page route — enumerated by invariant 6's filter, so framework and
  static routes are excluded — contains the `htmx-config` meta tag.
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

S1-S5 tests whose subject is a **refusal decision, an isolation guarantee, or a
state proof** remain unchanged in substance. Route tests whose
explicit purpose was to defer S6 presentation are expected to change, and are
listed individually rather than discovered during implementation:

| Test | Why it changes |
|---|---|
| `tests/test_app.py:477-503` | asserts `role="alert"` is absent for an injected transaction error — S6 makes it present |
| `tests/test_app.py:588` | asserts the raw string `"registry deletion transaction failed"` renders — the disclosure boundary forbids it |
| `tests/test_app.py:393` `test_tampered_proposal_form_writes_nothing` | asserts `status_code >= 400` in six parametrized cases. `propose` is `fragment-only`, so once Task 11 gives it a route-level catch every one of the six describes to refusal severity and returns **200**. **Status expectation only** — its three state proofs (`HEAD` unchanged, entity bytes unchanged, no proposal written) stay verbatim, and they are the test's actual subject. It additionally **gains** an observable-refusal assertion: the response must carry `role="alert"` and the described code and message for the condition, and must not echo the submitted value. Swapping `>= 400` for `== 200` alone would weaken the test, since a 200 carrying no alert would pass; the added assertion is what keeps the refusal proven now that the status no longer proves it. The six parameters are six cases of this one declared presentation regression, not six new exceptions. Owned by Task 11. |
| `tests/test_app.py` `test_concurrent_outbox_requests_keep_entity_diffs_isolated` | forces two requests to overlap by monkeypatching `main.load_proposals` onto a `threading.Barrier(2)`. Task 12 moves the outbox routes onto `project_outbox`, which by design §3 **never** calls `load_proposals`, so the barrier stops firing and the test passes while proving nothing — measured at 0 barrier hits against an expected 2. **Monkeypatch target only** (`load_proposals` → `project_outbox`): the test's subject, its isolation assertions, and its concurrency mechanism are preserved verbatim, and it **gains** an explicit `hits == 2` assertion so it can never silently go inert again. This row exists to *restore* an S1-S5 isolation proof that S6 would otherwise hollow out, not to relax one. Owned by Task 12. |

Rule 5's route-shape-first selection settles what earlier drafts left open:
`propose`, `outbox_approve`, `outbox_reject`, and the two registry POSTs always
use the fragment renderer, so refusals on those routes stay at **200**. The two
tests earlier drafts predicted would break — the stale-source refusal and the
cross-entity isolation test — do **not** change, which is the main reason
route-shape-first was chosen over header-only selection.

An earlier revision of this section also claimed "every existing status
assertion therefore holds". **That was false**, and it is why this table
carried only two rows. It was an inference from the selection rule, never
checked against the test file. `test_tampered_proposal_form_writes_nothing`
asserts `>= 400` precisely because `propose` has no route-level catch **today**;
the moment Task 11 gives it one, all six cases return 200. The third row above
records it.

The lesson is the one this project keeps relearning: an enumeration justified by
reasoning rather than by reading is wrong in the direction it cannot see.

This table is an **explicit allowlist, not a numerical cap.** A row is added
when S6 provably changes what a test observes; the count is an outcome, never
the constraint. The binding rule is the one below, and where the two appear to
conflict — as with the concurrency row above, where leaving the test untouched
would have silently destroyed an isolation proof — **preserving the proof
wins.** A test that still passes while no longer exercising its subject has not
been preserved; it has been hollowed out, which is worse than a visible failure
because nothing reports it.

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

## 9. Completion gates

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

## 10. Non-goals

No new route, screen, dashboard card, drag-drop UI, batch UI, or workflow. No
change to any validation, refusal, or commit decision. No new dependency,
JavaScript framework, schema, daemon, or deployment behavior. No adapter or
ingest surface. No LLM or external-service call in the request path. No new
logging subsystem. Existing OneOS, Command Center, workspace, and Blocks /
Modules terminology preserved exactly.

## 11. Known limitations

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

## 12. Unresolved: the review gate does not bind reviewed content

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
refuse visibly on mismatch.

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
