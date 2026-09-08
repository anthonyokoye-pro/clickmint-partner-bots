# CLICKMINT Architecture Audit — 2026-09-08

## Scope

This audit covers the three polling bots, shared domain modules, JSON and SQLite stores, Admin Mini App/API, scheduler, deployment scripts, tests, simulations, and documentation. It follows the completed non-financial Telegram smoke test and the compliance/enforcement directive.

## Current architecture

```text
Telegram Bot API / Telegram Mini App
        |
        +-- reward_bot.py --------+
        +-- partnership_bot.py ---+--> application handlers
        +-- admin_bot.py ----------+
        +-- admin_web/app.js ------> admin_http.py -> admin_api.py
                                      |
                                      +--> task_marketplace SQLite
                                      +--> broadcast_queue SQLite
                                      +--> credibility SQLite
                                      +--> Mint ledger SQLite
                                      +--> JSON legacy stores

Pure rules: core.py, governance.py, eligibility.py, economics.py, credibility.py
Persistence: store.py (legacy JSON), SQLite repositories
Optional observation: views_provider.py / future Telethon observer
```

## What is good

- Pure rules are largely separated from Telegram handlers and have offline tests.
- Reward and Partnership purposes remain separate while queue infrastructure is scoped.
- Forward-only content, human review for borderline submissions, least-privilege roles, idempotency, audit records, and financial feature disablement are explicit.
- Mini App authentication is server-side and the live HTTPS/Telegram smoke test passed.

## High-priority findings

1. **Enforcement was fragmented.** Existing `ACTIVE/WATCH/RESTRICTED/REMOVED` channel fields were not a system-wide user/channel/group incident model. There was no durable report/evidence/timeline/safe-mode store.
2. **Admin Mini App is incomplete for campaign lifecycle.** Broadcasts have create/queue/pause/resume/cancel but no title, edit, delete, or explicit draft lifecycle. Ad campaign parity and consent workflows are not implemented.
3. **Runtime boundaries are duplicated.** Legacy JSON state and newer SQLite stores coexist. A migration boundary is documented but not yet authoritative; the Admin runtime currently uses a compatibility facade for the Mint ledger.
4. **Cross-platform locking is incomplete.** `JsonStore` uses `fcntl` on POSIX and degrades to no lock on Windows. This is acceptable for a prototype but unsafe for concurrent Windows bot processes and must be replaced with a Windows-capable lock or a single writer.
5. **Telegram layer remains dependency- and environment-sensitive.** Offline bot wiring cannot run where aiogram is not installed. Live Bot API permission tests remain external.
6. **Scheduler and queue are separate mechanisms.** Future campaign claims are protected in SQLite, but the production worker/retry/rate-limit integration is not one unified service.

## Safety/compliance findings

- Content gating is useful but is not proof of Telegram compliance; human review and evidence are required.
- No mechanism may auto-ban based on a keyword or a report count alone.
- Advertising consent, versioned terms, scoped Ads Manager roles, and campaign safety/reporting remain unimplemented and must stay separate from announcements and task distribution.
- Direct posting must remain conditional on Telegram administrator permissions, especially `can_post_messages`.
- Telegram Mini App initData must continue to be verified server-side; browser identity is never trusted.
- Quick Tunnels are suitable for testing only, not production.

## Changes made in this audit pass

- Added `enforcement.py`: durable entity states, reports, evidence, enforcement timeline, temporary expiry, capability checks, and platform safe mode.
- Extended task eligibility with enforcement state and safe-mode fail-closed checks.
- Added `test_enforcement.py` covering lifecycle, reports, evidence, bans, restore, safe mode, and task blocking.

## Next architecture work

1. Put enforcement checks at the shared application-service boundary, not only in UI handlers.
2. Add admin API/UI for reports, evidence, timelines, enforcement actions, appeals, and emergency controls.
3. Unify campaign state transitions and add named draft lifecycle for announcements and future ads.
4. Add a cross-platform store lock or migrate mutable legacy state behind one process.
5. Add dependency-aware CI and real aiogram wiring tests in an environment installing `requirements.txt`.
6. Keep financial systems disabled until marketplace, consent, safety, and operational recovery are validated.
