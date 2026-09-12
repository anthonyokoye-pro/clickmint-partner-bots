# Advertising — consent-aware, separate, off by default

`ad_campaigns.py` implements the model the compliance directive asked for: advertising is
**not** a broadcast and **not** a task. It has its own store (`ads.sqlite3`), its own role,
its own review step, and its own consent record. No payments/billing exist; financial
features stay disabled.

## Preconditions (all enforced in code, all tested)

| Precondition | Where it is enforced |
|---|---|
| **Kill switch** `ADS_ENABLED=false` (default) | `AdCampaignStore.queue/resume/claim` refuse; `go_live_check` fails if enabled without terms |
| **Versioned terms** published by the owner | `publish_ad_terms` (owner only). Publishing a new version invalidates *every* existing consent and stale approvals |
| **Explicit consent per destination** | `/ads on @channel` by the destination's registered owner, under the *current* terms version. `/ads off` revokes immediately and cancels pending deliveries. Re-checked at claim time |
| **Scoped Ads Manager role** | `RoleRegistry` scope `"ads"` (invite from admin bot → *Invite code — Ads Manager*, redeemed with `/adminlogin` in the reward bot). Network admins cannot draft ads; Ads Managers cannot moderate the network |
| **Human safety review** | `draft → in_review → approved/rejected`, owner only, reason mandatory both ways. Editing a draft resets review |
| **Enforcement + safe mode** | Delivery worker consults `EnforcementGate` for the destination *and* its owner; safe mode pauses the worker |
| **Disclosure** | Every delivered message is prefixed `📢 Ad · <advertiser>` and suffixed with a *Sponsored content* note carrying the terms version |
| **Posting rights** | Delivered through the **destination owner's own bot** after a fresh permission verification — never the shared bot |

## Lifecycle

```
publish_terms(v)            owner
  └─ /ads on @chan          destination owner  (consent ↔ terms v)
create_campaign             ads manager        (draft, bound to terms v)
  └─ submit                 ads manager        (in_review)
      └─ approve/reject     OWNER, with reason (approved | rejected → editable again)
          └─ queue          ads manager        (fan-out ONLY to consenting destinations; ADS_ENABLED required)
              └─ worker     reward bot         (claim → re-check consent → gate → verify → post → complete)
pause / resume / cancel     ads manager        (cancel also cancels pending deliveries)
```

## Operator commands

- Reward bot: `/ads` (show terms + consent status of your destinations), `/ads on @x`, `/ads off @x`.
- Admin Mini App: *Advertising* panel — publish terms (owner), create/submit/queue/pause/resume/cancel (Ads Manager), approve/reject (owner).
- API: `GET /api/admin/ads`, `POST /api/admin/ads/terms`, `POST /api/admin/ads`, `POST /api/admin/ads/{id}/edit`,
  `POST /api/admin/ads/{id}/{submit|approve|reject|queue|pause|resume|cancel}`.

## What is deliberately absent

- Any payment, pricing, escrow, or payout.
- Any consent default of "on", any implied consent, any consent surviving a terms change.
- Any way to post an ad to a destination that has not opted in — including the owner's own.
