# CLICKMINT Final Project Audit and Freeze Record

**Audit date:** 2026-09-12  
**Decision:** Freeze the current ClickMint project and shift primary development to a separate Partnership Bot repository.  
**Audit basis:** source inspection, existing tests/documentation, staging observations recorded during this session, and official Telegram documentation review.

## 1. Executive summary

ClickMint is a Python/aiogram Telegram network composed of a Reward Bot, a Partnership Bot, an Admin Bot, an Admin Mini App, an onboarding Mini App, shared verification services, governance/enforcement logic, internal Mint accounting, task/broadcast foundations, and background delivery/re-verification loops.

The strongest completed area is the **permission-first user-owned bot and destination verification path**. Dedicated staging resources successfully demonstrated bot identity, channel access, Telegram member count, administrator state, posting permission, onboarding through Telegram Web App, fail-closed behavior after permission removal, and recovery after permission restoration.

The project is **not production-ready**. The codebase is a migration-stage system: mature legacy JSON bot logic coexists with newer SQLite stores and service boundaries. The repository also has a dirty, non-reproducible local working tree. Task, broadcast, advertising, restart recovery, financial, and large-scale operational claims must remain bounded by their actual test evidence.

The right strategy is not to add every proposed feature now. Freeze this project, preserve the handoff, and develop the Partnership Bot separately with a smaller, clearer architecture.

## 2. Source-control and reproducibility finding

Observed at audit time:

- Local branch: `arena/01a0713e-clickmint-partner-bots`.
- Local checkout was based on `c7e4b8e` and contained many modified and untracked source/documentation files.
- The session branch on GitHub was advanced with the Admin Mini App syntax fix and the audit handoff documentation.
- `.env.staging` and other runtime secrets are intentionally untracked.
- `__pycache__` artifacts were present locally and are not project source.

**Risk:** a future agent cannot safely reconstruct the exact working implementation by pulling or resetting without first reconciling the dirty tree.

**Freeze action:** do not use `git clean -fd` or `git reset --hard` on the staging checkout. Preserve secrets and runtime data outside Git. When reopening, create a clean archival clone and explicitly select the authoritative commit/files.

## 3. System inventory

### Bots and frontends

- `reward_bot.py`: public reward/exchange network, Mint/task/referral flows, campaigns, destination setup, reports, admin-scope paths.
- `partnership_bot.py`: curated partner contracts, destination verification, forwarded-post submission, partner offers, accept/reject/report chain, review outcomes, scoped broadcast worker.
- `admin_bot.py`: owner/scoped-admin dashboard, Mint/referral review, reports, contracts, invite codes, safety operations.
- `admin_server.py` + `admin_http.py`: local HTTP service for Admin API, onboarding API, static Mini Apps, origin/rate handling.
- `admin_web/`: Admin Mini App dashboard and controls.
- `onboarding_web/`: user-owned bot credential connection Mini App.

### Domain and services

- Core exchange/governance: `core.py`, `governance.py`, `eligibility.py`.
- Destination/Telegram verification: `channel_registry.py`, `channel_connection.py`, `verification_store.py`, `telegram_verification.py`, `destination_state.py`.
- Accounting: `mint_ledger.py`, `currency.py`, `economics.py`, `user_directory.py`, `migrate_mint.py`.
- Tasks and broadcasts: `task_marketplace.py`, `broadcast_queue.py`, `services/task_service.py`, `services/broadcast_service.py`, `audience.py`.
- Delivery: `relay.py`, `delivery_worker.py`, `scheduler.py`.
- Trust/safety: `enforcement.py`, `enforcement_gate.py`, `human_verification.py`, `credibility.py`, `performance_snapshots.py`, `referral_ranking.py`.
- Campaigns/operations: `ad_campaigns.py`, `platform_store.py`, `db_backup.py`, `admin_idempotency.py`, `admin_deploy.py`.

## 4. Current architecture and data flow

```text
Telegram users/admins
       |
       v
aiogram bot handlers / Mini Apps
       |
       v
Application/domain logic
       |
       +--> Governance, eligibility, enforcement
       +--> Verification and destination state
       +--> Tasks, broadcasts, relay, scheduler
       +--> Mint/account/referral logic
       |
       v
JSON compatibility stores + multiple SQLite authorities
       |
       v
Telegram Bot API / temporary HTTPS tunnel / local workers
```

The architecture is intentionally moving toward service boundaries, but the current bots still construct and call many domain objects directly. There is no single unified application-service layer across all old and new paths.

### Storage split

- Legacy reward/partnership/session state: JSON files under `STORE_DIR`.
- Mint/user accounts: SQLite transactional ledger and user directory foundation.
- Credentials/destinations: shared verification SQLite.
- Platform audit/roles: shared platform SQLite.
- Tasks, broadcasts, enforcement, ads, Admin API, worker metrics: separate SQLite paths.

This split is workable for an experimental single-host system but creates cross-database transaction and consistency risks.

## 5. Requirement audit

### Completed and live-verified

- Three-bot staging separation was configured.
- Staging preflight validates dedicated resources, HTTPS origins, unique tokens, path containment, and disabled financial flags.
- Read-only Telegram Bot API smoke verification passed for a dedicated staging bot/channel.
- User-owned bot onboarding through a Telegram Web App passed.
- Bot identity and user-owned token storage path worked without displaying the token after success.
- Channel registration used Telegram-retrieved metadata/member count rather than trusting a manual count.
- Administrator and required posting permission checks passed.
- Removing posting permission caused fail-closed behavior.
- Restoring permission allowed successful re-verification.
- Admin Mini App opened/authenticated inside Telegram after a JavaScript startup syntax error was fixed.
- Shared verification state, permission checks, enforcement gate, reports, role scopes, safe-mode foundation, and audit/delivery foundations exist in source.

### Partially complete

- Reward Bot: broad reward/exchange foundation exists, but full live user lifecycle and scale behavior are unproven.
- Partnership Bot: contract, verification, submission, matching, offer, review, and delivery foundations exist; a separate clean production architecture is still needed.
- Task Marketplace: task creation/claim/completion foundations and tests exist, but full live Telegram flow and duplicate/recovery evidence were not completed.
- Broadcasts: queue/lifecycle/retry/admin controls exist, but real live delivery, scheduling, restart, and large audience behavior are unproven.
- Advertising: consent/review/campaign foundations exist; advertising is disabled and should remain so until separately approved.
- Admin Mini App: functional dashboard and APIs exist, but it is not a premium, complete visual product and frontend parity is incomplete.
- Referral system: code/link/qualification/ranking foundations exist; referral UX, attribution, anti-abuse, and end-to-end reward evidence need further work.
- Credibility/performance: score/band/performance foundations exist, but reliable Telegram historical analytics are not generally available through the Bot API.
- Human verification: module/foundation exists, but risk-based provider integration and operational policy are not complete.
- Documentation: extensive documents exist, but earlier status documents contain historical commit/status claims that must be reconciled in any future clean archive.

### Not completed or deliberately deferred

- Production deployment and operations.
- Full live task marketplace acceptance/completion/duplicate test.
- Full live broadcast and advertising lifecycle.
- Real worker crash/restart recovery against Telegram.
- Production monitoring, alerting, backups, and restore drill.
- Paid deposits, Boost, withdrawals, Stars/TON/crypto integration.
- Guaranteed channel analytics beyond official Bot API capabilities.
- Human-verification provider integration.
- Premium Admin Mini App redesign, loading animations, button feedback, and full editor.
- Autonomous AI engineering or multi-agent production control.
- Website, cross-platform integrations, group monetization, and large-scale infrastructure.

### Implemented incorrectly or risky

- The Admin Mini App had a JavaScript syntax error that prevented its startup script from running; fixed in the session branch, but frontend linting should be mandatory.
- Free ngrok warning/interstitial HTML interfered with API JSON requests in Telegram Web App testing; temporary tunnel selection must account for embedded Web App behavior.
- Linux `/srv` staging paths were unsuitable for Windows Git Bash.
- Runtime configuration was easily stale because already-running bots did not reload changed `.env.staging` values.
- The system has mixed JSON/SQLite authorities and therefore cannot claim fully atomic cross-domain operations.
- Some command/legacy paths coexist with button-first flows; UX is not yet consistently button-first.
- Partnership manual/chain delivery cannot guarantee that a partner actually publishes after accepting; direct posting requires explicit bot permissions.
- Mint/deposit/withdrawal concepts must not be activated as a financial product without separate legal, payment-provider, and accounting review.

## 6. Security and compliance assessment

### Positive controls

- Secrets are environment-based and staging preflight avoids printing them.
- Telegram Mini App identity is intended to be validated server-side.
- User-owned bot credentials are encrypted at rest through the credential store.
- Destination participation fails closed when verification/permissions are missing.
- Enforcement states and safe-mode foundations exist.
- Reports are intended for human review rather than automatic punishment.
- Broadcast scopes, worker admission, retries, and idempotency foundations exist.
- Telegram restrictions are treated as constraints; no account rotation, fake engagement, or permission bypass should be added.

### Gaps

- No independent security assessment or penetration test.
- No completed threat model for all cross-database/admin paths.
- No production secret manager or rotation/recovery procedure.
- No proven webhook secret deployment.
- No comprehensive replay/race/duplicate live test.
- No mature human-verification/risk provider integration.
- Audit immutability and evidence retention are foundations, not a compliance program.
- Financial/deposit/withdrawal concepts remain unsafe to activate.

### Compliance conclusion

No live evidence from this audit proves a policy violation, but automated distribution, broadcasts, referral incentives, and future advertising can become spam or manipulation risks if expanded without consent, rate limits, permissions, and human governance. Keep risky features paused and clearly distinguish distribution opportunity from guaranteed reach/engagement.

## 7. UI/UX assessment

The Admin Mini App is functional but plain and incomplete relative to the requested premium/polished direction.

Observed gaps to carry forward:

- weak visual hierarchy and branding;
- limited navigation model;
- form-heavy dashboard experience;
- incomplete empty/loading/error/success states;
- no properly integrated loading animation system;
- limited button/micro-interaction feedback;
- incomplete rich Telegram-oriented editor;
- command-heavy Telegram workflows remain;
- welcome/onboarding copy is functional but not a final product experience;
- mobile and accessibility review is incomplete;
- campaign/draft management requires consistent state-aware controls;
- frontend behavior must not block backend requests or conceal errors.

Future UI work should establish a small design system, reusable message/keyboard templates, state-aware action availability, and a lightweight mobile-first dashboard. Do not add heavy animation libraries before measuring need.

## 8. Recommended future roadmap

### Phase 0 — Freeze and reconciliation (now)

- Stop feature development.
- Preserve this audit and `docs/PARTNERSHIP_BOT_HANDOFF.md`.
- Stop local bots/tunnels/workers when no longer needed.
- Keep secrets/databases out of Git.
- Create a clean archival baseline before reopening.

### Phase 1 — Separate Partnership Bot pilot

- New repository and isolated staging resources.
- Port only necessary partnership behavior.
- Build router/service/repository boundaries.
- Implement durable contracts, offers, decisions, deliveries, reports, and notifications.
- Re-run live Telegram verification and permission tests.

### Phase 2 — Core reliability

- One authority per entity.
- Transaction/idempotency boundaries.
- Durable jobs/leases and recovery.
- Structured logs, metrics, backups, restore drills.
- Complete regression and concurrency tests.

### Phase 3 — Telegram UX and Mini App

- Button-first flows.
- Improved onboarding/welcome messages.
- State-aware keyboards and callback ownership checks.
- Premium but lightweight visual system.
- Rich editor based on Telegram `MessageEntity` data, not invented markup.

### Phase 4 — Trust and abuse controls

- Risk-based human verification only at justified points.
- Referral/reward abuse controls.
- Evidence/report/appeal completion.
- Safe-mode and emergency operational runbooks.

### Phase 5 — Reward/task/broadcast completion

- Live task lifecycle.
- Broadcast lifecycle and segmented audiences.
- Ad system only after explicit opt-in, policy, and reward economics review.
- No payment activation.

### Phase 6 — Production readiness

- Stable HTTPS/domain or supported hosting URL.
- Webhooks and secret token.
- Database backup/restore and monitoring.
- Secret rotation, least privilege, incident response.

### Deferred roadmap

- Deposits, Boost, withdrawals, Stars/TON/crypto.
- AI intelligence and engineering agents.
- Channel-growth analytics/attribution.
- Group distribution and cross-platform adapters.
- Website, large-scale workers, Redis/Postgres migration, and media/branding library.

## 9. Current versus recommended architecture

| Area | Current | Recommended future direction | Priority |
|---|---|---|---|
| Bot handlers | Large monolithic modules | aiogram routers and thin handlers | High |
| Business logic | Mixed direct handler/domain calls | application services and policies | High |
| Storage | JSON + many SQLite authorities | one clear authority per entity, relational migration plan | Critical |
| Tasks/offers | Foundations and in-process flows | durable marketplace claims, expiry, idempotency | High |
| Delivery | in-process loops | leased durable worker jobs | High |
| Verification | strong foundation | keep service boundary and expand edge tests | High |
| Admin | functional bot/Mini App | API parity, state-aware UI, mobile-first design | Medium |
| Analytics | partial proxies | only collect metrics Telegram legitimately exposes | Medium |
| AI | future only | optional proposal/analysis layer, never transaction dependency | Low now |
| Payments | disabled foundation | separate legal/payment subsystem later | Deferred |
| Infrastructure | single-host/free pilot | clear Postgres/worker/monitoring migration path | Medium |

## 10. Professional recommendation

If responsible for ClickMint at zero budget and 1,000–10,000 users/channels, I would build first:

1. A separate, focused Partnership Bot pilot.
2. One authoritative database model for the pilot.
3. Permission-first verified destinations.
4. Durable partnership contracts/offers/deliveries.
5. Simple, reliable notifications and human review.
6. Structured audit logs, backups, and tests.
7. A small Admin interface that exposes only proven operations.

I would deliberately avoid now:

- crypto deposits/withdrawals;
- guaranteed growth or views;
- complex AI agents;
- microservices/Kubernetes;
- MTProto analytics as a default dependency;
- broad advertising marketplace;
- cross-platform integrations;
- heavy gamification/media systems.

The current ClickMint foundation is worth preserving, but the safest next step is not continued feature accumulation. It is a separate Partnership Bot codebase with a smaller architecture, explicit state machines, and a real operational test plan.

## 11. Final freeze checklist

- Current ClickMint project: **frozen**.
- New feature implementation: **not authorized unless explicitly reopened**.
- Financial features: **disabled/deferred**.
- Advertising: **disabled/deferred**.
- Live Telegram claims: limited to the evidence listed above.
- Partnership Bot: separate repository/environment required.
- Secrets: never place in documentation or Git.
- Future implementation: begin only from the handoff and a clean source baseline.
