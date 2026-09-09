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

### Broadcast formatting (decision 2026-09-09 #3)

Formatting is never typed as markup. Two ways to create a Reward broadcast draft:

| Composed in | How | Payload |
|---|---|---|
| **Telegram** | Owner sends the formatted message to the admin bot | `text` + `entities[]` exactly as Telegram parsed them (unsupported entity types dropped, ranges validated in UTF-16 units, `text_link` limited to http(s)/tg URLs) |
| Mini App | Plain-text box, `POST /api/admin/broadcasts` | `text`, `entities: []` |

Delivery calls `send_message(text, entities=…)` with **no `parse_mode`**, so a typed `<b>` is
delivered as literal text. Editing a Telegram-composed draft in the Mini App drops its
formatting on purpose (the editor is plain text). Each dashboard broadcast row carries
`composed_in`, `entity_count` and an escaped `preview_html` for display only. Ads are excluded
from this feature by decision.

### Clear performance cooldown

```http
POST /api/admin/performance/{destination_id}/clear
{"reason": "manual review completed"}
```

### Trust & safety (reports, evidence, appeals)

```http
GET  /api/admin/safety                       # safe-mode status, open reports, open appeals
GET  /api/admin/reports?status=PENDING&limit=50
GET  /api/admin/reports/{report_id}          # report + evidence + current entity state
GET  /api/admin/appeals?status=OPEN
GET  /api/admin/enforcement/{entity_type}/{entity_id}

POST /api/admin/reports
{"entity_id": "@chan", "entity_type": "channel", "reason": "repeated scam links"}

POST /api/admin/reports/{report_id}/review
{"status": "UNDER_REVIEW|DISMISSED|WARNED|RESOLVED", "notes": "…"}   # never changes state

POST /api/admin/evidence
{"entity_id": "@chan", "entity_type": "channel", "evidence_type": "link|message|screenshot|log|note",
 "reference": "https://t.me/chan/12", "report_id": "rpt_…"}

POST /api/admin/enforcement/{entity_type}/{entity_id}/{flagged|restricted|suspended|banned|removed|restore}
{"reason": "…", "duration_seconds": 3600, "related_report_id": "rpt_…"}   # OWNER only

POST /api/admin/appeals/{appeal_id}/decide
{"decision": "UNDER_REVIEW|UPHELD|WITHDRAWN|OVERTURNED", "notes": "…"}   # OVERTURNED is OWNER only

POST /api/admin/safety/emergency
{"enabled": true, "reason": "…"}                                        # OWNER only
```

Linking `related_report_id` to an enforcement action closes that report with the
matching outcome. `duration_seconds` must be 1 s – 365 days.

### Advertising (consent-aware; see docs/ADVERTISING.md)

```http
GET  /api/admin/ads                                   # any admin (read-only)
POST /api/admin/ads/terms        {"text": "…"}        # OWNER — invalidates all consents
POST /api/admin/ads              {"title","advertiser_label","category","text","scheduled_at"?}   # Ads Manager scope
POST /api/admin/ads/{id}/edit    {"title"?,"text"?,"category"?,"scheduled_at"?}                    # draft/rejected only
POST /api/admin/ads/{id}/submit
POST /api/admin/ads/{id}/approve {"reason": "…"}      # OWNER, reason required
POST /api/admin/ads/{id}/reject  {"reason": "…"}      # OWNER, reason required
POST /api/admin/ads/{id}/queue                        # approved only; ADS_ENABLED; ≥1 consenting destination
POST /api/admin/ads/{id}/pause | resume | cancel
```

## Deliberately unavailable operations

This API does not expose endpoints for:

- Revenue accounting.
- Deposits.
- Withdrawals.
- External payouts.
- Mint balance transfers.
- Boost purchases.
- Ad billing or pricing of any kind.

Those boundaries remain disabled until their separate economic, payment, compliance, and reconciliation phases are completed.
