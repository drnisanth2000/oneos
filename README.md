# OneOS

A read-and-approve surface over a private, Git-backed Markdown vault. Incoming
information becomes a redacted inbox receipt; deterministic rules propose where
it belongs; nothing curated changes until the owner approves it, and every
approval is one revertible Git commit.

FastAPI, Jinja, HTMX and Alpine.js. No build step and no JavaScript bundler.

## Documentation

| Read | For |
|---|---|
| [`AGENTS.md`](AGENTS.md) | rules, invariants, stack (also `CLAUDE.md`) |
| [`PRODUCT-THESIS.md`](PRODUCT-THESIS.md) | why OneOS exists and what it is not |
| [`BUILD.md`](BUILD.md) | build loop, self-checks, hard stops |
| [`docs/STATUS.md`](docs/STATUS.md) | current build state, gates, decisions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | code map, routes, configuration |
| [`docs/LOCAL-OPERATIONS.md`](docs/LOCAL-OPERATIONS.md) | running the local deployment |
| [`deploy/README.md`](deploy/README.md) | container runtime |
| [`CHANGELOG.md`](CHANGELOG.md) | merged milestones |

## Development

Requires Python 3.12 and `uv`. Tests use synthetic fixtures and need no vault:

```sh
uv sync
uv run python -m pytest -q
```

To run against a vault on loopback for development, with sign-in disabled:

```sh
ONEOS_VAULT=<vault root> ONEOS_AUTH_MODE=disabled uv run uvicorn app.main:app
```

With `ONEOS_DEPLOYMENT=local`, `disabled` is not honoured: every request except
`/healthz` gets a 503 instead of being served without sign-in. The owner
deployment always requires sign-in; see `docs/LOCAL-OPERATIONS.md`.

## ⚖️ Licensing

This repository uses a dual/source-available licensing model to support individual creators while ensuring commercial entities contribute fairly:

*   **For Individuals:** Free to use, modify, and distribute. **Attribution (giving credit) is required.**
*   **For Companies:** Commercial use requires financial contribution. If your company uses this software, you must either enter a **revenue-share agreement** or **pay a licensing fee**.

### Commercial Licensing & Terms
If you are using this project inside a commercial product or company, please reach out to obtain a commercial license:
📧 **Contact:** info@nambisons.com
