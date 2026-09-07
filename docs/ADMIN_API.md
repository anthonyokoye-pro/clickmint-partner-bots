# Admin Mini App API Contract

The Admin Mini App API is an authenticated, server-side boundary. Every request must include validated Telegram Mini App `initData` through the configured transport header:

```http
X-Telegram-Init-Data: <initData>
```

Mutation requests additionally require:

```http
Content-Type: application/json
X-Idempotency-Key: <unique-key>
```

## Authentication and errors

| Status | Meaning |
|---|---|
| 200 | Request completed |
| 400 | Invalid request, missing idempotency key, or invalid operation |
| 403 | Invalid Telegram identity or insufficient admin access |
| 404 | Unknown endpoint |
| 413 | Request body exceeds the configured limit |
| 415 | Mutation is not JSON |
| 429 | Rate limit exceeded |
| 500 | Unexpected server error |

Every response includes `X-Correlation-ID`.

## Read endpoints

### Dashboard

```http
GET /api/admin/dashboard?task_limit=20&task_category=Guides&broadcast_status=paused
```

Returns:

```json
{
  "user": {"id": 123, "username": "owner"},
  "channels": [],
  "tasks": [],
  "broadcasts": []
}
```

`task_limit` is capped at 100. Broadcast status may be `draft`, `queued`, `running`, `paused`, `cancelled`, or `completed`.

### Audit events

```http
GET /api/admin/audit?object_type=channel&limit=50
```

Returns:

```json
{"events": [{"audit_id": "...", "action": "...", "object_type": "channel", "object_id": "...", "reason": "...", "created_at": 0}]}
```

### Security and audit events

Security events are available through the same audit endpoint:

```http
GET /api/admin/audit?object_type=admin_security&limit=100
```

Recorded actions include:

```text
ADMIN_AUTH_REJECTED
ADMIN_ORIGIN_REJECTED
ADMIN_RATE_LIMITED
ADMIN_IDEMPOTENCY_REJECTED
```

Every event uses the response correlation ID as its object ID.

### Performance history

```http
GET /api/admin/performance/{destination_id}?limit=20
```

Returns:

```json
{
  "destination_id": "-100123",
  "current": {},
  "history": [],
  "interventions": []
}
```

### Transport metrics

```http
GET /api/admin/transport/metrics
```

Returns request count, response status counts, and path counts. This endpoint is still authenticated and is not public telemetry.

## Mutation endpoints

All mutations require a unique `X-Idempotency-Key`. Replaying the same key for the same authenticated endpoint returns the original result without executing the operation again.

### Approve referral ranking

```http
POST /api/admin/referrals/2026-09/approve
{"reason": "monthly review complete"}
```

### Manage broadcast

```http
POST /api/admin/broadcasts/{campaign_id}/pause
POST /api/admin/broadcasts/{campaign_id}/resume
POST /api/admin/broadcasts/{campaign_id}/cancel
{"reason": "safety review"}
```

### Clear performance cooldown

```http
POST /api/admin/performance/{destination_id}/clear
{"reason": "manual review completed"}
```

## Deliberately unavailable operations

This API does not expose endpoints for:

- Revenue accounting.
- Deposits.
- Withdrawals.
- External payouts.
- Mint balance transfers.
- Boost purchases.

Those boundaries remain disabled until their separate economic, payment, compliance, and reconciliation phases are completed.
