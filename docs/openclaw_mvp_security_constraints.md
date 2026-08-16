# OpenClaw MVP Security Constraints

## 1. Security Goal

In the MVP, OpenClaw is a constrained orchestrator.
It is not a host execution shell.

The system exposes capabilities through an explicit MCP tool allowlist.
Anything outside that allowlist is denied by default.

## 2. Default-Deny Surface

The MVP default is to reject:

- host execution
- native OpenClaw plugins
- undisclosed scripts or system commands
- implicit access to sensitive files
- full-permission or YOLO-style execution modes

## 3. Allowed Capability Surface

The allowed MCP tools are:

- `refresh_news`
- `query_news_research`
- `recommend_stock`
- `run_watchlist_triage`
- `get_company_profile`
- `retrieve_supporting_articles`

Additional constraints:

- only `refresh_news` may mutate research data
- the default research path stays on `financial-agent`
- `QuantGPT` only enters the capability surface for explicit quantitative validation

## 4. Credentials and Logging

Secrets should remain file- or environment-backed and must not be echoed in tool results.

The MVP requires:

- no inline token or password disclosure in responses
- log redaction where sensitive config might appear
- no reflection of private configuration in visible MCP output

## 5. Operational Guards

The MVP runtime keeps a small number of hard guards:

- explicit tool registration
- daily rate limit for `refresh_news`
- predictable model-selection policy
- route-policy helpers that separate default research from optional quant work

## 6. Future Hardening

Later phases may add:

- finer-grained permission classes
- per-role and per-channel isolation
- approval gates
- external sender allowlists
- audit trails for outbound actions

Those capabilities must harden the system without weakening the MVP default-deny posture.
