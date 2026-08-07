# OneOS Canonical Naming and GitHub Design

Date: 2026-08-07
Status: implemented

## Purpose

Establish **OneOS** as the only active product name and prepare the application
repository for private, agent-assisted development through GitHub without
exposing the private Grey Matter vault or instance-specific information.

This is a naming and development-workflow change. It does not alter OneOS
behavior, schema, phase order, dependencies, or authority boundaries.

## Naming contract

The canonical product name is **OneOS**.

| Concern | Canonical name | Meaning |
| --- | --- | --- |
| Complete product | OneOS | The complete local-first operating system |
| Human interface | OneOS | The product UI; “web interface” may describe its delivery mechanism but is not a product name |
| Private system of record | Grey Matter | The Markdown, SQLite, and Git-backed vault |
| Deterministic control boundary | Command Center | Scope, policy, proposals, validation, and approved Git transactions |
| Asynchronous agent | Hermes | Schedules, delivery, and judgement through proposals |
| Source repository | `oneos` | The instance-neutral application repository |
| Python project | `oneos` | The installable project name |
| Canonical specification | `oneos-spec.md` | The authoritative implementation specification |

The following names are deprecated in all active material:

- LifeOS
- OneOS Web
- OneOS Console

“Grey Matter,” “Command Center,” and “Hermes” remain because they identify
components with distinct responsibilities; they are not alternative product
brands.

The archived `lifeos-adoption-guide-legacy.md` is the sole historical naming
exception. Its archive notice must continue to state that it is research, not
implementation authority. Active documents may link to that filename only to
identify the archive.

## Cutover scope

The hard cutover covers both the public application repository and active
private-vault system material.

Application repository changes include:

- repository headings, UI titles, package metadata, module docstrings, and CLI
  descriptions;
- `AGENTS.md`, `BUILD.md`, `PRODUCT-THESIS.md`, status material, tests, and
  other active documentation;
- references to the renamed canonical specification;
- repository slug and canonical commands.

Private system changes include:

- active system documentation and Hermes grounding;
- tool filenames and identifiers such as the filesystem MCP service and entity
  scaffold wizard;
- actor-policy identifiers, tests, and every active reference to them;
- `oneos-web-spec.md` becoming `oneos-spec.md` with all links reconciled.

Internal identifier changes must be atomic within their repository. A renamed
actor or tool cannot be committed until its policy entry, callers, tests, and
documentation agree.

## Local path transition

The canonical development path becomes `~/code/oneos`.

To avoid invalidating active desktop tasks or tools that know the current
physical directory, the first implementation may create `~/code/oneos` as a
symlink to the existing checkout. All new guidance and commands use only the
canonical symlink. The physical directory can be renamed later when no process
depends on it; its temporary physical name is a filesystem compatibility detail,
not a supported product alias.

## GitHub model

Create a private GitHub repository named `oneos`. The GitHub owner is supplied
at setup time and must not be embedded in source files.

The application repository is the only repository connected to GitHub. Grey
Matter, its Git history, live registries, database files, entity names, and
personal or company content remain local and private.

Development uses this flow:

1. Create a bounded issue or task with acceptance criteria.
2. Work on a `codex/` branch in an isolated local worktree or Codex cloud
   environment.
3. Test against instance-neutral fixtures.
4. Push the branch and open a pull request.
5. Run deterministic CI checks and an independent review.
6. Run the private, local integration gate against Grey Matter.
7. Merge only after both public CI and private integration gates succeed.

Cloud agents never receive the live Grey Matter vault. Tests that need vault
shape use synthetic registries and documents containing no real entity values.

## History-safety gate

No existing Git history is uploaded until every reachable commit and current
tracked file has been checked for:

- instance-specific values prohibited by `AGENTS.md`;
- credentials, tokens, private paths, database files, and vault content;
- active deprecated product names outside the explicitly permitted archive;
- files that should never leave Grey Matter.

If the complete history is clean, preserve it and push it to the private remote.
If any prohibited material exists in reachable history, do not upload that
history. Create a clean repository baseline from the validated current tree and
retain the former history locally as a private archive. Do not push first and
attempt to clean it afterward.

## Agent autonomy model

One lead task owns requirements, dependencies, and the final result. Independent
agents may handle bounded exploration, tests, implementation, and review, but
write-heavy tasks that touch the same files do not run concurrently.

An implementation agent must:

1. read the applicable `AGENTS.md`, specification, and build gate;
2. add or update a failing test where behavior changes;
3. make the smallest compliant change;
4. run targeted and required verification;
5. inspect its diff for instance values and unrelated edits;
6. produce a reviewable branch or pull request.

A separate reviewer checks naming consistency, system boundaries, privacy,
scope isolation, Git reversibility, and test evidence. CI handles deterministic
checks. A background follow-up task may monitor CI and review feedback, but it
must stop for new dependencies, convention changes, security-boundary changes,
destructive actions, deployment, or unresolved product decisions.

The human approval surface is therefore limited to genuine product exceptions,
high-risk boundary changes, and final merge or publication decisions.

## Failure handling

- A naming scan finding is a hard failure unless it is inside the archived
  historical guide or explicitly describes a deprecated term in this contract.
- A history-scan uncertainty blocks the push and selects the clean-baseline
  route.
- A cloud test that requires private vault data is redesigned around a synthetic
  fixture; the private data is never uploaded to make the test convenient.
- Any mismatch among an actor identifier, policy rule, caller, or test blocks
  the naming migration commit.
- A failed local integration gate blocks merge even when public CI passes.

## Verification and completion criteria

The cutover is complete only when:

- active UI and documentation display OneOS as the sole product name;
- active filenames, tool identifiers, actor identifiers, and metadata no longer
  use the deprecated names;
- active references resolve after the specification and script renames;
- the legacy adoption guide remains archived and non-authoritative;
- repository tests and the instance-value/AGPL scan pass;
- vault unit tests, `policy_enforcer.py`, and `check_v2.py` pass;
- the canonical local path works through `~/code/oneos`;
- the private GitHub remote contains only history that passed the safety gate;
- a trial `codex/` branch can pass CI and the private local integration gate
  without uploading Grey Matter.

## Non-goals

- No application feature, UI redesign, schema, roadmap phase, or dependency is
  introduced.
- Grey Matter is not renamed; it is a component name, not the product name.
- Hermes and Command Center are not presented as standalone products.
- The repository is not made public during this cutover.
- Deployment is not part of the initial GitHub setup.
