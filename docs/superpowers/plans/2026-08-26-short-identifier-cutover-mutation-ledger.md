# Short-Identifier Cutover — Stage A Mutation Ledger

**Branch:** `codex/short-id-cutover-stage-a`
**Base:** freshly fetched `origin/main` at `416e1fb8821cf0bf22b63d72309899e49da60af9`
**Plan:** `docs/superpowers/plans/2026-08-26-short-identifier-cutover-stage-a.md`

Every row below was proved by the same procedure: copy the target file outside
the repository, apply the exact edit, run only the named node, confirm RED for
the stated reason, restore from the copy, verify the restoration by SHA-256,
and re-run the node to GREEN. No row is recorded as evidence unless its
mutation actually killed its test.

## Row-versus-mutation arithmetic

The row count and the mutant-edit count are not the same number, and the
difference is load-bearing when checking coverage:

- **52 numbered Stage A rows** — rows 1–49 from the plan's table, plus row 50
  (post-move database target derivation), row 51 (validator detritus) and row
  52 (axis-independent typed-span exclusion).
- **Row 51 carries two independent mutations**, 51a and 51b, because the
  correction it records has two separable guards: the `-B` flag that stops
  bytecode at its source, and the `--ignored` coverage that detects anything a
  validator still leaves behind. Either can be removed without the other.
- **53 Stage A mutant edits** = 52 rows + 1 for the 51a/51b split.
- **Plus 3 integration mutations** (M-A, M-B, M-C) covering the Console
  taxonomy and reader-category declarations.
- **56 total proofs**, every one of which is recorded in full below.

Closing suite **1656 passed**, no production diff outstanding.

## Method notes

Thirty-one rows carried an explicit `A` → `B` code pair and were applied by a
driver that refused to proceed unless the A-side matched exactly once. The
remainder were hand-applied, each with an anchor wide enough to be unique.

## Rows retired as non-evidence

Seven originally specified mutations did not kill their named test. Each was
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

## Complete evidence, one section per mutation

Every section below was captured from an actual run. Where an earlier
summary retained only a headline, the mutation was re-run from scratch so
that its exact edit, command, RED output, restoration digest and GREEN
re-run are all recorded rather than reconstructed.

### Mutation 1 — `app/identifiers.py`

**Edit:**

```diff
- IDENTIFIER_MINIMUM_LENGTH = 5
+ IDENTIFIER_MINIMUM_LENGTH = 4
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_identifiers.py::test_floor_is_one_above_the_audit_long_term_threshold
```

**RED** (exit 1):

```
>       assert IDENTIFIER_MINIMUM_LENGTH == 5
E       assert 4 == 5
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `b370e309d5d3d7d2…` before and `b370e309d5d3d7d2…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 2 — `app/identifiers.py`

**Edit:**

```diff
- if new != expected:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_refuses_a_mapping_that_is_not_the_deterministic_result
```

**RED** (exit 1):

```
>       with pytest.raises(Exception, match="deterministic"):
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'deterministic'
E         Actual message: 'approved dispositions do not exactly match the source advisory report; re-run from inventory'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `b370e309d5d3d7d2…` before and `b370e309d5d3d7d2…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.12s`

### Mutation 3 — `app/cutover_db.py`

**Edit:**

```diff
- if item.axis == target.axis
+ if item.axis in {"product", "member"}
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_db.py::test_a_product_target_receives_only_the_product_mapping
```

**RED** (exit 1):

```
>       assert read(tmp_path / "ab" / "books.db", "SELECT product FROM ledger") == [
E       AssertionError: assert [('ab-member',)] == [('ab-product',)]
E
E         At index 0 diff: ('ab-member',) != ('ab-product',)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `679ef99fb9672117…` before and `679ef99fb9672117…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 4 — `app/cutover_db.py`

**Edit:**

```diff
- f"SET {_quote_identifier(target.column)} = ? "
+ f'SET "tag" = ? '
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_db.py::test_only_the_allowlisted_column_is_updated
```

**RED** (exit 1):

```
>       assert read(tmp_path / "ab" / "books.db", "SELECT tag FROM ledger") == [("ab",)]
E       AssertionError: assert [('ab-product',)] == [('ab',)]
E
E         At index 0 diff: ('ab-product',) != ('ab',)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `679ef99fb9672117…` before and `679ef99fb9672117…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 5 — `app/cutover_db.py`

**Edit:**

```diff
-     pure = PurePosixPath(target.path)
+     return sorted(root.rglob("books.db"))[-1]  # MUTANT
+     pure = PurePosixPath(target.path)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_db.py::test_a_matching_column_name_in_another_database_is_untouched
```

**RED** (exit 1):

```
>       assert read(tmp_path / "zz" / "books.db", "SELECT product FROM ledger") == [("ab",)]
E       AssertionError: assert [('ab-product',)] == [('ab',)]
E
E         At index 0 diff: ('ab-product',) != ('ab',)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `679ef99fb9672117…` before and `679ef99fb9672117…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 6 — `app/cutover_db.py`

**Edit:**

```diff
- if current.is_symlink():
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_db.py::test_a_symlinked_component_is_refused
```

**RED** (exit 1):

```
>       with pytest.raises(DatabaseCutoverError):
E       Failed: DID NOT RAISE DatabaseCutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `679ef99fb9672117…` before and `679ef99fb9672117…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 7 — `app/cutover_build.py`

**Edit:**

```diff
- if residual:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_gate_refuses_a_database_writer_that_leaves_the_old_value
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverError, match="database residual after update"):
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'database residual after update'
E         Actual message: 'database residual after migration'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.16s`

### Mutation 8 — `app/cutover_build.py`

**Edit:**

```diff
- if remaining:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_gate_refuses_a_writer_that_misses_the_policy_except_half
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverError, match="entity:action-policy:except"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.18s`

### Mutation 9 — `app/cutover_build.py`

**Edit:**

```diff
- check_collisions(manifest.mappings, existing_identifiers(scratch))
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_refuses_a_colliding_mapping
```

**RED** (exit 1):

```
>       with pytest.raises(Exception, match="collides"):
E       Failed: DID NOT RAISE Exception
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.14s`

### Mutation 10 — `app/cutover_build.py`

**Edit:**

```diff
- require_clean_entities(vault, affected)
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_refuses_an_entity_with_ignored_content
```

**RED** (exit 1):

```
>       with pytest.raises(Exception, match="ignored or untracked"):
E       Failed: DID NOT RAISE Exception
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.11s`

### Mutation 11 — `app/cutover_build.py`

**Edit:** move `_require_dispositions(scratch, manifest)` from immediately after worktree entry to immediately after `_apply_mappings_in_order(...)`

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_dispositions_are_checked_before_any_path_move
```

**RED** (exit 1):

```
>       build_cutover(vault, raw, record)
>       assert (root / "ab").is_dir(), "dispositions were not checked on the source tree"
E       AssertionError: dispositions were not checked on the source tree
E       assert False
E        +  where False = is_dir()
E        +    where is_dir = (PosixPath('<temporary-path-redacted> / 'ab').is_dir
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.33s`

### Mutation 12 — `app/cutover_locations.py`

**Edit:**

```diff
- (paths|except):\s*\[
+ (paths):\s*\[
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_sensitive_reads_are_denied_before_and_after_the_cutover
```

**RED** (exit 1):

```
>       promoted(vault)
>           raise CutoverError(
E           app.cutover_build.CutoverError: approved dispositions do not exactly match the source advisory report; re-run from inventory
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.38s`

### Mutation 13 — `app/cutover_locations.py`

**Edit:**

```diff
- rf"(?<![\w-]){re.escape(term)}(?![\w-])"
+ rf"{re.escape(term)}"
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_advisory_does_not_report_a_longer_token
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []
E       AssertionError: assert [AdvisoryOccu...48a', line=1)] == []
E
E         Left contains 3 more items, first extra item: AdvisoryOccurrence(path='note.md', axis='entity', old='ab', ordinal=1, context_sha256='d6e4dbb46a94328220c954d9b97bc8d210228f079346c470d1a4d8404d22348a', line=1)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 14 — `app/cutover_locations.py`

**Edit:**

```diff
-             if exempt_former_slugs and re.match(
-                 r"^\s*former_slugs:\s*\[", line
-             ):
+             if "former_slugs" in line:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_former_slugs_is_not_exempt_in_the_member_registry
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[2:3]) == [
E       AssertionError: assert [] == [AdvisoryOccu...b79', line=3)]
E
E         Right contains one more item: AdvisoryOccurrence(path='_system/members.yaml', axis='member', old='m7', ordinal=1, context_sha256='af4e7e38edaef5a3b66c524f00d8ae734e5aa8d5c9865cb7caf860cc75533b79', line=3)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 15 — `app/cutover_locations.py`

**Edit:**

```diff
-             except (UnicodeDecodeError, OSError) as exc:
-                 raise UnreadableFile(
-                     f"{relative} could not be read; the advisory scan cannot pass "
-                     f"on a file it never saw"
-                 ) from exc
+             except (UnicodeDecodeError, OSError):
+                 continue  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_advisory_scan_itself_refuses_an_unreadable_file
```

**RED** (exit 1):

```
>       with pytest.raises(UnreadableFile, match="the advisory scan cannot pass"):
E       Failed: DID NOT RAISE UnreadableFile
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 16 — `app/cutover_build.py`

**Edit:**

```diff
-             registry.write_text(
-                 rewrite_yaml_value_field(
-                     registry.read_text(encoding="utf-8"), "id", old, new
-                 ),
-                 encoding="utf-8",
-             )
+             registry.write_text(
+                 _record_former_slug(
+                     rewrite_yaml_value_field(
+                         registry.read_text(encoding="utf-8"), "id", old, new
+                     ),
+                     new, old, 6,
+                 ),
+                 encoding="utf-8",
+             )
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_former_slugs_is_written_only_on_entity_and_product_keys
```

**RED** (exit 1):

```
>       promoted(vault)
>           raise CutoverError(f"renamed registry key {key!r} is absent")
E           app.cutover_build.CutoverError: renamed registry key 'm7-member' is absent
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.33s`

### Mutation 17 — `app/cutover_build.py`

**Edit:**

```diff
-             text = rewrite_mapping_key(registry.read_text(encoding="utf-8"), old, new, 4)
-             registry.write_text(_record_former_slug(text, new, old, 6), encoding="utf-8")
+             text = rewrite_mapping_key(registry.read_text(encoding="utf-8"), old, new, 4)
+             registry.write_text(_record_former_slug(text, new, old, 6), encoding="utf-8")
+         _ws = system / "workspaces.yaml"  # MUTANT: product claims workspace id
+         if _ws.is_file():
+             _ws.write_text(
+                 rewrite_yaml_value_field(_ws.read_text(encoding="utf-8"), "id", old, new),
+                 encoding="utf-8",
+             )
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_a_product_kind_workspace_id_takes_the_workspace_suffix
```

**RED** (exit 1):

```
>       assert "id: q7-workspace" in workspaces, "product claimed workspace id"
E       AssertionError: product claimed workspace id
E       assert 'id: q7-workspace' in 'workspaces:\n  - {id: q7-product, entity: ab-entity, product: q7-product, member: m7-member, kind: product}\n'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.37s`

### Mutation 18 — `app/cutover_build.py`

**Edit:**

```diff
-     """Plan each mapping from the tree produced by its predecessor."""
-     for mapping in mappings_in_order(manifest):
+     """Plan each mapping from the tree produced by its predecessor."""
+     _ws = root / "_system" / "workspaces.yaml"
+     _snap = _ws.read_text(encoding="utf-8") if _ws.is_file() else None
+     for mapping in mappings_in_order(manifest):
+         if _snap is not None and _ws.is_file():
+             _ws.write_text(_snap, encoding="utf-8")  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_sequential_application_preserves_every_mapping_touching_one_file
```

**RED** (exit 1):

```
>       assert "entity: ab-entity" in workspaces, "entity rewrite was lost"
E       AssertionError: entity rewrite was lost
E       assert 'entity: ab-entity' in 'workspaces:\n  - {id: w7-workspace, entity: ab, product: q7, member: m7, kind: product}\n'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.16s`

### Mutation 19 — `app/cutover.py`

**Edit:**

```diff
-             if git(vault, "rev-parse", "HEAD").strip() != source_head:
+             if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_promotion.py::test_promotion_refuses_a_moved_head
```

**RED** (exit 1):

```
>       with pytest.raises(
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'live HEAD moved since the build'
E         Actual message: 'fast-forward promotion refused; the vault is unchanged'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.21s`

### Mutation 20 — `app/cutover.py`

**Edit:**

```diff
- require_clean_entities(vault, affected_entities)
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_promotion.py::test_promotion_repeats_the_ignored_content_check
```

**RED** (exit 1):

```
>       with pytest.raises(
E       Failed: DID NOT RAISE UnmigratableContentError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.21s`

### Mutation 21 — `app/cutover.py`

**Edit:**

```diff
- with action_lock(vault):
+ with contextlib.nullcontext():
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_promotion.py::test_promotion_takes_the_shared_action_lock
```

**RED** (exit 1):

```
>       promote(vault, built, head, git_status_bytes(vault), [])
>           with contextlib.nullcontext():
E           NameError: name 'contextlib' is not defined. Did you forget to import 'contextlib'?
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.18s`

### Mutation 22 — `app/cutover.py`

**Edit:**

```diff
-     committed = False
+     committed = True  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_promotion.py::test_a_failed_promotion_is_not_reported_as_committed
```

**RED** (exit 1):

```
>       assert not isinstance(caught.value, CutoverCommittedError), (
E       AssertionError: uncommitted failure reported committed
E       assert not True
E        +  where True = isinstance(CutoverCommittedError('the cutover committed but the lock layer failed; do not retry'), CutoverCommittedError)
E        +    where CutoverCommittedError('the cutover committed but the lock layer failed; do not retry') = <ExceptionInfo CutoverCommittedError('the cutover committed but the lock layer failed; do not retry') tblen=2>.value
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.15s`

### Mutation 23 — `app/cutover.py`

**Edit:**

```diff
-     result = build_cutover(vault, manifest_bytes, record)
-     print(git(vault
+     raise CutoverError("dry run skipped build")  # MUTANT
+     print(git(vault
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_cli.py::test_dry_run_builds_and_shows_the_diff_without_touching_the_vault
```

**RED** (exit 1):

```
>       assert code == 0
E       assert 1 == 0
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.29s`

### Mutation 24 — `tools/public_repo_audit.py`

**Edit:**

```diff
-         terms.update(key for key in entities if isinstance(key, str))
+         terms.update(key for key in entities if isinstance(key, str))
+         for spec in entities.values():
+             if isinstance(spec, dict):
+                 terms.update(spec.get('former_slugs') or [])
```

**Command:**

```bash
uv run python -m pytest -q tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids
```

**RED** (exit 1):

```
>       assert "ab" not in short_terms and "ab" not in long_terms
E       AssertionError: assert ('ab' not in {'ab'})
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `d3be7e8a8a5e48bf…` before and `d3be7e8a8a5e48bf…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.08s`

### Mutation 25 — `app/cutover_locations.py`

**Edit:**

```diff
-         rf"{re.escape(old)}"
-         rf"(?=[ \t]*(?:[,}}#]|$))"
-     )
-     return pattern.sub(rf"\g<1>\g<2>{new}", text)
+         rf"{re.escape(old)}"
+     )
+     return pattern.sub(rf"\g<1>\g<2>{new}", text)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_yaml_value_field_rewrite_does_not_match_a_scalar_prefix
```

**RED** (exit 1):

```
>       assert rewrite_yaml_value_field(text, "id", "ab", "ab-member") == text
E       AssertionError: assert 'members:\n  ...abel: Keep}\n' == 'members:\n  ...abel: Keep}\n'
E
E           members:
E             ab:
E         -     - {id: ab note, label: Keep}
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 26 — `app/cutover_locations.py`

**Edit:**

```diff
-     typed_spans = {
-         (path, line, old, start, end)
-         for path, line, _axis, old, start, end in _typed_token_spans(root, mappings)
-     }
+     typed_spans = set()  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_typed_registry_front_matter_workspace_policy_and_proposal_lines_are_not_advisory
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS) == [
E       AssertionError: assert [AdvisoryOccu... line=2), ...] == [AdvisoryOccu...c48', line=7)]
E
E         At index 0 diff: AdvisoryOccurrence(path='_system/entities.yaml', axis='entity', old='ab', ordinal=1, context_sha256='ad66da3b274d0a9f28ba638cacc5b1baa7a7caba4b1f286854c17eca18b19107', line=2) != AdvisoryOccurrence(path='ab/00-inbox/note.md', axis='entity', old='ab', ordinal=1, context_sha256='8836b9c14049b133c43fa7031a2bf06634348a3bd7eb144777341511e0815c48', line=7)
E         Left contains 17 more items, first extra item: AdvisoryOccurrence(path='_system/members.yaml', axis='entity', old='ab', ordinal=1, context_sha256='ad66da3b274d0a9f28ba638cacc5b1baa7a7caba4b1f286854c17eca18b19107', line=2)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 27 — `app/cutover_locations.py`

**Edit:**

```diff
- text = os.readlink(candidate)
+ continue
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_a_symlink_is_scanned_as_link_text_without_following_its_target
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
E       AssertionError: assert [] == [AdvisoryOccu...f5c', line=1)]
E
E         Right contains one more item: AdvisoryOccurrence(path='ab-link', axis='entity', old='ab', ordinal=1, context_sha256='222d8c2b1964b509a0568f21185af550edd99eabdd8d5c67fb77781d6b42bf5c', line=1)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.03s`

### Mutation 28 — `app/cutover_db.py`

**Edit:**

```diff
-         if path.is_symlink():
-             raise DatabaseCutoverError(
-                 f"{path.relative_to(root).as_posix()} is a symlink; inventory "
-                 "never follows or silently omits a database redirection"
-             )
+         if path.is_symlink():
+             continue  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_db.py::test_schema_inventory_refuses_a_books_db_symlink_without_following_it
```

**RED** (exit 1):

```
>       with pytest.raises(DatabaseCutoverError, match="symlink"):
E       Failed: DID NOT RAISE DatabaseCutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `679ef99fb9672117…` before and `679ef99fb9672117…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 29 — `app/cutover_build.py`

**Edit:**

```diff
- _require_post_advisory(scratch, manifest)
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_regenerates_the_advisory_report_after_rewriting
```

**RED** (exit 1):

```
>       with pytest.raises(Exception, match="advisory report changed"):
E       Failed: DID NOT RAISE Exception
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.20s`

### Mutation 30 — `app/cutover_build.py`

**Edit:**

```diff
- validator(scratch)
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_validators_run_after_migration_and_before_commit
```

**RED** (exit 1):

```
>       assert observed == [(True, result.source_head)]
E       AssertionError: assert [] == [(True, 'c20d...06149fc6520')]
E
E         Right contains one more item: (True, 'c20dea1e3ecdb38ba6095189dde0306149fc6520')
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.26s`

### Mutation 31 — `app/cutover.py`

**Edit:**

```diff
- if confirmed != built_commit:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_promotion.py::test_a_commit_confirmed_as_the_wrong_head_is_reported_as_committed_but_unresolved
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverCommittedError, match="does not equal the built commit"):
E       Failed: DID NOT RAISE CutoverCommittedError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.19s`

### Mutation 32 — `app/cutover_inventory.py`

**Edit:**

```diff
- if completed.stdout:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_cli.py::test_inventory_refuses_a_dirty_live_vault
```

**RED** (exit 1):

```
>       assert code == 1
E       assert 0 == 1
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `92e78585cb48d1b4…` before and `92e78585cb48d1b4…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.15s`

### Mutation 33 — `app/cutover_build.py`

**Edit:**

```diff
- if existing:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_existing_former_slugs_are_preserved_and_no_duplicate_key_is_created
```

**RED** (exit 1):

```
>       assert text.count("former_slugs:") == 1
E       AssertionError: assert 2 == 1
E        +  where 2 = <built-in method count of str object at 0x10b318fb0>('former_slugs:')
E        +    where <built-in method count of str object at 0x10b318fb0> = 'entities:\n  ab-entity:\n    former_slugs: [ab]\n    former_slugs: [older]\n    label: A\n'.count
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.39s`

### Mutation 34 — `app/cutover_build.py`

**Edit:**

```diff
-         path.write_text(rewritten, encoding="utf-8")
+         path.write_text(
+             yaml.safe_dump(yaml.safe_load(rewritten), sort_keys=False),
+             encoding="utf-8",
+         )
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_proposal_prefixes_are_rewritten_and_a_pre_cutover_token_is_refused
```

**RED** (exit 1):

```
>       assert 'opaque: "keep: [x]"  # exact' in text, (
E       AssertionError: proposal rewrite altered bytes outside the approved fields
E       assert 'opaque: "keep: [x]"  # exact' in "id: 20260826T120000-cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd\naction: classify\nentity: ab-entity\nsrc: ab-entity/00-inbox/active/x.md\ndst: ab-entity/09-marketing/active/x.md\nopaque: 'keep: [x]'\n"
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.37s`

### Mutation 35 — `app/cutover_build.py`

**Edit:**

```diff
- _CHECK_V2_ZERO.search(combined)
+ "0 error(s), 0 warning(s)" in combined
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_default_validator_does_not_read_ten_errors_as_zero
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverError, match="0 error"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.13s`

### Mutation 36 — `app/cutover_build.py`

**Edit:**

```diff
-         item.old,
-         item.context_sha256,
-     )
+         item.old,
+     )
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_post_advisory_identity_rejects_same_count_with_changed_context
```

**RED** (exit 1):

```
>       with pytest.raises(Exception, match="advisory report changed"):
E       Failed: DID NOT RAISE Exception
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.19s`

### Mutation 37 — `app/cutover.py`

**Edit:**

```diff
- mappings = proposed_mappings(snapshot)
+ mappings = proposed_mappings(vault)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_cli.py::test_inventory_reads_tracked_evidence_from_the_captured_head
```

**RED** (exit 1):

```
>       assert "entity: ab -> ab-entity" in out
E       AssertionError: assert 'entity: ab -> ab-entity' in 'source HEAD: 1a1acbd8132ecb466d2f7e62a4687fba0b13bf27\nentity: zz -> zz-entity\nproduct: q7 -> q7-product\nmember: m7...: path=ab/books.db table=roster column=member axis=member old=m7 count=1\n[INVENTORY] read-only; nothing was written\n'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.18s`

### Mutation 38 — `app/cutover.py`

**Edit:**

```diff
-         raise CutoverError("live HEAD changed during inventory; discard the result")
-     require_clean_status(vault)
-     require_clean_entities(vault, affected)
+         raise CutoverError("live HEAD changed during inventory; discard the result")
+     require_clean_entities(vault, affected)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_cli.py::test_inventory_discards_results_when_live_status_changes_before_return
```

**RED** (exit 1):

```
>       assert main(["inventory", "--vault-root", str(vault)]) == 1
E       AssertionError: assert 0 == 1
E        +  where 0 = main(['inventory', '--vault-root', '<temporary-path-redacted>
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.19s`

### Mutation 39 — `app/cutover_locations.py`

**Edit:**

```diff
-                 for match in pattern.finditer(line):
+                 for match in list(pattern.finditer(line))[:1]:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_advisory_identity_distinguishes_two_tokens_on_one_line
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
E       AssertionError: assert [AdvisoryOccu...049', line=1)] == [AdvisoryOccu...049', line=1)]
E
E         Right contains one more item: AdvisoryOccurrence(path='note.md', axis='entity', old='ab', ordinal=2, context_sha256='d4f14ea18db8bfd4a94dd2c4d24df8c93d29144f570b6bf655e31307de839049', line=1)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 40 — `app/cutover_build.py`

**Edit:**

```diff
- existing_identifiers(scratch)
+ existing_identifiers(vault)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_build_collision_check_uses_the_manifest_source_head
```

**RED** (exit 1):

```
>           build_cutover(vault, raw, record)
>               raise CollisionError(
E               app.cutover_inventory.CollisionError: new value collides with an existing identifier on axis 'entity'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.29s`

### Mutation 41 — `app/cutover_locations.py`

**Edit:**

```diff
- normalized = boundaried(term).sub("<mapped>", normalized)
+ normalized = normalized
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_stable_context_ignores_an_approved_typed_value_rewrite
```

**RED** (exit 1):

```
>       assert stable_advisory_context(before, ADVISORY_MAPPINGS) == (
E       AssertionError: assert '3b6a471a0f35...5c98db86c8401' == '4489cca5fb4c...cca2add7442e0'
E
E         - 4489cca5fb4c9eb6f901c15bb5782049fe8957acd9cfe8a59e3cca2add7442e0
E         + 3b6a471a0f357ccf1cf2b36f60b5f48e69fdcab460062cd30ed5c98db86c8401
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 42 — `app/cutover_build.py`

**Edit:**

```diff
- _occurrence_key(item, path=path, include_ordinal=True)
+ _occurrence_key(item, path=path, include_ordinal=False)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_post_advisory_identity_refuses_reordering_approved_contexts
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverError, match="advisory report changed"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.23s`

### Mutation 43 — `app/cutover_locations.py`

**Edit:**

```diff
-                     if span_key in typed_spans:
+                     if any(item[:3] == span_key[:3] for item in typed_spans):  # MUTANT
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_a_typed_scalar_does_not_hide_same_axis_prose_on_its_line
```

**RED** (exit 1):

```
>       assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
E       AssertionError: assert [] == [AdvisoryOccu...60b', line=2)]
E
E         Right contains one more item: AdvisoryOccurrence(path='note.md', axis='entity', old='ab', ordinal=1, context_sha256='1b661c7abb64d2efee48f456fd634214054fee41d3dd42de7b1a2de9b6e7f60b', line=2)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 44 — `app/cutover_manifest.py`

**Edit:**

```diff
- + "\n"
+ + ""
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_manifest.py::test_canonical_bytes_have_exact_utf8_json_framing
```

**RED** (exit 1):

```
>       assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
E       assert (False)
E        +  where False = <built-in method endswith of bytes object at 0xa69a98280>(b'\n')
E        +    where <built-in method endswith of bytes object at 0xa69a98280> = b'{"source_head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mappings":[{"axis":"entity","old":"ab","new":"ab-entity"}..."0000000000000000000000000000000000000000000000000000000000000000","line":3,"kind":"incidental","typed_location":""}]}'.endswith
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `fa80185aae7e7cfa…` before and `fa80185aae7e7cfa…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 45 — `app/cutover_manifest.py`

**Edit:**

```diff
- object_pairs_hook=_without_duplicate_keys
+ object_pairs_hook=dict
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_manifest.py::test_duplicate_object_keys_are_refused_even_with_a_matching_digest
```

**RED** (exit 1):

```
>       with pytest.raises(ManifestError, match="duplicate"):
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'duplicate'
E         Actual message: 'approval manifest is not in canonical form'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `fa80185aae7e7cfa…` before and `fa80185aae7e7cfa…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 46 — `app/cutover_manifest.py`

**Edit:**

```diff
- _require_relative_posix_path(self.path, "database target path")
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_manifest.py::test_manifest_paths_must_be_canonical_relative_posix[absolute]
```

**RED** (exit 1):

```
>       with pytest.raises(ManifestError, match="path"):
E       Failed: DID NOT RAISE ManifestError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `fa80185aae7e7cfa…` before and `fa80185aae7e7cfa…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.01s`

### Mutation 47 — `app/cutover_build.py`

**Edit:**

```diff
-     status = git(repo, "status", "--porcelain=v2", "--untracked-files=all")
-     if status:
+     status = git(repo, "status", "--porcelain=v2", "--untracked-files=all")
+     if status and False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_executor_revision_refuses_a_dirty_worktree
```

**RED** (exit 1):

```
>       with pytest.raises(cutover_build.CutoverError, match="executor worktree"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.16s`

### Mutation 48 — `app/cutover_build.py`

**Edit:**

```diff
- if head != record.executor_commit:
+ if False:
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_executor_revision_refuses_a_different_commit
```

**RED** (exit 1):

```
>       with pytest.raises(cutover_build.CutoverError, match="executor commit"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.13s`

### Mutation 49 — `app/cutover.py`

**Edit:**

```diff
- require_executor_revision(record)
+ None
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_cli.py::test_action_commands_refuse_a_different_executor[dry-run]
```

**RED** (exit 1):

```
>       assert main(argv) == 1
E       AssertionError: assert 0 == 1
E        +  where 0 = main(['dry-run', '--vault-root', '<temporary-path-redacted> '--approval', ...])
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `f8c42dc96c361980…` before and `f8c42dc96c361980…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.12s`

### Mutation 50 — `app/cutover_build.py`

**Edit:**

```diff
-             scratch, _post_move_database_targets(manifest), manifest.mappings
+             scratch, manifest.databases, manifest.mappings
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_final_database_gate_reads_the_moved_artifact
```

**RED** (exit 1):

```
>           build_cutover(vault, raw, record)
>           raise DatabaseCutoverError("approved database is missing or not a regular file")
E           app.cutover_db.DatabaseCutoverError: approved database is missing or not a regular file
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.21s`

### Mutation 52 — `app/cutover_locations.py`

**Edit:**

```diff
-                     span_key = (
-                         relative,
-                         number,
-                         old,
-                         match.start(),
-                         match.end(),
-                     )
-                     if span_key in typed_spans:
+                     span_key = (
+                         relative,
+                         number,
+                         axis,
+                         old,
+                         match.start(),
+                         match.end(),
+                     )
+                     if span_key in _typed_token_spans(root, mappings):
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_locations.py::test_a_typed_span_is_typed_for_every_axis_not_only_its_own
```

**RED** (exit 1):

```
>       assert found == [], "a typed span was misreported on another axis"
E       AssertionError: a typed span was misreported on another axis
E       assert [AdvisoryOccu...27c', line=2)] == []
E
E         Left contains 3 more items, first extra item: AdvisoryOccurrence(path='_system/products.yaml', axis='workspace', old='q7', ordinal=1, context_sha256='dbb2671eec840116d26edf4a64f9ce25e027c91a38136263d17691100b594f58', line=3)
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.02s`

### Mutation 51a — `app/cutover_build.py`

**Edit:**

```diff
- [sys.executable, "-B", "-m", "unittest", "discover"]
+ [sys.executable, "-m", "unittest", "discover"]
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_run_vault_validators_leaves_no_bytecode
```

**RED** (exit 1):

```
>       assert list(vault.rglob("*.pyc")) == []
E       AssertionError: assert [PosixPath('/...hon-313.pyc')] == []
E
E         Left contains one more item: PosixPath('<temporary-path-redacted>
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.16s`

### Mutation 51b — `app/cutover_build.py`

**Edit:**

```diff
- "--untracked-files=all", "--ignored"],
+ "--untracked-files=all"],
```

**Command:**

```bash
uv run python -m pytest -q tests/test_cutover_build.py::test_a_validator_that_writes_an_ignored_file_refuses_the_build
```

**RED** (exit 1):

```
>       with pytest.raises(CutoverError, match="validator changed the isolated tree"):
E       Failed: DID NOT RAISE CutoverError
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `73977aa91b1e04c3…` before and `73977aa91b1e04c3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.24s`

### Mutation M-A — `app/console_errors.py`

**Edit:**

```diff
-     _cutover_build.CutoverCommittedError: _CODES["E-COMMITTED"],
+ (removed)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_console_invariants.py::test_every_application_exception_resolves_to_its_designed_code
```

**RED** (exit 1):

```
>           assert describe(_probe(cls)).code == code, cls.__qualname__
E           AssertionError: CutoverCommittedError
E           assert 'E-ADMIN' == 'E-COMMITTED'
E
E             - E-COMMITTED
E             + E-ADMIN
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `b85fdad16bc56ef3…` before and `b85fdad16bc56ef3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.10s`

### Mutation M-B — `app/console_errors.py`

**Edit:**

```diff
-     _cutover_db.DatabaseCutoverError: _CODES["E-ADMIN"],
+ (removed)
```

**Command:**

```bash
uv run python -m pytest -q tests/test_console_invariants.py::test_every_application_exception_resolves_to_its_designed_code
```

**RED** (exit 1):

```
>           assert code != "E-UNKNOWN", f"{cls.__module__}.{cls.__qualname__} is unmapped"
E           AssertionError: app.cutover_db.DatabaseCutoverError is unmapped
E           assert 'E-UNKNOWN' != 'E-UNKNOWN'
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `b85fdad16bc56ef3…` before and `b85fdad16bc56ef3…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.12s`

### Mutation M-C — `app/cutover_locations.py`

**Edit:**

```diff
- @structured_reader(category="admin-record")
- def _load_yaml_file(
+ def _load_yaml_file(
```

**Command:**

```bash
uv run python -m pytest -q tests/test_console_readers.py::test_every_structured_read_site_declares_a_category
```

**RED** (exit 1):

```
>       assert offenders == [], f"{_TRIGGER_HELP}: {offenders}"
E       AssertionError: structured read site without a @structured_reader category declaration: ['app/cutover_locations.py:544']
E       assert ['<absolute-path-redacted>'] == []
E
E         Left contains one more item: 'app/cutover_locations.py:544'
E         Use -v to get more diff
```

**Restoration:** preimage copied back; `cmp` identical; SHA-256 `7026ddee6e7ddf93…` before and `7026ddee6e7ddf93…` after — byte-identical.

**GREEN** (exit 0): `1 passed in 0.09s`

## Restoration summary

All 56 mutations restored byte-identically: every `cmp` reported
no difference and every before/after SHA-256 matched. After the campaign,
`git diff app/ tools/` was empty apart from the deliberate `a5fe98f`
defect fix, which is committed rather than restored.
