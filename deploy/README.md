# Local container runtime

This packaging runs OneOS behind Caddy at the single browser origin
`https://localhost:8443`. Only Caddy publishes a host port, and that port is
bound to IPv4 loopback. The application is reachable only on the private
Compose network. Requests that complete TLS for `localhost` but supply another
HTTP `Host` are rejected by Caddy with 421 before reaching the application.

Use `python -m tools.local_service` for owner setup and routine operations once
that command is installed. It reads the private `config.json` from the state
root without evaluating it as shell code and supplies these required Compose
inputs:

- `ONEOS_VAULT`: existing vault directory, mounted read-write at `/vault`;
- `ONEOS_STATE_DIR`: private deployment state root;
- `ONEOS_UID` and `ONEOS_GID`: numeric owner IDs used for the image and process;
- `ONEOS_GIT_NAME` and `ONEOS_GIT_EMAIL`: explicit local audit identity.

The setup command creates `auth/` and `status/` beneath the state root before
Compose starts. Authentication state is writable at `/state/auth`. The whole
status directory is mounted read-only at `/state/status`, allowing backup jobs
to atomically replace `backup-status.json` while the running application sees
the new inode. Never put the vault, state root, `config.json`, credentials, or
private paths in the repository or build context.

The application filesystem is read-only except for the vault, authentication
state, and in-memory `/tmp` and `/run`. Caddy persists its local CA and TLS
material under `${ONEOS_STATE_DIR}/caddy/{data,config}` so stopped-service
backups can capture the CA and configuration state. Both images run with the selected owner UID/GID;
the application image also creates a matching passwd entry for Git and Python
libraries that resolve the current user. Both services drop Linux capabilities, prohibit
privilege escalation, rotate local logs, and use `unless-stopped`, which
restarts crashes without undoing an explicit stop. No Docker socket is mounted.
Caddy's inherited low-port file capability is removed in the derived image:
port 8443 does not need it, and retaining it would make Linux refuse execution
when all container capabilities are dropped with `no-new-privileges` enabled.
The public Caddyfile is baked into that image rather than bind-mounted as a
single host file. This makes a Caddyfile edit change the image build and avoids
stale or detached VirtioFS file mounts after atomic source-file replacement.

The image installs production dependencies exactly from `uv.lock`, carries Git
for audited vault transactions, and keeps the environment's Python first on
`PATH` so vault policy hooks use the same locked Python dependencies. Hooks are
not bypassed or disabled. The Python 3.12 slim-trixie base supplies Git 2.47 or
newer because the transaction boundary deliberately requests 64-character
object abbreviations for SHA-256 compatibility; Bookworm's Git 2.39 rejects
that valid runtime contract before commit. The server runs without source
reload.

## Local certificate trust

Caddy issues the `localhost` certificate from its persistent internal CA. The
owner setup flow must install that CA root into the macOS trust store before
the browser is used. Its host path is
`${ONEOS_STATE_DIR}/caddy/data/caddy/pki/authorities/local/root.crt`; do not
publish or commit it. Losing the Caddy data directory destroys the local CA
and requires trusting the replacement root again. Setup creates both Caddy
state directories with mode `0700` and ownership matching
`ONEOS_UID:ONEOS_GID`.

## Direct Compose validation

For synthetic diagnostics, export only non-secret temporary paths and values,
then run:

```sh
docker compose config --quiet
docker compose build --pull app caddy
docker compose up --wait
curl --fail --cacert /path/to/copied/root.crt https://localhost:8443/healthz
docker compose stop
```

`docker compose config` must fail when a required input is absent. Startup
readiness is determined by the app's `/healthz` process probe and Caddy's HTTPS
probe. Owner-facing vault and backup diagnostics belong to authenticated
`/readyz`; they are not exposed by the healthcheck.

The image tags are deliberately exact instead of floating tags. Update them in
a reviewed change, rebuild with `--pull`, and rerun the synthetic runtime tests.
The Python dependency resolution remains governed by the committed `uv.lock`.
Caddyfile changes likewise require rebuilding and recreating the Caddy service;
the owner operation wrapper performs that lifecycle rather than reloading a
potentially stale container.
