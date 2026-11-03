# Shared access through isolated operator instances

The architecture is **facility OIDC → authenticated reverse proxy → one APEXA
instance per Unix user and assigned proposal**. Each owns its MCP processes,
sessions, workflows, credentials, uploads and job state. Multiple users do not
share one Python process.

Examples are grouped in `deploy/`: `apexa-operator@.service`,
`operator.env.example`, `oauth2-proxy.cfg.example` and
`nginx-operator.conf.example`. Provision distinct OS accounts, loopback ports,
backend tokens and protected environment files. Use a read-only shared checkout
without a shared credential-bearing `.env`; `PYTHON_DOTENV_DISABLED=1` prevents
its loading. State lives in `/var/lib/apexa-USERNAME`, mode 0700.

The proxy obtains a verified email claim from oauth2-proxy and overwrites
caller-supplied `X-APEXA-Principal` and `Authorization`. APEXA checks that
principal against `APEXA_OPERATOR_ID` for HTTP and WebSockets. A valid campus
login with a different identity is refused. Configure the issuer, audience,
verified-email policy, client secret and cookie secret through site tooling.

Set `APEXA_PROPOSAL_ID` and `APEXA_DATA_ROOTS` to the assigned proposal. Enforce
actual ownership with filesystem ACLs and the remote scheduler identity.
Data-root checks constrain supported browser/inspection paths; they do not
sandbox every scientific library or embedded parameter-file path. The Unix
identity must not be able to read other users' private proposals. Add reviewed
systemd `ReadWritePaths` for assigned outputs. Keep motor writes and unrestricted
shell access disabled for general analysis instances.

Each instance's resource registry coordinates its own processes. Cross-user
allocation belongs to Slurm/cgroups. Keep SQLite on qualified local storage;
back up the entire state directory, including session `payloads/`, checkpoints
and run receipts.

Site acceptance requires two real SSO accounts: verify Bob cannot access
Alice's HTTP routes or WebSocket, direct access without the backend token fails,
logout/expiry behaves correctly, and filesystem/scheduler access to another
proposal is denied. The offline tests establish application identity checks,
not the facility's SSO, filesystem ACLs or cluster configuration.
