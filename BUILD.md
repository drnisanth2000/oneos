# BUILD — how to keep going without asking

Companion to `AGENTS.md`. That file says what the system *is*; this one says
how to advance it. Read both.

Authority: `$ONEOS_VAULT/_system/docs/oneos-spec.md` §10 is the build order.
This file adds the loop, the self-checks, and the four points where you must
stop and ask a human.

---

## 1. The loop — run this for every step

```
1. Read the step in spec §10. Read anything it names.
2. Build it. Smallest thing that satisfies the done-when.
3. Run §3 self-check. All three must pass.
4. Commit. One commit per step, message naming the step number.
5. Move to the next step. Do not skip. Do not batch two steps
   unless the first would only exist to be deleted (see step 1-2
   precedent: they were merged for exactly that reason).
```

If a step's done-when cannot be met, stop and say why. Do not redefine the
done-when to match what you built.

---

## 2. Steps 3–11, one line each

| # | Step | Done when |
|---|---|---|
| 3 | `schema.py` — **extract** the Pydantic front-matter model from `policy_enforcer.py` + `check_v2.py`; the enforcer then imports it | Both validators agree on 100 real vault files. It is a refactor of tested code, not a rewrite |
| 4 | Rename command — dry-run default, one atomic commit | Throwaway entity renames cleanly **and** a `.sensitive/` read is still denied afterwards. **Read the warning in §4 first** |
| 5 | Folder-drop adapter — `watchdog` on `_dropbox/`, text extraction, `source_ref` + `sha256`, **PII filter** | A PDF dropped in appears in `00-inbox/active/` within a minute, with PII stripped before anything is written |
| 6 | Triage screen — list unsorted, show parsed fields + proposed `block:`/`sub:`, keys `j`/`k`/`a`. Creates `_system/classifier/rules.yaml` | Faster than Obsidian + Dataview on 20 real items, measured with a clock |
| 7 | Outbox write path — confirming a classification writes a **proposal**, never a file move. Render the git diff | Diff renders; no file has moved |
| 8 | Approve / reject | One commit per approval; `git revert` undoes it with zero manual cleanup |
| 9 | Registry CRUD — add/edit direct, **delete via outbox** with a reference count | Deleting a product shows what breaks before it runs |
| 10 | Email adapter — IMAP poll into the step-5 envelope | Same envelope, same PII filter, no second code path |
| 11 | Deploy — Compose + Caddy on the VPS beside Hermes | **Only after the Phase 1 gates in spec §11 pass.** Stop and ask |

---

## Safety Foundation — next, before live gate trials

The original steps 1–10 exist, but review found that their tests do not yet
prove the safety guarantees against real adapter output or concurrent requests.
This is hardening of Phase 1, not a new feature phase. Complete in this order:

| # | Hardening task | Done when |
|---|---|---|
| S1 | **Commit on ingest.** After redaction, the shared ingest path creates one `ingest:` commit containing only the inbox receipt. Raw content stays outside the vault. | A real adapter-created item is tracked; approval is one later commit; reverting approval restores it to triage. Duplicate intake is a no-op. |
| S2 | **Request-local entity scope.** Bind an immutable scope per request and validate the entity against `entities.yaml`. | Concurrent requests for different entities cannot read, propose, approve, or render each other's paths. |
| S3 | **Server-owned destinations.** Validate active module and `sub:` against registries, derive `block` server-side, and prove resolved paths remain in scope. | Tampered entity/module/sub/block values and traversal attempts fail before a proposal is written. |
| S4 | **Fresh, collision-safe proposals.** Store source SHA-256 and use a collision-safe proposal id. | Changed or missing sources are visibly refused at approval; same-second proposals never overwrite each other. |
| S5 | **Isolated Git transaction and audit.** Commit exactly the reviewed paths, restore filesystem/index/proposal state on failure, and make Gate 3 validate both sanctioned message type and changed paths—including `ingest:` receipts. | Unrelated staged and unstaged changes remain untouched; injected commit failure leaves no partial move; a misleading commit prefix cannot sanction unrelated paths; one revert restores the full approved batch. |
| S6 | **Visible Console failures.** Return specific safe errors through the Command Center surface. | Stale, invalid, missing, cross-scope, and Git failures are visible and no route silently swallows them. |

Do not add dashboard cards, drag-drop UI, general workflows, or new agent skills
inside this hardening sequence. The OneOS shell may adopt the approved
workspace switcher and **Blocks / Modules** terminology only where required to
surface scoped safety state.

---

## 3. Self-check — after every step, before every commit

All three. No exceptions, no "this step doesn't touch that."

```bash
# 1. Your tests
uv run python -m pytest -q

# 2. The vault's tests — you must not have broken them
cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q; cd -

# 3. The vault is unchanged and clean
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT" | tail -2
git -C "$ONEOS_VAULT" status --short
```

Check 2 must show 34+ tests OK. Check 3 must show
`0 error(s), 0 warning(s)` **and an empty git status** — if the vault has
uncommitted changes, you wrote to it, which is invariant 1 violated. Revert
and start the step again.

### Public and private repository audits

Install the exact Gitleaks release locally on Apple Silicon after verifying its
published SHA-256. The release asset is a developer tool, not a project
dependency:

```bash
curl --fail --location --silent --show-error \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_arm64.tar.gz \
  --output /private/tmp/gitleaks_8.30.1_darwin_arm64.tar.gz
echo "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5  /private/tmp/gitleaks_8.30.1_darwin_arm64.tar.gz" | shasum -a 256 -c -
tar -xzf /private/tmp/gitleaks_8.30.1_darwin_arm64.tar.gz -C /private/tmp gitleaks
install -m 0755 /private/tmp/gitleaks "$HOME/.local/bin/gitleaks"
export PATH="$HOME/.local/bin:$PATH"
```

Run the general secret/history gate first, then the finite OneOS audit. The
trusted local integration agent alone adds live registry-derived terms:

```bash
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

GitHub CI runs Gitleaks and the vault-free OneOS command against synthetic
repository state only. It receives no vault path, registry, database, or
credential. The trusted local integration agent runs the final command before
merge when a change reads or interprets vault structure.

### Standing regression — E4 visibility

Re-run the synthetic missing-module fixture after any change to `vault.py`, the
sidebar, or `scope.py`:

```bash
uv run pytest tests/test_vault.py -q
```

A missing module rendering as a shorter list is the bug this whole check
exists for. Four validators once missed exactly this.

### Residence and migration checks

Run these checks for any feature that inventories, imports, extracts, copies,
archives, quarantines, or disposes of files:

- **Copy verification:** compute SHA-256 before and after the copy and refuse
  quarantine when the hashes differ. Never implement move-and-delete.
- **Batch reversibility:** one approved batch produces exactly one commit;
  `git revert` restores every affected vault path with no manual cleanup.
- **Provenance:** every extracted database row or retained knowledge entry
  resolves to its source reference and recorded hash. Broken provenance is a
  failed import, not a warning.
- **Inventory reconciliation:** completion requires every inventoried item to
  be accounted for at its original path, approved destination, or quarantine.
- **Hard failures:** missing items, conflicting canonical copies, duplicate
  destinations, checksum mismatches, and unaccounted inventory stop the run.

Migration discovery is read-only. Any later destructive or quarantine-purge
decision is batched where practical, proposed through the outbox, and executed
only after approval. These checks do not authorize new physical sub-folders;
the `sub:` front-matter convention and 15-file threshold still govern them.

---

## 4. Hard stops — ask the human, do not decide

**Step 4, the rename.** Policy and registry files can contain entity slugs.
One edit fails *open*:

```
action-policy.yaml
  {read, paths: ["<entity>/**"], except: ["<entity>/.sensitive/**"]}
```

Rename the allow path, miss the `except:`, and `.sensitive/` becomes
agent-readable. The command must rewrite both halves atomically, and the test
must assert the denial still holds. Show your plan before writing this one.

**Step 5, the PII filter.** PII committed to git needs history rewriting to
remove — the only irreversible mistake in this design. It ships *with* the
first adapter, never "in a follow-up." Show the filter rules before wiring the
watcher.

**Step 11, deploy.** Gates first. Never deploy to prove a gate.

**Any change to `$ONEOS_VAULT/_system/conventions.md` or the registries.**
Those are frozen. If the app needs a convention changed, that is a
`decisions.md` entry the human writes, not a file you edit.

**Any write to curated content outside `<entity>/outbox/`.** The only intake
exception is a redacted triage receipt committed immediately and alone with an
`ingest:` message. Registry add/edit and the tested rename admin operation keep
their existing direct, one-commit rules. Including "just fixing" a stale vault
doc is not an exception; report it instead.

---

## 5. When you are unsure

State the ambiguity and what you would do, then continue with the safer
reading. Do not stop the whole build for a judgement call that has an obvious
conservative answer — but do not silently pick the convenient one either.

The failure mode this project keeps hitting: a check that iterates over what
exists can never report what is absent. When you write a validator, a reader,
or a UI list, ask what it does when the thing it is looking for is *missing*.
That question has found four real bugs so far and no false alarms.
