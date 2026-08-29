# Gate 3 Filesystem Evidence Boundary

**Status:** REVISION 1 — AWAITING APPROVAL. Revision 1 adds the sanctioned
rename topology rule in "Directory ancestry disposition" and reconciles every
statement that previously said a directory delta without a classified
descendant always violates. The original design was approved conversationally
by the product owner on 2026-08-29; that approval does not extend to this
revision.

**Revision history:**

- Revision 0 — approved 2026-08-29. Implementation of Tasks 1–8 proceeded
  against it.
- Revision 1 — this document. Raised by an independent scoped review of the
  implementation, which reproduced a false violation: a sanctioned entity
  rename that moves a directory with no tracked descendant reported both the
  removed and the added directory as unsanctioned direct writes. Revision 0's
  normative step 5 required exactly that outcome, while its own closing
  sentence claimed the rule "preserves sanctioned rename and ordinary Git
  path topology". Both could not hold. Revision 1 resolves the contradiction
  in favour of the closing sentence, under a narrow, fail-closed pairing rule.

**Base:** freshly fetched `origin/main` at
`fecafea674cc254217d24950e716e42f71353fdc`.

**Authority:** the current repository instructions and Safety Foundation build
contract; the public S5 Gate 3 design; the public S7 bound-review-token design;
the authoritative system specification section for Gate 3; and the generic
v2/v2.1 conventions. Where sources differ, the repository's documented
authority order applies.

## Objective

Close a Gate 3 discovery gap without changing what Gate 3 sanctions. Git can
omit an untracked FIFO, socket, symlink, empty directory, or other non-regular
entry from porcelain output. The current narrow supplement discovers such an
entry only at the canonical S7 quarantine store. The same filesystem object at
another in-boundary location can therefore evade both dirty fingerprinting and
the final audit.

Gate 3 will add a deterministic, no-follow metadata traversal over the
auditable working-tree boundary. The snapshot and check will compare this
supplemental evidence alongside the existing Git-derived dirty evidence. New,
removed, replaced, or changed evidence will be a session change. The existing
exact S7 record-sanctioning predicates remain unchanged, and the exact
canonical S7 quarantine-directory creation remains the sole **standalone**
directory-only exception. A directory pair moved by a separately verified
sanctioned rename inherits that rename's disposition; it is not a second
standalone exception.

## Authority reconciliation

### Pre-existing state

The public S5 design defines Gate 3 as an endpoint comparison: the snapshot
records initially dirty state and the check reports new or changed dirty state.
Neither the later public S7 design nor the later authoritative Gate 3 text
overrides that baseline-preservation rule for non-regular entries. S7 narrows
which new quarantine outcome may be sanctioned; it does not redefine an
unchanged pre-existing filesystem object as a session write.

Supplemental filesystem evidence therefore follows the same rule:

- evidence present and identical at snapshot and check is preserved baseline
  state, not a violation and not a sanction;
- evidence added, removed, replaced, or changed between the two endpoints is a
  session change; and
- an incomplete or untrustworthy observation fails the command closed rather
  than silently changing baseline semantics.

Correct comparison requires the evidence to be serialized. Snapshot schema
version 4 is therefore an explicit compatibility boundary. A version 3
snapshot is refused with the existing controlled unsupported-version failure;
the operator must take a fresh snapshot. There is no implicit upgrade whose
missing initial filesystem state could be mistaken for a clean baseline.

### Git-invisible directories

Git-invisible directories are inside the unsanctioned-write contract. The
generic Gate 3 rule covers unsanctioned direct writes across a complete
session, the repository conventions explicitly note that Git does not
represent empty directories, and S7 calls its canonical durable quarantine
directory the one directory exception (Revision 1 reads that as the sole
*standalone* exception; see "Sanctioned rename topology"). Treating all real directories as
invisible would reproduce the same discovery flaw in a different filesystem
shape.

The supplement therefore records included real directories. A new or removed
empty directory is evidence. Recording it and disposing of it are separate
questions: the evidence is always collected, and "Directory ancestry
disposition" alone decides whether a given delta is sanctioned, suppressed as
a duplicate, or violating. Directory presence changes that exist only as
necessary ancestry for a Git-classified path are composed with that path's
disposition so they do not create duplicate or contradictory findings, and a
directory pair that a separately verified sanctioned rename moved inherits
that rename's disposition. No other empty-directory allowance exists.

### Ignored regular files

This correction does not create a second regular-file scanner. S5 defines
ordinary dirty evidence through Git, while the later authority requires a
narrow filesystem correction for evidence Git cannot classify. No
authoritative text extends this correction to ignored regular-file contents.
Regular files therefore remain governed by the existing Git-derived path and
fingerprint behavior, including Git's ignore semantics. This boundary is
deliberate and is not used to exempt directories, symlinks, or other
non-regular objects.

## Approved scope

The expected implementation surface is:

- `tools/gate3_audit.py` for snapshot schema, metadata discovery, comparison,
  and composition with the existing audit; and
- `tests/test_gate3_audit.py` for portable synthetic regressions.

Only minimal public documentation required by existing policy may accompany
those changes. There is no dependency, registry, convention, taxonomy, or
curated-data change.

## Filesystem boundary

### Included namespace

The traversal begins at the configured vault working-tree root after the
existing root validation. The root must be a real directory and is opened
without following a symlink. Every reachable directory entry is considered
unless its contents are excluded below.

For each included path, the supplement records:

- every real directory, including an empty directory; and
- every non-regular entry, including a symlink, FIFO, socket, character
  device, block device, or unclassified special type.

Regular files are not supplemental evidence. They remain inputs to the
existing Git status, index, content, and mode fingerprinting.

### Authoritative exclusions

Only these generic, authoritative exclusions apply:

- Git administrative metadata, resolved from Git rather than inferred from a
  private layout;
- the contents of a real convention-defined sensitive directory;
- the contents of the real convention-defined root scratch directory; and
- the contents of the exact generic cache directory derived by the existing
  executable convention.

The exclusion directory entry itself remains observable metadata; the walker
does not descend into it. An exclusion applies only when the named entry is a
real directory. A symlink, FIFO, socket, device, regular file, or other object
occupying an exclusion name is not treated as a directory exclusion: a
non-regular object is recorded, a regular file remains under Git's rules, and
neither is followed.

Exclusion names and paths come only from generic public conventions or existing
executable constants. Runtime registries may validate that an S7 location
belongs to a canonical runtime entity, but no entity, member, product, or
other instance value may define or widen a traversal exclusion.

### Boundary properties

- Directory symlinks are entries, never traversal roots.
- A symlink target outside the vault is hashed as link text but never opened or
  inspected.
- Mount points that appear as real directories remain inside the boundary;
  crossing them is not silently skipped unless an authoritative generic
  exclusion says so.
- The walk is metadata-only. It does not open regular-file contents or special
  devices.
- Relative paths must be canonical, duplicate-free, and representable by the
  snapshot format. An undecodable or otherwise unclassifiable name fails
  closed.

## Snapshot schema version 4

The top-level JSON object becomes:

```text
{
  "version": 4,
  "head": <validated object id>,
  "dirty": <existing Git-derived fingerprint map>,
  "filesystem": <supplemental filesystem fingerprint map>
}
```

The existing `dirty` schema and its semantics do not change. The new
`filesystem` map is keyed by validated vault-relative path. Each value has the
closed shape:

```text
{
  "kind": <directory | symlink | fifo | socket | char-device |
           block-device | other>,
  "mode": <integer permission/mode bits>,
  "identity_digest": <tagged stable-identity digest>,
  "target_digest": <symlink-target digest or null>
}
```

`identity_digest` is SHA-256 over a domain tag and a canonical, length-delimited
encoding of the entry kind, filesystem-device identity, inode identity, and,
for device objects, special-device identity returned by no-follow metadata
inspection. It never includes content, path text, or timestamps. This detects
replacement by another object of the same apparent kind. `target_digest` is a
separately domain-tagged SHA-256 of the raw link target and is populated only
for symlinks; the target is never resolved. `mode` is the integer returned by
the platform's permission-bit extraction, stored separately so a permission
change is explicit.

Directory modification and change timestamps are intentionally excluded from
the persisted fingerprint: normal descendant activity changes them and would
turn every valid tracked write into a parent-directory violation. They may be
used transiently to detect an inconsistent traversal, but never as
snapshot/check evidence.

The loader accepts exactly version 4 and exactly the closed fields above.
Missing fields, extra fields, invalid kinds, invalid modes or digests,
non-canonical paths, duplicate normalized paths, and malformed JSON are
controlled failures. Partially collected evidence is never accepted or
reported as a successful snapshot. Snapshot output remains outside the vault
under the existing rule.

## Deterministic no-follow traversal

The collector uses descriptor-relative depth-first traversal:

1. Open the validated real vault root as a directory without following a
   symlink.
2. List one directory and sort its entry names by filesystem-byte ordering.
3. Inspect every name relative to the open parent descriptor with no-follow
   metadata operations.
4. Record a symlink or other non-regular object without opening its target.
5. For a real child directory, open it with directory and no-follow flags,
   compare the pre-open metadata with descriptor metadata, recurse, reverify
   the child name from the parent after recursion, and close the child
   descriptor in `finally` while unwinding.
6. Relist and reverify the directory after its children. The name set and
   identities must agree with the observations used to build the result.
7. Close the root descriptor in `finally` on success or failure.

Descriptor lifetime is bounded by traversal depth: the walker retains only
the descriptors needed for the current ancestor chain. A child descriptor is
closed during depth-first unwind before the next sibling is visited. It never
retains one descriptor per visited directory.

The filesystem traversal is bracketed by two exact collections of the Git
status/index inputs used for dirty fingerprints. They must be equal. A change
in HEAD remains covered by the existing pre/post audit-head check. The combined
snapshot or check observation is accepted only when the Git bracket and every
directory-local consistency check agree.

The algorithm detects observable replacement, disappearance, relisting,
metadata, and status races. Like the existing endpoint Gate 3 model, it does
not claim an impossible atomic filesystem snapshot against an actor that
changes and restores an entry entirely outside the observation windows, or
mutates after the final check. Any race or inconsistency that is observed is a
controlled fail-closed error.

## Evidence comparison and composition

### Filesystem delta

The check compares the union of snapshot and current `filesystem` paths in
canonical sorted order:

- equal fingerprints are unchanged baseline;
- current-only paths are additions;
- snapshot-only paths are removals; and
- unequal fingerprints are type, mode, identity, or symlink-target changes.

Additions, removals, and changes are session changes. A change between a real
directory and any non-directory is a replacement, not an ancestor event.
Likewise, symlinks and other special types cannot be confused with regular
files or with one another.

### Composition with Git evidence

Git commit history, Git-derived dirty fingerprints, and supplemental
filesystem evidence are collected separately and then composed. The
supplement does not inject synthetic Git statuses or weaken the current dirty
fingerprint type. Final sanctioned and violating outputs are canonical-sorted
and deduplicated.

The new boundary-wide collector subsumes and retires the current
canonical-store-only special-entry discovery helper. This removes the blind
spot rather than layering two partially overlapping discovery paths. It does
not alter the downstream S7 sanctioning code.

A non-directory filesystem delta has no general sanction. It is a direct-write
violation. In particular, the exact S7 record sanction continues to require a
regular file discovered through the existing Git-derived evidence and to pass
every current record, pending-state, receipt, action, identity, byte, and
commit-correlation predicate. Those predicates and their classifier taxonomy
are unchanged. A non-regular lookalike at the same path cannot enter that
sanctioning path.

### Directory ancestry disposition

Only directory presence deltas participate in ancestry composition. Directory
mode or identity changes at a path that exists at both endpoints are always
violations, even when descendants changed validly.

For each added or removed real directory:

1. Find already-classified changed descendant paths from commit evidence,
   dirty evidence, and non-directory filesystem evidence.
2. A descendant is relevant only when the directory is a strict, necessary
   ancestor of that descendant in the matching before/after topology: an
   added directory must be necessary to a descendant addition in the current
   tree; a removed directory must have been necessary to a descendant removal
   in the snapshot tree.
3. If a relevant descendant is violating, its finding already fails the gate;
   suppress the ancestor as a duplicate.
4. If all relevant descendants are sanctioned, inherit their sanctioned
   disposition for the ancestor topology rather than reporting a contradictory
   directory violation.
5. If there is no relevant descendant, the delta may still be paired under
   "Sanctioned rename topology" below.
6. Otherwise the directory delta stands on its own and is a violation unless
   it is the exact quarantine-directory exception below.

The descendant rule alone does **not** preserve rename topology for a
directory that has no classified descendant — a directory holding only
untracked or ignored content, or nothing at all, has none by construction.
Revision 0 asserted otherwise; that assertion was wrong and is withdrawn.
Rename topology is preserved by the pairing rule below and by nothing else.

Ordinary Git path topology is still preserved by the descendant rule, and an
unrelated empty sibling, a wrong-location lookalike, or a directory
containing only ignored regular files is still detected. Mixed descendants
fail because a violating descendant cannot be erased by a sanctioned one.

### Sanctioned rename topology

A removed directory and an added directory may inherit the disposition of a
rename that Gate 3 has **already** proven sanctioned. A successful pair
reports **both endpoints as sanctioned writes**, exactly as an ancestor that
inherits from sanctioned descendants does; neither endpoint is a violation.
Pairing is an inheritance, never a suppression. This is inheritance
from a separately verified commit, not a new standalone exception, and it is
never a general directory-move whitelist: a directory move that no verified
rename explains remains a violation.

Pairing requires every one of the following. Any failure, missing evidence,
or ambiguity fails closed and the delta is judged as if no pairing existed.

1. **A proven rename.** Some commit in the audited window is sanctioned by
   the existing commit-relative rename verification, which reconstructs the
   expected envelope from the rename planner over that commit's own parent
   tree and requires the commit's exact change set to equal it. A commit that
   is not sanctioned, or whose envelope could not be reconstructed,
   contributes no mapping. An unsanctioned commit anywhere in the window
   already lands in the violating-commit list and fails the gate outright, so
   pairing can never rescue such a window; it can only ever avoid adding a
   second, redundant finding to one that already fails.
2. **Mapping from the verified plan.** The old-root/new-root pairs come from
   the move pairs of the exact plan that produced the matching envelope,
   expressed vault-relative. They are never inferred from path-name
   similarity, from the commit message alone, or from the final manifest. If
   more than one axis reproduces the matching envelope for one commit, the
   mapping for that commit is ambiguous and contributes nothing.

   The envelope builder already computes these pairs and then discards them,
   returning only the envelope; the sanction check returns only a boolean,
   and neither the commit-audit result nor the filesystem disposition call
   receives them. Implementing this rule therefore requires threading the
   matched plan's move pairs out of the envelope builder, through the rename
   sanction check and the commit audit, to filesystem disposition. That is a
   deliberate extension of the approved surface, not a hint to reach for the
   manifest or a name heuristic. **It must not change which commits are
   sanctioned:** the sanctioning decision keeps its present semantics
   exactly.

   Detecting ambiguity also requires the mapping pass to evaluate every axis
   to completion. The present check returns on its first matching axis and so
   can never observe a second one. The mapping pass must not inherit that
   early return, while the sanctioning decision keeps it.
3. **Exact relative path beneath the roots.** For one mapped pair
   `(old_root, new_root)`, the removed path must be `old_root/tail` and the
   added path must be `new_root/tail` for the identical `tail`. An empty
   `tail` pairs the roots themselves. A different tail, a different root, or
   a tail matched only by prefix is not a pair.
4. **Both endpoints are real directories.** Both fingerprints must have kind
   `directory`. A symlink, FIFO, socket, device, regular file, or any other
   object at either endpoint is never paired.
5. **Matching metadata and identity.** The two fingerprints must have equal
   `mode` and equal `identity_digest`. A rename on one filesystem preserves
   device and inode identity, so an unequal identity means the added
   directory is a different object that merely occupies the expected name.

**Sequential renames** compose deterministically in audited commit order,
oldest first. A later mapping `(o, n)` rewrites an earlier mapping's
destination `d` when `o` equals `d` **or** `o` is a proper path-prefix of
`d`; the rewritten destination is `n` joined with `d`'s tail beneath `o`.
Prefix composition is required, not a refinement: renames on different axes
nest. A sanctioned product rename yielding `a/<module>/p → a/<module>/q`
followed by a sanctioned entity rename yielding `a → b` moves a directory
from `a/<module>/p/<tail>` to `b/<module>/q/<tail>`, and neither mapping
alone pairs those endpoints. Exact-root composition would leave that case
reporting the false violation this revision exists to remove.

Composition is applied only across commits that are each independently
sanctioned. If two rewrites apply to one destination, or the rewritten set
sends one source to two destinations or two sources to one destination, the
chain is conflicting; a conflicting or ambiguous chain contributes no
mapping at all rather than a best guess.

**Pairing cannot hide anything.** It is evaluated only after the
violating-descendant rule, so a violating descendant beneath an otherwise
paired directory still fails the gate and still suppresses its ancestor
rather than sanctioning it. Pairing never reaches an unrelated sibling
addition or removal, a move to or from a root the verified plan does not
name, a replacement of either endpoint, a special file or symlink at either
endpoint, or a directory independently created or deleted at a path that
merely resembles a rename destination. Each of those remains a violation on
its own terms.

### Exact S7 quarantine-directory exception

The only **standalone** directory-only sanction — the only one that needs no
other verified evidence to stand on — is addition of the exact canonical S7
quarantine directory for a runtime-manifest entity. Sanctioned rename
topology is not a second standalone exception: it inherits the disposition of
a separately verified sanctioned rename commit and cannot sanction anything
on its own. Its path shape comes from
the existing executable S7 constant and canonical entity validation. This
allows the durable directory to remain after first use or a refusal leaves it
empty.

The exception does not apply to:

- a similarly named directory at any other location;
- a canonical name beneath a non-canonical or unknown entity;
- removal of the canonical directory;
- replacement, redirection, mode change, or identity change of that directory;
- any symlink or non-directory object at that name; or
- any child record, sibling write, or other content.

Child records continue through the unchanged exact S7 record-sanctioning
predicates. An unrelated sibling write remains a violation even when the
canonical directory addition itself is sanctioned.

## Error handling

Snapshot and check fail through the controlled command boundary, produce no
success result, and never accept partial evidence when any relevant operation
is unreadable, disappearing, malformed, unsupported, or unclassifiable. This
includes:

- root, directory, or entry open/list/stat/readlink failures;
- permission errors and unexpected object types;
- pre-open, descriptor, post-recursion, or relisting identity mismatches;
- a name appearing, disappearing, or changing during traversal;
- failure to close or otherwise complete a descriptor-owned operation;
- inconsistent Git status/index brackets or a changed audit HEAD;
- malformed version 4 filesystem evidence; and
- path encoding, normalization, ordering, or duplicate-key ambiguity.

There is no skip-on-error mode, best-effort partial walk, fallback to following
paths, or conversion of an observation failure into an empty evidence set.

## Test matrix

All tests use synthetic repositories and portable temporary fixtures. They do
not require a live vault or privileged device creation.

### Discovery and baseline preservation

- A wrong-location FIFO at a quarantine-like leaf is absent from Git status
  but appears in supplemental evidence and fails when created after snapshot.
- Git-invisible non-regular entries at unrelated entity-local locations are
  detected.
- A pre-existing FIFO, symlink, socket where supported, or empty directory is
  accepted when its fingerprint is unchanged.
- Removal, same-kind replacement, type replacement, mode change, and symlink
  target change of pre-existing evidence fail.
- New and removed empty directories fail when they have no classified
  descendant and no verified sanctioned-rename pairing.
- A directory containing only ignored regular content still contributes the
  directory delta; the ignored regular content itself is not scanned.

### Exact S7 behavior

- Addition of the exact canonical quarantine directory is sanctioned even
  when a refusal leaves it empty.
- Canonical regular quarantine records remain governed by every existing
  sanctioned-outcome check.
- A non-regular object at the canonical record or store path cannot be
  sanctioned through type confusion.
- A wrong-location lookalike, an unrelated sibling write, canonical-directory
  removal, and canonical-directory replacement remain violations.
- Existing record-sanctioning regressions remain unchanged and green.

### Sanctioned rename topology

Every case below is adversarial and must be written RED before the pairing
rule exists. Exactly three end sanctioned — the no-tracked-descendant case,
the ignored/untracked-content case, and the sequential-composition case.
Every other case must still report a violation.

- A sanctioned entity rename moving a directory that has **no tracked
  descendant** pairs, and both endpoints are reported as sanctioned writes.
- The same rename where the directory holds only ignored or untracked
  regular content pairs, because the contract deliberately leaves ignored
  regular files to Git and the directory itself is the only evidence.
- The same directory delta with **no sanctioned rename commit** in the
  window violates at both endpoints.
- A removed or added path beneath a **wrong old or new root** — one the
  verified plan does not name — violates.
- A removed and added pair whose **relative tails differ** violates, including
  a tail that matches only as a prefix.
- A pair whose `identity_digest` differs violates, proving the added
  directory is a different object occupying the expected name.
- A pair whose `mode` differs violates.
- A pair where either endpoint is a **symlink or any non-directory** violates,
  and is never treated as ancestry.
- An **ambiguous** rename mapping — more than one axis reproducing
  one commit's matching envelope — contributes nothing and the delta
  violates.
- A **conflicting** rename chain — one source mapped to two destinations, or
  two sources to one destination — contributes nothing and the delta
  violates.
- A **sequential** rename chain across two independently sanctioned commits
  composes, and the composed endpoints pair as sanctioned writes. This case
  must include a nested chain — a rename beneath a root that a later rename
  then moves — so exact-root composition cannot pass it.
- An **unrelated sibling** directory added or removed beside a correctly
  paired directory still violates.
- A **violating descendant** beneath an otherwise paired directory still
  fails the gate, and its ancestor is suppressed rather than sanctioned.

### Traversal and races

- A directory symlink is recorded and not followed; an external target is not
  inspected.
- A symlink or special entry occupying an exclusion name is not treated as an
  excluded real directory.
- Sorted input order produces deterministic serialized evidence and final
  findings.
- Instrumented traversal proves child descriptors close during depth-first
  unwind and peak descriptor use is bounded by depth.
- Injected list, open, stat, descriptor-identity, readlink, relist, and close
  failures produce controlled failure with no partial success.
- Disappearance and replacement at each verification boundary fail closed.
- A Git status/index change across the traversal bracket fails closed.
- Socket coverage runs where safely supported. Character-device,
  block-device, and residual special-type classification is tested through
  synthetic metadata classification rather than privileged creation.

### Schema and regressions

- Snapshot output is version 4 and round-trips the closed filesystem map.
- Version 3, unknown versions, missing/extra fields, invalid fingerprints,
  malformed paths, and duplicate canonical paths are refused.
- The snapshot-outside-vault rule remains enforced.
- Clean vaults produce no supplemental delta.
- Valid tracked changes, valid untracked regular dirty evidence, pre-existing
  regular dirty evidence, and sanctioned pending regular files do not regress.
- Directory ancestry for sanctioned and violating Git paths is classified once
  without hiding unrelated directory evidence.

## Scope boundaries

This work does not:

- introduce a general directory-move whitelist, or any directory sanction
  that does not trace to separately verified evidence;
- change S7 transaction, quarantine, receipt, or recovery behavior;
- change any S7 record-sanctioning predicate or sanctioned-content semantic;
- change the classifier taxonomy, registries, conventions, dependencies, or
  curated vault content;
- add content inspection for ignored regular files;
- follow directory symlinks or read special devices;
- claim continuous monitoring between snapshot and check endpoints;
- run a live Gate 3 trial, Gate 1 timing, deployment, or Phase 2 work; or
- reuse, clean, reset, delete, or modify preserved correction or live-trial
  evidence.

## Acceptance gates

Implementation is complete only after all of the following fresh evidence is
recorded under the repository's preservation rules:

- focused RED then GREEN proof for the complete test matrix;
- full public suite with at least 1,847 passing tests;
- private unittest discovery with at least 39 passing tests;
- structural validation at zero errors and zero warnings;
- policy self-test;
- clean pinned Gitleaks scan;
- clean public current-tree and canonical-history audits;
- clean combined repository-plus-vault history audit;
- byte-identical protected vault HEAD, status, worktree diff, and cached diff
  versus opaque preimages;
- independent scoped review with findings reported by severity;
- CodeRabbit review of the final pushed head, with every finding resolved or
  returned for owner decision; and
- owner authorization before merge.

The implementation branch must not be pushed or opened as a pull request until
all local gates pass. It must not be merged as part of this work without the
owner's explicit merge authorization.
