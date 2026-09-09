# Lifecycle Validation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this approved, bounded correction. Preserve the existing isolated branch and all evidence.

**Goal:** Detect incomplete required lifecycle structure without repairing it.

**Architecture:** Correct the public contract and develop the private validator in an isolated external copy. Enumerate manifest-required modules from flags, validate required filesystem kinds without following symlinks, and retain E4 for structural failures.

**Tech Stack:** Existing Python, unittest, YAML registries; no new dependencies.

**Spec:** Owner-approved lifecycle correction: conventions v2 governs; archive omits `active/`, `archive/`, and `status.md`; analytics requires its declared additions.

## Global Constraints

- No live-vault mutation, repair, proposal, approval, or gate trial.
- No private implementation or instance data in this repository.
- No push, pull request, merge, deployment, or evidence deletion.
- Preserve registry activation from explicit flags only.
- Preserve E1–E4 and existing warning semantics outside required structure.

## Task 1: Correct and test required structure

**Files:** Public `AGENTS.md`; external private candidate `check_v2.py` and `test_lifecycle_structure.py`.

**Interface:** Existing validator CLI, invoked with a synthetic vault path. Failures return nonzero and contain E4; complete fixtures return zero.

- [ ] Capture fresh opaque preimages and verify private candidate copies.
- [ ] Correct archive wording in `AGENTS.md`.
- [ ] Add black-box synthetic tests with independently specified fixtures:

```python
result = run_validator(complete_fixture_without_active_directory)
assert result.returncode == 1
assert "E4" in result.stdout
```

- [ ] Cover each base child, each analytics addition, missing module/entity roots, wrong kinds, symlinks (including ancestors and dangling targets), special files, complete fixtures, archive exemption, and flag-only activation.
- [ ] Run focused tests against the unchanged validator and record failure counts outside the repositories.
- [ ] Implement required-shape enumeration from `lifecycle_pattern` and `extensions`. Ordinary modules require `_templates/`, `active/`, `archive/`, and `status.md`; the archive exception retains `_templates/` but does not require the other three children. Directory extensions retain their trailing-slash type declarations.
- [ ] Check every ancestor before descent; refuse redirected or non-directory roots and non-regular required files. Keep legacy content traversal from following invalid entries.
- [ ] Run focused regressions and the complete copied private unittest suite; correct synthetic fixtures that previously omitted required lifecycle children.

## Task 2: Verify and propose governance

**Files:** `docs/LIFECYCLE-REPAIR-PROPOSAL.md`; external evidence and candidate patch.

- [ ] Document a proposed sanctioned repair operation, including review binding, isolated commit, rollback/revert, filesystem evidence, and Gate 3 recognition. Do not implement or execute it.
- [ ] Run public tests, Gitleaks, public audit, and trusted-local combined history audit with sanitized output only.
- [ ] Run the candidate validator read-only against live state and report its findings as expected unresolved defects, not successful repair.
- [ ] Obtain independent scoped review; resolve actionable findings and repeat affected checks.
- [ ] Verify live preimages unchanged, preserve the external candidate and patch, and report exact public branch/HEAD plus sanitized results. Leave all changes local for owner review.
