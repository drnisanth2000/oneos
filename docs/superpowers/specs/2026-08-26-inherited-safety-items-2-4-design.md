# Inherited Safety Items 2–4

**Status:** APPROVED — conversational design and written artifact approved by
the product owner on 2026-08-26. Implementation remains unstarted and belongs
to the separately handed-off external-agent tasks below.

**Base:** freshly fetched merged `origin/main` at
`ee591549249ca798de2dae5b9156a96895917f5e`. Fresh public baseline:
`uv run python -m pytest -q` → 1,476 passed.

**Authority:** `AGENTS.md`, `BUILD.md` Safety Foundation, and
`docs/STATUS.md` “S7 inherits these from S6”.

**Scope:** the three separately sequenced pre-live-trial items inherited from
S6: prose-leakage enforcement (item 2), remaining dependency-time filesystem
failure shapes (item 4), and route declaration completeness (item 3).

## Objective

Finish the remaining Safety Foundation work without reopening S7 or adding a
new product surface:

1. make exact private registry identifiers in tracked Markdown fail the
   existing publication audit;
2. turn an unavailable configured vault root and an unreadable entity manifest
   into existing, truthful Console outcomes before the global fallback; and
3. make route failure declarations complete against an explicit graph of
   route-facing service and dependency contracts.

The three items are sequential integration boundaries, not one implementation
branch. They are implemented and merged in the order **2 → 4 → 3**. Item 2
closes the publication gate first. Item 4 defines the final typed dependency
outcomes. Item 3 then verifies route declarations against those settled
outcomes.

## Shared decisions

1. **No new feature surface.** No dashboard, parser, workflow, registry,
   schema, daemon, dependency, or operator action is added.
2. **Existing taxonomy copy is reused where it is truthful.** Item 4 maps the
   configured vault root disappearing to `E-TAMPER` and entity-manifest read
   failure to `E-CONFIG`; it does not invent another error code.
3. **The global fallback is not a successful safety response.** It remains the
   last boundary for unforeseen programmer defects. A known operational
   failure is either typed and handled or explicitly documented as a deliberate
   `E-UNKNOWN` outcome.
4. **No private material crosses the cloud boundary.** An external agent works
   only with this public repository and synthetic fixtures. It never receives
   a live vault path, registry value, database, history, proof snapshot, or
   private decision authority.
5. **Independent review and mutation evidence are mandatory.** Every
   protection is deliberately broken, its named regression must go red, the
   exact pre-image is restored, and the regression and full suite must return
   green. A green suite without the red step is not completion evidence.
6. **Each item gets a fresh branch, task, worktree, pull request, and merged
   baseline.** The next item does not start until its predecessor is merged
   into freshly fetched `origin/main` and the full public suite passes there.

## Item 2 — prose-leakage enforcement

### Problem

`tools/public_repo_audit.py` already loads instance-derived terms from the live
registries at the trusted local boundary. Long terms are matched in text, while
short terms are currently checked only as path components or approved
structured values. Consequently an exact short identifier can appear in
ordinary Markdown prose and pass. The current regression explicitly permits
that shape.

### Design

Extend the existing public-repository audit; do not add a second scanner.
Whenever a vault is supplied, every tracked Markdown blob in every selected
revision is checked for exact long **and short** registry-derived identifiers.
The existing path and structured-value rules remain in force for other file
types.

An exact token uses the audit's existing identifier boundaries: the characters
immediately before and after the term must not be ASCII letters or digits. For
example, an exact `abc` token fails while `xabcx` remains allowed. Markdown
front matter and prose are scanned alike. The check is deliberately strict:
there are no term, file, line, quotation, code-fence, generated-file, or
documentation exemptions. The accepted cost is that a short private identifier
that is also an ordinary word must be rewritten in public documentation.

The scanner reports only the existing safe location form—revision, repository
path, and line number—and the generic `instance-value` category. It must never
include the matched registry term, source registry path, or surrounding line in
the finding or exception text.

The vault-free public CI command keeps working without live terms. Synthetic
tests create a synthetic vault and prove both current-tree and history scans.
The trusted local boundary runs the existing combined command with
`ONEOS_VAULT`; no live value enters CI configuration, fixtures, logs, or this
repository.

### Acceptance

- An exact short identifier in tracked Markdown prose fails current-tree and
  history audit modes.
- The same characters inside a longer alphanumeric token pass.
- Existing short-term path-component and structured-value findings still fail.
- A finding exposes only its safe location and generic message.
- No exemption or new dependency exists.
- Mutating away only the Markdown short-token check makes its named test red;
  restoring the exact implementation makes it green.

## Item 4 — dependency-time filesystem outcomes

### Problem

Two realistic post-startup changes currently escape before a route body can
handle them:

- the configured vault root is renamed, unmounted, or otherwise ceases to be a
  directory, so `vault_root()` raises a raw `RuntimeError`; and
- `_system/entities.yaml` cannot be read because access is denied, so
  `EntityCatalog.load()` leaks `PermissionError`/`OSError`.

Both arise while FastAPI resolves `EntityScope`. Route-level `except` clauses
cannot answer them, and the global fallback renders `E-UNKNOWN`.

### Design

Add a narrow domain exception, `VaultRootUnavailable`, for a configured vault
root that was expected but is unavailable at request time. Its message is
constant and contains no configured path or environment value. An unset
`ONEOS_VAULT` at process startup remains a startup-configuration failure; this
item governs the post-startup loss of a configured root.

Map `VaultRootUnavailable` exactly to the existing `E-TAMPER` outcome. That
copy already tells the operator that a managed folder is missing or moved,
keeps reviewed actions read-only, asks them to restore it, and explains that an
intentional whole-vault move requires updating `ONEOS_VAULT` and restarting.
No symlink is proposed or followed automatically.

`EntityCatalog.load()` converts manifest read failures, including
`PermissionError`, into `EntityManifestError` with a constant, non-disclosing
message. The existing typed dependency handler and exact taxonomy mapping then
render `E-CONFIG`. YAML-shape failures keep their present behavior.

Register a dedicated dependency-boundary handler for
`VaultRootUnavailable`, parallel to the existing `EntityManifestError`
handler. Both handlers render through the normal Console composition path and
`status_for`; neither reads exception text into the response. The global
`Exception` handler is excluded from the proof.

Typed dependency handling must not retire lower route guards. A failure that
can occur inside a route body must remain in that route's declared catch family
even when an application-level handler could produce the same visible output.

### Verification boundary

Tests start the app against a valid synthetic vault, then alter the filesystem
and request every affected entity-scoped Console surface. They assert the exact
code and status, a non-empty safe alert, absence of raw paths and raw exception
messages, absence of actionable controls, no call to the global fallback, and
byte-identical synthetic vault state after the request.

The manifest-permission regression uses a real permission change on hosts where
the process is subject to it. A deterministic reader-boundary injection also
pins the conversion so privileged CI accounts cannot make the proof vacuous.
Both variants restore permissions in `finally` cleanup.

### Acceptance

- A configured root lost after startup renders `E-TAMPER`, never `E-UNKNOWN`.
- An entity manifest that cannot be read renders `E-CONFIG`, never
  `E-UNKNOWN`.
- No response leaks the configured root, manifest path, operating-system error,
  or submitted value.
- Every affected surface is read-only and the synthetic vault is unchanged.
- Removing either typed conversion or either dependency handler makes its
  named regression red.
- Removing an existing lower route catch still makes the lower-guard pin red.

## Item 3 — declaration completeness

### Problem

The current declaration-driven totality sweep injects only the exception types
already named in each route's `ConsoleRoute.catches`. It detects drift between
a decorator and the route body's `except`, but it cannot detect a missing type:
an exception omitted from both places is never injected. A broad typed
application handler can also make a lower catch redundant at runtime while
silently erasing the lower declaration's evidence.

Python cannot soundly infer every exception a dynamic call graph might raise.
This item therefore proves a precise, reviewable boundary: every **known domain
failure** exported by a route-facing service or FastAPI dependency is covered
through an explicit, closed failure-contract graph. Unforeseen programmer
defects remain the global fallback's job and are not misrepresented as a
statically proven finite set.

### Contract model

`app/console_routing.py` remains pure metadata and gains an immutable
`FailureContract`. A contract names:

- the exact domain exception classes directly exported by that boundary;
- other contracted boundaries it calls, forming the transitive graph; and
- any exact known exception deliberately left to `E-UNKNOWN`, paired with a
  non-empty written reason.

A failure-contract decorator attaches this metadata to route-facing service
entry points and FastAPI dependencies without changing runtime exception
handling. `ConsoleRoute` points to the route-body service contracts it uses.
FastAPI dependency contracts are derived from the endpoint's actual dependency
metadata, so dependency coverage is not a second hand-maintained route list.

The contract graph is closed over the explicitly inventoried route-facing
services and dependencies. Every inventoried boundary must carry a contract;
removing the decorator is a structural failure. Contract-to-contract calls are
checked in executable code, not comments or docstrings, so removing a declared
edge or adding an undeclared contracted call is also a structural failure.
Cycles, `Exception`, `BaseException`, non-exception values, duplicate
dispositions, and an empty deliberate-unknown reason are refused.

### Route completeness rule

For each Console endpoint, take the transitive union of its body-service and
dependency contracts.

- A body-service failure must be covered by the route's own `catches` family,
  or be an exact deliberate-unknown entry. An application handler does **not**
  satisfy this rule; this preserves the lower route guards.
- A dependency failure must be covered by a non-global typed FastAPI handler,
  or be an exact deliberate-unknown entry. The global `Exception` handler never
  counts.
- A failure cannot be both safely handled and deliberately unknown.
- Subclass coverage follows Python's actual catch/handler behavior, but the
  contract itself names the exact exported class so a newly introduced subtype
  cannot appear by implication alone.

The existing runtime catch tuples and Console taxonomy remain the authority for
operator behavior. Contracts describe and verify that behavior; they do not
catch, translate, suppress, or render exceptions.

### Evidence

The main proof is structural and lives with the existing Console invariants.
Representative real-filesystem route tests remain mandatory because metadata
can be wrong in the same way code comments can be wrong. The filesystem tests
from item 4 become required members of this completeness evidence rather than
being replaced by it.

Mutation evidence must include at least:

1. remove a known domain exception from a route catch while its body contract
   still exports it;
2. remove a failure contract from an actual FastAPI dependency;
3. remove an edge between two contracted service boundaries;
4. add a known exported exception without a route or dependency disposition;
5. let a broad typed application handler exist while deleting the narrower
   route catch it must not retire; and
6. replace a named deliberate-unknown class or its written reason with a broad
   catch-all.

Each mutation names the exact test and intended diagnostic that must go red,
then restores the target file byte-for-byte.

### Acceptance

- Every inventoried route-facing service and dependency has one immutable
  failure contract.
- Every Console route covers the transitive known domain failures reachable
  from its body and dependency contracts.
- The global fallback never satisfies completeness.
- Typed dependency handlers cannot silently retire lower route declarations.
- Contract checks inspect executable structure rather than prose.
- Representative real-filesystem failures resolve to the same outcomes the
  contracts predict.
- The required mutations are red under mutation and green after byte-identical
  restoration.

## External-agent and trusted-local sequence

For each item, the external agent receives the approved design, its own
implementation plan, the recorded merged `origin/main` SHA, exact in-scope
files, public tests, mutation requirements, and stop conditions. It implements
only on its fresh `codex/` branch using synthetic fixtures and returns a clean
handoff with commits and public evidence.

The external agent stops on dependency, schema, convention, or security-boundary
changes; destructive actions; deployment; an unresolved product choice; or any
need for private material. It does not push, open a pull request, merge, delete
branches, remove worktrees, or access Grey Matter unless the product owner
separately authorizes that action.

The trusted local reviewer independently checks the diff and every factual
claim, reruns the public suite and mutation campaign, and then performs the
private read-only gates from `BUILD.md` with opaque pre/post preservation proof.
Only after that review may the product owner authorize push, pull request, and
merge. The next item begins from the freshly fetched merge commit.

## Explicitly out of scope

- Any change to S7 review tokens, receipts, quarantine, managed-directory
  boundaries, or operator workflows.
- Linux no-overwrite verification; S7 records it as an accepted unexercised
  platform limitation.
- Email, PDF, mailbox, browser-extension, MCP, CLI, messaging, parser, routing,
  or dashboard work.
- Moving the repository or vault, changing `ONEOS_VAULT`, creating symlinks, or
  designing folder migration.
- Automatic repair, cleanup, retry, or destructive recovery.
- New dependencies, schemas, registry values, conventions, secrets, or private
  fixtures.

## Completion

The inherited work is complete only after all three pull requests are merged
in order and a fresh final `origin/main` baseline passes. Each item records its
own public test selection, mutation RED→GREEN evidence, independent review,
Gitleaks and public audit results, private 37-test and `check_v2` results, and
opaque byte-preservation comparison. Counts without their commands and
mutations without their exact failing tests are not completion evidence.
