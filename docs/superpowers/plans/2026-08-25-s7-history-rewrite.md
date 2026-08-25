# S7 branch history rewrite — 2026-08-25

The S7 branch's history was rewritten once, on 2026-08-25, with explicit
authorization. This file is the record. It exists because the rewrite changed
81 commit SHAs, and several approved documents cite the originals as evidence
of when review happened.

## Why

`tools/public_repo_audit` failed on this branch, in both HEAD and `--history`
mode: 159 findings across 80 commits, all of one type, `absolute-private-path`.
Every finding traced to two synthetic fixtures in
`tests/test_review_tokens.py`, introduced with the file itself in the first S7
task and carried forward by every later commit:

The fixture embedded a POSIX home-directory absolute path — the shape the
audit rejects, written here as `<HOME>/someone/Vault/secret.md` rather than
literally, for the reason given below — and the paired assertion checked that
its leading segment never appeared in a rendered exception.

The string names no real person, entity, or vault. It is test data whose whole
purpose is to prove that a path-shaped secret never reaches a rendered
exception. But the audit's rule is deliberately shape-based —
`/(?:Users|home)/[^/\s]+/` — and it cannot distinguish synthetic from real.
That is the correct design for a secrets gate, and `BUILD.md` forbids
weakening the scanner or adding a broad ignore to silence it.

A forward-only fix would have cleared HEAD while leaving `--history` failing,
so it could not satisfy a gate that is mandatory before merge. The branch had
never reached `origin` and no remote ref contained the commits, which made this
the least disruptive moment to rewrite.

## Scope

Two deterministic string substitutions, applied across the whole branch range:

- the home-directory fixture path → `/vault-root/holder/secret.md`
- its leading segment in the assertion → `"/vault-root/"`

The original strings are deliberately not reproduced anywhere in this file.
The first version of this record quoted them verbatim to show what had been
removed, and the audit immediately failed on *this document* — two findings,
same rule, in the file explaining the fix. The gate was right and the document
was wrong: a shape-based rule cannot make an exception for prose, and it should
not be asked to.

The replacement matches none of the audit's three private-path patterns and
preserves the tests' intent: the fixture is still an absolute, path-shaped
value, and the assertion still proves it does not leak.

Nothing else changed. Diffing the pre-rewrite tip against the new one shows
three changed lines in one file:

```
git diff c95b410638f3 83b7091e0c7d --name-only
tests/test_review_tokens.py
```

Commit count, order, messages, authors, and dates are unchanged: 83 commits
from the baseline, of which 2 kept their original SHAs because they predate
the file and their trees were untouched.

## Verification after the rewrite

```
tools/run_gitleaks.sh .                            no leaks found (328 commits)
public_repo_audit --repo . --history               CLEAN, exit 0
uv run python -m pytest -q                         1461 passed
s7_mutation_campaign.py                            see the ledger's campaign block
git diff --check / git status --porcelain          clean
```

## Cited approval SHAs are NOT rewritten in place

The approved documents record when review happened — "Amendment 1 (APPROVED at
`e0316cc`)", "Amendment 2 (APPROVED at `0492d94`)", and so on. Those are
historical facts. Rewriting them to the new SHAs would assert that approvals
were given against commits that did not exist when they were given, so the
originals stay as written and this table carries the correspondence.

### Recovery bundle

A bundle of the complete pre-rewrite history was written outside the
repository before any history was touched. Its location is deliberately not
recorded here; the evidence that identifies it is:

```
sha256                543856feb85710677c24a0cc760a1294d99722fd17703c05e36571bde044767b
git bundle verify     22 refs; records a complete history; hash algorithm sha1
```

`refs/original/refs/heads/codex/s7-bound-review-tokens` still points at the
pre-rewrite tip, giving a second, in-repository recovery path. It affects
neither audit and Gitleaks remains clean with it present. **Do not remove it
without separate authorization.**

**A fresh clone will not resolve the original SHAs.** They currently resolve in
the rewriting clone only because `filter-branch` left its backup ref at
`refs/original/refs/heads/codex/s7-bound-review-tokens`. A recovery bundle of
the complete pre-rewrite history was taken outside the repository before any
history was touched.

### Key checkpoints

| Cited as | Now | What it is |
| --- | --- | --- |
| `e0316cc` | `14272308bea0` | Amendment 1 approved |
| `0492d94` | `5632f44a7e6f` | Amendment 2 approved |
| `519066c` | `e5c946677b46` | Amendment 3 Stage 2 design approved |
| `c95b410` | `83b7091e0c7d` | pre-rewrite tip → post-rewrite tip |

### Full correspondence

| Before | After |
| --- | --- |
| `caae8408ed51` | `caae8408ed51` |
| `e2d13dd2a74a` | `e2d13dd2a74a` |
| `5de9ab0e96e8` | `a2868d8c2723` |
| `069b26b81b66` | `aaea62ddfb31` |
| `fc5fccead306` | `90a55b642097` |
| `e31ae582516a` | `e6e15dfebd3f` |
| `45dd191eaa08` | `fa3d03c7caea` |
| `e8d4a3cdcb59` | `72f361b508ed` |
| `b94b743c8ea9` | `f77b1c5a46ce` |
| `c0b8a8122363` | `266aee6589a3` |
| `73c1016c7b6a` | `9c1ea7cce401` |
| `46fd10cde808` | `5088e038fb06` |
| `e0316cc2bce3` | `14272308bea0` |
| `e8e10e7a3d29` | `f9c41a3c62c3` |
| `a3cbf044cd0c` | `236cba96f084` |
| `b5e3d9e97130` | `06ba7143bec5` |
| `69e007d57814` | `ee9a1c809c91` |
| `b7201990fab5` | `910305551a42` |
| `abe451f1d9d5` | `2730c513356d` |
| `803088cba89c` | `3fcf921fda9d` |
| `e39d766a0797` | `da41eea4f6a4` |
| `38ebb0a49454` | `9cee3a505279` |
| `5275788cd301` | `12990a8cd529` |
| `4b14155ade80` | `abce9d576862` |
| `69db4257c622` | `311db2e6a190` |
| `561c4613236b` | `b272f6a8d9dc` |
| `6ab9a2083d0f` | `7f0bfabbeaca` |
| `6c017e6f448a` | `28655d22fb53` |
| `ce951c30658b` | `9384e411d2ce` |
| `65a4c0228250` | `86cf75443bfe` |
| `177f0a4ce7b3` | `8a387970714c` |
| `cc6b14240e14` | `21acc3aeaed2` |
| `42a839968ff8` | `a9f3e3c8bc6b` |
| `4975134d5308` | `383b9b3d116f` |
| `a805c7155693` | `573ffb0fdf22` |
| `40ce53cd9d53` | `ff1eea22d71e` |
| `19b8e5906d05` | `41fae756519e` |
| `cd50b5a5c6f4` | `a37cb7982af8` |
| `1d54dc137d79` | `ab33567bc28f` |
| `f26bef60f824` | `1918b8c90ee3` |
| `4aebfd374c68` | `c6e4221d7535` |
| `8c92797cd18b` | `afc7dcbb5c24` |
| `5b2567180b95` | `de7d0a44d24c` |
| `e51b5b3530b7` | `963a6ae2b78e` |
| `81ab24a2b8ca` | `5b9f469921c4` |
| `96758031c47c` | `faaceb4070da` |
| `5ea1d0c26b50` | `1b5e52259de4` |
| `218b99193177` | `087ae6d3e7ce` |
| `77a21e42c6e1` | `3a56fefcc764` |
| `0492d94c3ab7` | `5632f44a7e6f` |
| `8b5ae5fd0482` | `81d9e346557e` |
| `b998babd87c0` | `751309453b4c` |
| `e79dc2c17ceb` | `9168a04626a0` |
| `02e0eb280c65` | `670c25c8fd5f` |
| `7bad7ad404f9` | `500cae717a8c` |
| `23b02b8fee55` | `fffe34a22910` |
| `34b6ace9dc0d` | `9ea71c2b8dc6` |
| `350c327b51ec` | `8c92df400271` |
| `3e126e917db7` | `9d0d96fbf0d0` |
| `38e2f3e32212` | `23a46d221b5b` |
| `76224c0c75dd` | `015705747f49` |
| `0d79c0bf1caa` | `3bf22cd81d7f` |
| `57b6f78d1422` | `5a8bffe62b77` |
| `ef1c77a13ed8` | `6b8d472ccce0` |
| `519066c61b1e` | `e5c946677b46` |
| `2b1da7cd593a` | `a34dbd472ec8` |
| `9d5df6da248a` | `3b7ed0e8b7c4` |
| `2316add5ca9f` | `2dc6e5418bff` |
| `d36c6af23ea8` | `949b1df126dd` |
| `ee337057df97` | `78240a691e6c` |
| `daab5f2304c8` | `4a803605de00` |
| `eb50a6173ba5` | `fc3a7e1f58c6` |
| `150eac46517b` | `cf495b2cc796` |
| `d64a2b7e9a9c` | `5c7c3917e55e` |
| `a6aab70d091b` | `85a1e89b8122` |
| `a4dcf7cc17e1` | `ce83636ad168` |
| `192cd4d96f91` | `b427cc619369` |
| `53e141b20764` | `63384d6e2ea6` |
| `7689f0cf20d4` | `1bbd9c04fef2` |
| `1f5c3d02f566` | `94148561958a` |
| `032d4f473be7` | `f8e2aa0f7817` |
| `7d08ff2aefd4` | `2e6d1c5b9f45` |
| `c95b410638f3` | `83b7091e0c7d` |
