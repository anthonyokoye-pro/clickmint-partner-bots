# Trust, Safety, Telegram Compliance & Enforcement

## Principles

CLICKMINT works with Telegram controls; it does not bypass bans, rate limits, moderation, permissions, or reporting systems. Growth never outranks user safety, Telegram compliance, system integrity, fraud prevention, and fair governance.

## Entity states

Users, channels, groups, and other registered entities can be represented as:

```text
ACTIVE → FLAGGED → RESTRICTED → SUSPENDED → BANNED/REMOVED
                         \→ ACTIVE (review/restore)
```

A report is evidence for review, not proof of wrongdoing. A single keyword, report, or suspicious signal must not create a permanent ban.

## Durable records

`enforcement.py` stores:

- entity state and reason;
- enforcement events with previous/new state, actor, timestamp, duration, notes;
- reports and review status;
- evidence references (message/post IDs, timestamps, internal records);
- system safe-mode state.

Do not store unnecessary private content. Store references and minimum information needed for security, moderation, fraud prevention, audit, and recovery.

## Capability effects

The enforcement service exposes capability checks for:

```text
tasks, distribution, partnerships, rewards, campaigns,
registration, automated_posting
```

`RESTRICTED`, `SUSPENDED`, `BANNED`, and `REMOVED` must fail closed for the appropriate capabilities. The application service, not the frontend, is authoritative.

## Emergency safe mode

Safe mode pauses risky activity without deleting history. It is intended to pause distribution, campaigns, registration, automated posting, and reward activity while preserving evidence and audit records.

## Telegram-specific boundaries

- Validate Mini App `initData` on the server; never trust browser-provided user identity.
- Verify channel/group membership and administrator permissions before posting.
- Direct posting requires the bot to have the necessary administrator permission, especially `can_post_messages`.
- Use Telegram-supported message entities/parse modes only; do not invent markup.
- Respect flood limits with queueing, retries, and backoff.
- Do not rotate accounts, evade enforcement, mass-report, impersonate, manipulate engagement, or retaliate against reporters.
- Keep advertising separate from announcements and task distribution. Consent, versioned terms, scoped Ads Manager roles, and safety review are required before advertising is enabled.

## Human review

Human review is required for serious enforcement, borderline content, appeals, permanent bans, and decisions based on incomplete or conflicting evidence. Automation may collect signals, preserve evidence, pause risky actions, and recommend review; it must not convert one weak signal into a permanent punishment.

## Current status

Implemented:

- durable enforcement state store;
- reports, evidence, event timeline;
- temporary enforcement expiry;
- safe-mode control;
- eligibility fail-closed integration and regression tests;
- **shared gate (`enforcement_gate.py`)** consulted by the reward bot (registration,
  forward/distribution, task claims, scheduled direct delivery), the partnership bot
  (registration, offers — both the sender and every target partner) and the admin bot.
  The gate only reads states a human recorded; it fails closed if the store is unreachable;
  the owner is exempt from per-entity state but **not** from safe mode;
- **appeals** (`compliance_appeals`): a restricted member files `/appeal <text>` in the
  reward bot. Filing changes nothing. Admins may mark an appeal UNDER_REVIEW / UPHELD /
  WITHDRAWN; only the **owner** may OVERTURN, which performs an audited `restore()`;
- in-bot 🚩 reports are mirrored into `compliance_reports` (still PENDING a human);
- Admin Mini App **Reports & appeals** panel and API (`/api/admin/safety`,
  `/api/admin/reports…`, `/api/admin/evidence`, `/api/admin/appeals/{id}/decide`);
- admin bot **Trust & safety** panel with an owner-only safe-mode toggle;
- broadcast workers (reward + partnership) claim nothing during safe mode and mark
  deliveries to enforced recipients `blocked` instead of messaging them;
- final appeal decisions (UPHELD / OVERTURNED / WITHDRAWN) are sent to the appellant
  through the durable outbox; interim states are silent.

Not yet implemented:

- consent-aware advertising;
- report entry points for *group* entities from inside Telegram groups.

## Safe mode semantics

When safe mode is on: member forwards, task claims, partnership offers and new
registrations are refused with a "paused" message; scheduled direct deliveries and
queued broadcasts are **retried later** rather than dropped; owner panels and the Admin Mini App keep working
so the incident can be handled.

## Human verification and risk

Human verification complements Telegram identity; it does not prove that a person is legitimate. The future provider boundary must validate tokens server-side, reject replay and expiry, bind a result to the intended action/session, and record only minimal operational metrics. Normal navigation should not be challenged. High-risk referrals, reward farming, rapid campaigns, and unusual registration may require a challenge. Provider failure must fail safely without silently granting sensitive access.
