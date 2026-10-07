# Using PactMesh from your own AI agent

PactMesh is infrastructure: your agent should not need to know about envelopes, escrow or Merkle trees.
There are three integration levels.

## 1. MCP (any MCP-capable agent, no code)

The Model Context Protocol is an open standard. `python -m pactmesh mcp` is a stdio MCP server (stdlib
only) that exposes a running buyer as tools:

| Tool | What it does |
|---|---|
| `pactmesh_create_task` | hire a service with a private budget (enforced locally, never sent) |
| `pactmesh_get_task` | proposals (supplier text labelled untrusted), model / policy / executed decisions, receipt |
| `pactmesh_list_tasks` | tasks and committed spend |
| `pactmesh_cancel_task` | cancel before funding |
| `pactmesh_verify_evidence` | verify the receipt's signatures, Merkle inclusion, anchor and escrow |
| `pactmesh_emergency_stop` | pause or resume new contracts |

The agent gets **no keys**. Everything it asks for still passes the buyer's deterministic policy, so an
agent that is tricked by a supplier still cannot overspend.

Start a buyer (`python -m pactmesh demo --keep` or `python -m pactmesh buyer`), then register the server in
your MCP client. A generic configuration (use absolute paths):

```json
{
  "mcpServers": {
    "pactmesh": {
      "command": "python",
      "args": ["-m", "pactmesh", "mcp", "--api", "http://127.0.0.1:8700",
               "--token-file", "/ABSOLUTE/PATH/PactMesh/.pactmesh-demo/buyer/admin_token"],
      "cwd": "/ABSOLUTE/PATH/PactMesh"
    }
  }
}
```

The token file is created by the buyer: `.pactmesh/buyer/admin_token` by default, or
`.pactmesh-demo/buyer/admin_token` with the demo. Tested: `tests/test_mcp.py` drives a complete contract over
stdio, including a prompt-injected quote that the policy blocks.

## 2. Local HTTP API (any language)

`POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `GET /negotiations/{id}/evidence`,
`POST /verify`, `POST /policy/pause`, `GET /metrics`, `GET /health`. Mutations need
`Authorization: Bearer <token>` and `Idempotency-Key`. The API listens on 127.0.0.1 by default.

## 3. Python runtime (build your own buyer or supplier)

`pactmesh.buyer.Buyer` and `pactmesh.supplier.Supplier` take any transport (`DirectTransport`,
`MixnetTransport`) and any ledger client (`SimLedgerClient`, `SolanaEscrowClient`). Both transports pass the
same conformance suite (`tests/test_transport_conformance.py`); a new transport must pass it as well.
