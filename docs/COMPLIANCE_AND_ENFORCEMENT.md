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

Implemented in this audit pass:

- durable enforcement state store;
- reports, evidence, event timeline;
- temporary enforcement expiry;
- safe-mode control;
- eligibility fail-closed integration and regression tests.

Not yet implemented:

- Admin Mini App enforcement panels;
- bot report submission integration for every entity type;
- appeals UI/workflow;
- full safe-mode wiring into all workers;
- consent-aware advertising.
