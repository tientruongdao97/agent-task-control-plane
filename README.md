# Agent Task Control Plane

A Python control plane that lets coding agents such as Antigravity pull issues over MCP, claim
them with expiring leases, report progress and test evidence, propose follow-up issues, and submit
results for human review.

## Scope

The application manages tasks; it does not remotely control an agent conversation. Agents initiate
all MCP calls. Test reports are stored as untrusted evidence and should be verified independently by
CI before a task is marked done.

## Quick start

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# Replace every example key. ACP_AGENT_API_KEY must match the corresponding
# agent entry inside ACP_AGENT_API_KEYS.
uv sync --all-extras
uv run agent-control-api
```

In another terminal:

```bash
uv run agent-control-mcp
```

The API listens on `127.0.0.1:8000`. The task dashboard is at `/admin`; enter the administrator
API key when prompted. OpenAPI is available at `/docs` in development. The MCP server uses stdio
by default, which is appropriate for a locally configured Antigravity client.

### Streamable HTTP MCP

For a remote MCP client, run:

```bash
ACP_MCP_TRANSPORT=streamable-http uv run agent-control-mcp
# or: uv run agent-control-mcp-http
```

The default endpoint is `http://127.0.0.1:8001/mcp`. Configure `ACP_MCP_HOST` and `ACP_MCP_PORT`
when needed. The current static agent identity is suitable for one trusted agent per MCP process.
Before exposing it to a network, put it behind TLS and an authenticated reverse proxy. Multi-agent
remote deployment should replace static credentials with OAuth/OIDC and derive agent identity from
the authenticated request rather than process environment.

`ACP_AGENT_ID` is this project's internal agent identity, not an OAuth client ID. The MVP does not
register OAuth clients or use an `OAUTH_CLIENT_ID`; that belongs to the production OAuth/OIDC
upgrade described above.

## Antigravity MCP configuration

Add this project as a local MCP server. Use an absolute path and pass secrets through the process
environment or a secret manager; do not commit them.

```json
{
  "mcpServers": {
    "agent-tasks": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/agent_management",
        "run",
        "agent-control-mcp"
      ],
      "env": {
        "ACP_AGENT_ID": "antigravity",
        "ACP_AGENT_API_KEY": "secret-from-your-secret-manager"
      }
    }
  }
}
```

Suggested agent instruction:

> List available tasks, claim exactly one, and keep its lease alive. Work only within the task's
> repository and acceptance criteria. Report progress and test results. Propose unrelated findings
> as issues instead of expanding scope. Submit the result for review when finished.

## Administrator API

All `/v1` endpoints require `X-API-Key`. Important routes:

- `POST /v1/tasks` creates an issue.
- `GET /v1/tasks` lists tasks.
- `GET /v1/tasks/{task_id}` returns task details and its event timeline.
- `POST /v1/tasks/{task_id}/review` accepts or rejects a submitted result.
- `GET /health` is intentionally unauthenticated and returns no sensitive data.
- `GET /admin` serves the administrator dashboard.

## State model

```text
OPEN -> CLAIMED -> IN_PROGRESS -> SUBMITTED -> DONE
  ^         |            |            |
  +---------+------------+------------+-> OPEN (release/reject)
                         +-> BLOCKED
```

A claim returns an opaque lease token once. The server stores only its SHA-256 hash. Expired leases
are reclaimed lazily when agents list or claim work.

## Production checklist

- Use PostgreSQL and `ACP_DATABASE_URL=postgresql+psycopg://...`.
- Put the HTTP API and remote MCP transport behind TLS and an identity-aware proxy.
- Replace static keys with OAuth 2.1/OIDC for multi-user deployments.
- Disable docs, restrict CORS at the edge, rate-limit authentication failures, and rotate secrets.
- Run submitted commits in isolated CI; never execute agent-provided commands in this service.
- Add migrations, encrypted artifact storage, retention rules, and immutable audit export.

## Development

```bash
make format
make check
```
