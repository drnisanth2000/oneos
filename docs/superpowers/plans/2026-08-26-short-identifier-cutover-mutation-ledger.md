# Short-Identifier Cutover — Stage A Mutation Ledger

**Branch:** `codex/short-id-cutover-stage-a`
**Base:** freshly fetched `origin/main` at `416e1fb8821cf0bf22b63d72309899e49da60af9`
**Plan:** `docs/superpowers/plans/2026-08-26-short-identifier-cutover-stage-a.md`

Every row below was proved by the same procedure: copy the target file outside
the repository, apply the exact edit, run only the named node, confirm RED for
the stated reason, restore from the copy, verify the restoration by SHA-256,
and re-run the node to GREEN. No row is recorded as evidence unless its
mutation actually killed its test.

**Campaign: 52 rows, 52 proved.** Closing suite **1656 passed**, no production
diff outstanding.

## Method notes

Thirty-one rows carried an explicit `A` → `B` code pair and were applied by a
driver that refused to proceed unless the A-side matched exactly once. The
remainder were hand-applied, each with an anchor wide enough to be unique.

## Rows retired as non-evidence

Five originally specified mutations did not kill their named test. Each was
masked by an independent guard, so the row proved nothing and was corrected
rather than recorded. This is the campaign's own finding: a mutation that does
not go red is not weaker evidence, it is no evidence.

| Row | Original mutation | Why it did not kill | Correction |
|---|---|---|---|
| 14 | `exempt_former_slugs = relative in FORMER_SLUGS_FILES` → `True` | The exemption is gated on the filename **and** the line shape `^\s*former_slugs:\s*\[`; the member entry `- {id: m7-member, former_slugs: [m7]}` never matches that regex, so forcing the filename check true changed nothing | Retargeted to restore the blanket substring exemption `if "former_slugs" in line:` |
| 47 | `if status:` → `if False:` | Left the body over-indented; failed with `IndentationError`, so the guard was never exercised. A syntax error is not a mutation proof | Retargeted to the syntax-preserving `if status and False:` |
| 19 | live-HEAD precheck → `if False:` | `git merge --ff-only` independently refuses the non-fast-forward and raises `CutoverError` anyway, so the test passed on the fallback primitive | Test strengthened with `match="live HEAD moved since the build"`; mutation kept, so it now proves the precheck rather than the fallback |
| 5 | resolver body → `return next(root.rglob("books.db"))` | Enumeration happened to return the approved database first, so the correct file was updated | Retargeted to `sorted(root.rglob("books.db"))[-1]`, which the fixture guarantees selects `zz/books.db` |
| 15 | first advisory `raise UnreadableFile` → `return []` | `_typed_token_spans` reads the file first and raises; the advisory scan's own read is masked in the end-to-end test | Isolated regression added that neutralises `_typed_token_spans`; mutation retargeted at the second advisory read block |
| 11 | `_require_dispositions(...)` → `pass` | Not equivalent to the specified relocation; the test observes the tree the check ran against | Applied the exact relocation instead |
| 22 | `committed = True` moved before `git merge` | An ordinary merge refusal raises `CutoverError` directly and never consults the flag | Replaced: initial `committed = False` → `True`, with `action_lock` raising `GitTransactionFailure` before the body |

## Defect found by the campaign

Row 17's strengthened fixture — a product workspace whose `id` equals its
product slug — could not build at all:

```
CutoverError: post-migration advisory report changed from the approved incidental set
```

Simulation on that fixture, before the fix:

```
pre : 5 occurrences
post: 1 occurrence
vanished: ('_system/products.yaml',   'workspace', 'q7')
          ('_system/workspaces.yaml', 'product',   'q7')
          ('_system/workspaces.yaml', 'workspace', 'q7')
          ('ab/00-inbox/note.md',     'entity',    'ab')
          ('ab/00-inbox/note.md',     'workspace', 'q7')
```

`advisory_occurrences` included `axis` in its typed-span exclusion key, so a
product-typed `q7` was reported as advisory for workspace `q7` occupying the
same span. A typed rewrite then removed it, and `_require_post_advisory`
refused — making every class-3 cutover unbuildable even though the design
explicitly permits one literal on two axes.

Fixed in `a5fe98f` by deriving an axis-independent typed-span set. Typedness
belongs to the exact token span, not to the axis currently scanning it. The
exclusion narrows to `(path, line, old, start, end)`; whole lines and untyped
occurrences are never excluded, so ordinary same-literal prose remains
advisory per axis. `_require_post_advisory` was not weakened or removed.

After the fix the same fixture yields one occurrence — the ordinary entity
prose in `note.md`, which survives as required — and the full same-literal
build and promotion succeed.

## Campaign rows

Rows 1–49 are the plan's table. Row 50 is the post-move database derivation
correction, row 51 the validator-detritus guard, row 52 the axis-independent
typed-span exclusion. Three further integration rows (M-A, M-B, M-C) cover the
Console taxonomy and reader declarations.

Representative diagnostics, quoted from actual output:

| Row | Target | RED diagnostic |
|---|---|---|
| 1 | `app/identifiers.py` | `assert IDENTIFIER_MINIMUM_LENGTH == 5` |
| 3 | `app/cutover_db.py` | axis filter widened; product target received the member value |
| 5 | `app/cutover_db.py` | `assert [('ab-product',)] == [('ab',)]` — `zz/books.db` wrongly modified |
| 11 | `app/cutover_build.py` | `dispositions were not checked on the source tree` |
| 12 | `app/cutover_locations.py` | `.sensitive/` read allowed after a half-rewritten policy rule |
| 14 | `app/cutover_locations.py` | member provenance hidden by the blanket exemption |
| 15 | `app/cutover_locations.py` | `Failed: DID NOT RAISE UnreadableFile` |
| 17 | `app/cutover_build.py` | `AssertionError: product claimed workspace id` |
| 18 | `app/cutover_build.py` | `AssertionError: entity rewrite was lost` |
| 19 | `app/cutover.py` | `Expected regex: 'live HEAD moved since the build'` / actual `'fast-forward promotion refused'` |
| 22 | `app/cutover.py` | `uncommitted failure reported committed` |
| 24 | `tools/public_repo_audit.py` | retired identifiers re-seeded as audit terms |
| 47 | `app/cutover_build.py` | `Failed: DID NOT RAISE CutoverError` |
| 50 | `app/cutover_build.py` | `approved database is missing or not a regular file` — final gate read the pre-move path |
| 51a | `app/cutover_build.py` | `-B` removed; `test_cutover_validator.cpython-313.pyc` left in the tree |
| 51b | `app/cutover_build.py` | `--ignored` dropped; `Failed: DID NOT RAISE CutoverError` |
| 52 | `app/cutover_locations.py` | `a typed span was misreported on another axis` |
| M-A | `app/console_errors.py` | `assert 'E-ADMIN' == 'E-COMMITTED'` |
| M-B | `app/console_errors.py` | `app.cutover_db.DatabaseCutoverError is unmapped` |
| M-C | `app/cutover_locations.py` | undeclared structured read site at `cutover_locations.py:538` |

## Restoration

Every mutation was restored from its preimage and verified by SHA-256 before
the next row ran. After the campaign, `git diff app/ tools/` was empty apart
from the deliberate `a5fe98f` defect fix, which is committed rather than
restored.
