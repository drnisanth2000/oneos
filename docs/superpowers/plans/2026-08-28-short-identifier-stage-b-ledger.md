# Short-Identifier Cutover Stage B Mutation Ledger

**Date:** 2026-08-28  
**Scope:** Post-cutover enforcement of the five-character minimum for entity,
product, member, and workspace identifiers only.

This ledger records five independently applied mutant edits. Each mutation
made its named test RED, was reversed, matched its pre-mutation SHA-256, and
then returned GREEN under a fresh `PYTHONPYCACHEPREFIX`. Project identifiers,
module ids, block ids, flags, submodule ids, and other generic vocabulary stay
on their existing grammar-only boundary.

No live identifier, private vault path, private commit id, or captured private
test path is recorded here. Private diagnostics below are deliberately reduced
to their non-reflective assertion.

## Method

For each row:

1. Record the target file's SHA-256.
2. Apply exactly the mutant edit shown below.
3. Run only the test that owns the changed rule, using a fresh external Python
   bytecode cache and with pytest's cache provider disabled where applicable.
4. Require a non-zero exit caused by the intended assertion.
5. Reverse the edit, compare the SHA-256 with step 1, and rerun GREEN.

The public target hashes after restoration were:

```text
b370e309d5d3d7d21bf7933516e6db06c6ba93da3fb1d1f8958fe1ecb75e795b  app/identifiers.py
c61b3096ed903a6a53ca175133a8d2e6070e9f11bbeaf7d2252f6766f2100729  app/rename.py
098ae41f96ad873f8b4b54ab755d488bf2fe2681ce25cbc47e149499127bd2a3  app/vault.py
ab54959710df5c735d2d215e20194e9ce9c53d56ab85be60c83d65907c4e3b74  app/destinations.py
```

The private wizard also matched its pre-mutation SHA-256 after restoration;
the value is intentionally not copied into the public repository.

## M1 — shared floor cannot fail open

**Edit:**

```diff
 def meets_floor(value: str) -> bool:
-    return len(value) >= IDENTIFIER_MINIMUM_LENGTH
+    return True
```

**Command:**

```text
uv run python -m pytest -q -p no:cacheprovider \
  tests/test_stage_b_identifier_floor.py --tb=short
```

**RED:** `25 failed, 3 passed`; the first failure was
`DID NOT RAISE EntityManifestError`. The failures covered entity catalog
loading and selection, receipt paths and offline discovery, product/member
registry reads, delete proposal creation, every workspace axis field, rename,
and the shared-constant guard.

**Restored GREEN:** `28 passed`.

## M2 — the floor must not spread to project names

**Edit:**

```diff
-    if axis in REGISTRY_AXES and not meets_floor(new):
+    if not meets_floor(new):
```

**Command:**

```text
uv run python -m pytest -q -p no:cacheprovider \
  tests/test_stage_b_identifier_floor.py::test_project_rename_keeps_its_existing_grammar_only_boundary \
  --tb=short
```

**RED:** `RenameError: new registry identifier is shorter than five
characters`.

**Restored GREEN:** the focused test passed.

## M3a — vault vocabulary remains grammar-only

**Edit:** add `and len(value) >= 5` to `app.vault._is_registry_id`.

**Command:**

```text
uv run python -m pytest -q -p no:cacheprovider \
  tests/test_stage_b_identifier_floor.py::test_generic_registry_vocabulary_keeps_its_existing_grammar \
  --tb=short
```

**RED:** `assert is_vault_registry_id("x")` was false.

**Restored GREEN:** the focused test passed.

## M3b — destination vocabulary remains grammar-only

**Edit:** add `and len(value) >= 5` to
`app.destinations._is_registry_id`.

**Command:** the same focused command as M3a.

**RED:** `assert is_destination_registry_id("x")` was false.

**Restored GREEN:** the focused test passed.

## M4 — the private wizard cannot recreate a sub-floor entity

**Edit:** remove the `len(name) < IDENTIFIER_MINIMUM_LENGTH` refusal from
`_system/scripts/oneos_wizard.py`.

**Command:**

```text
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest \
  test_identifier_floor.IdentifierFloorTests.test_wizard_rejects_four_characters_and_accepts_five
```

**RED:** `AssertionError: SystemExit not raised`.

**Restored GREEN:** `Ran 2 tests ... OK` for the complete private Stage B test
module.

## Arithmetic

- Four public mutant edits: M1, M2, M3a, M3b.
- One private mutant edit: M4.
- **Five total Stage B mutant edits, five attributable RED results, five
  byte-identical restores, and five restored GREEN results.**

Discarded non-killing attempts: **zero**.
