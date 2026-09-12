# CLICKMINT Partnership Bot — Separate Project Handoff

**Status:** research/documentation handoff; current ClickMint project is frozen.  
**Source repository:** `anthonyokoye-pro/clickmint-partner-bots`  
**Source branch:** `arena/01a0713e-clickmint-partner-bots`  
**Date:** 2026-09-12

This document describes the Partnership Bot that exists in the ClickMint repository and the recommended way to rebuild it as a separate project. It is not a claim that every described product goal is complete. Sections labelled **Existing**, **Partial**, **Future**, or **Unknown** are intentionally separated.

## 1. Product definition

The Partnership Bot is the curated partner-network bot. It is distinct from the public Reward Bot:

- **Reward Bot:** broader exchange/reward network and internal Mint/task capabilities.
- **Partnership Bot:** curated partner relationships, partner contracts, reciprocal post offers, review gates, owner mediation, and partner-scope communication.
- **Admin Bot/Mini App:** owner and scoped-admin review, role, audit, campaign, enforcement, and system controls.

The Partnership Bot is intended for legitimate, consent-based cross-promotion between Telegram channels/groups. It must not be used for unsolicited bulk messaging, fake engagement, unauthorized posting, or permission bypasses.

## 2. Existing implementation inventory

### Main module

`partnership_bot.py` contains the current aiogram dispatcher, handlers, orchestration, partnership delivery loop, destination verification, contract flow, review flow, and audit presentation.

### Shared domain modules

| Module | Existing role in the Partnership Bot |
|---|---|
| `core.py` | `Contract`, `CreditLedger`, `DeliveryLog`, `ReportRegistry`, `PerformanceEngine`, post types, forwarding helpers |
| `governance.py` | `SubmissionGate`, `ReviewQueue`, `RoleRegistry`, terms, categories, post caps |
| `store.py` | JSON persistence for the Partnership Bot's compatibility/state store |
| `verification_store.py` | Shared SQLite credentials and destination records |
| `telegram_verification.py` | User-owned bot connection, Bot API verification, member count and permissions |
| `channel_registry.py` | Destination ownership/registration abstraction |
| `destination_state.py` | State machine for verifying, active, degraded, disconnected, removed destinations |
| `enforcement.py` / `enforcement_gate.py` | Shared safety state, capability blocking, reports, safe mode |
| `relay.py` | Legal delivery-route checks and auto-post capability checks |
| `platform_store.py` | Shared delivery audit and role storage (`PLATFORM_DB_PATH`) |
| `broadcast_queue.py` | Shared queue foundation, scoped using `bot_scope="partnership"` |
| `delivery_worker.py` | Rate admission and delivery metrics |
| `onboarding_api.py`, `onboarding_web/` | HTTPS Telegram Mini App for user-owned bot token onboarding |
| `config.py` | Tokens, owner, paths, flags, origin, worker and verification configuration |
| `ui.py` | Shared role/main menu keyboards |

### Tests and documents

Relevant tests include `test_bots.py`, `test_governance.py`, `test_core.py`, `test_telegram_verification.py`, `test_destination_state.py`, `test_relay.py`, `test_delivery_worker.py`, `test_broadcast_lifecycle.py`, `test_enforcement.py`, `test_platform_store.py`, and `test_webapp_auth.py`.

Relevant documents include:

- `docs/MASTER_SYSTEM_DOCUMENTATION.md`
- `docs/MASTER_PLAN.md`
- `docs/PARTNERSHIP_RESEARCH.md`
- `docs/RELAY_ARCHITECTURE.md`
- `docs/TELEGRAM_USER_BOT_VERIFICATION.md`
- `docs/COMPLIANCE_AND_ENFORCEMENT.md`
- `docs/DELIVERY_OPERATIONS.md`
- `docs/ADMIN_API.md`
- `docs/PROJECT_AUDIT_HANDOFF_2026-09-12.md`

## 3. Existing bot entry points and workflows

### `/start`

The current Partnership Bot start flow presents a role/menu experience and supports destination setup. Legacy forms may accept old arguments, but current registration is intended to retrieve authoritative Telegram information rather than trust a manually supplied member count.

### `/connectbot`

The user connects a BotFather-created bot. The preferred path is the HTTPS onboarding Mini App. The fallback accepts a token in chat and warns the user to rotate it; this fallback is weaker and should not be the normal separate-project UX.

The connection is stored encrypted through the shared verification store. The token is used to call Telegram `getMe` and later to operate on the user's destination.

### `/botstatus`

Shows the connected user-owned bot identity without revealing the token.

### `/disconnectbot`

Disconnects the user-owned credential and transitions owned destinations away from usable participation. A bot change also requires destinations to be re-verified.

### `/mychannels`

Displays the user's registered channels/groups and inline controls. Current controls include re-verification, statistics, auto-post choice, and removal. The destination rows are scoped to the calling Telegram user.

### `/verification <destination>` and `/scan`

Shows or refreshes Telegram verification checks. Checks include destination resolution, bot membership, administrator state, required permissions, accessibility, member count, and eligibility. Failures are intended to explain corrective action.

### `/stats`

Aliases the scan/statistics behavior. The Bot API can reliably provide chat metadata/member count subject to Telegram permissions and chat type; it cannot provide every analytics metric a channel owner might expect.

### `/contract`

Button-led two-stage contract setup:

1. Select post types the partner will accept/publish.
2. Select post types the partner will receive.
3. Save the contract.

The current post types originate in `core.POST_TYPES` (`Airdrops`, `Testnets`, `AI Tools`, `AI x Web3`, `Scam Alerts`, `Guides`, `Deadlines`, `DeFi`, `TON / Web3`, `General`).

### Submission flow

1. User accepts posting terms.
2. User selects a niche category.
3. User forwards a Telegram post to the bot.
4. `SubmissionGate` checks terms, category, hard-block patterns, and human-review patterns.
5. `EnforcementGate` checks the user and destination.
6. Verification and daily cap are checked.
7. The post is matched to partners whose receive contract accepts the category and whose destination is currently verified/eligible.
8. The partner receives an offer with response controls.
9. The chain records accept, reject, skip, or report decisions.
10. Audit/delivery state is recorded.

### Owner/admin review

`/adminlogin <CODE>` redeems a scoped one-time role invite. Partnership-scoped admins can use the partnership review functions. The owner/admin review panel shows pending human-review submissions and allows approval/rejection. Reports are intended to be reviewed manually; there is no automatic ban from a single report.

### Partnership broadcast worker

`notify_loop()` periodically performs:

- state-aware destination re-verification;
- partnership-scope broadcast recovery and delivery;
- pending review-result notifications.

`_process_partnership_broadcasts()` claims only `bot_scope="partnership"`, applies rate admission and enforcement, sends explicit text payloads, completes or retries delivery, and records worker metrics.

## 4. Current architecture/data flow

```text
Telegram user update
        |
        v
aiogram Dispatcher in partnership_bot.py
        |
        +--> role/session/terms/category validation
        +--> EnforcementGate
        +--> SubmissionGate / ReviewQueue
        +--> ChannelRegistry / DestinationStateMachine
        +--> TelegramVerificationService -> Telegram Bot API
        +--> Contract / PerformanceEngine / daily_post_cap
        +--> Relay and owner-bot delivery route
        +--> DeliveryAudit / BroadcastQueue / WorkerStore
        |
        v
Telegram response, offer, review notice, or failure explanation
```

User-owned destination flow:

```text
User -> HTTPS Mini App -> signed Telegram initData validation
     -> user-owned BotFather token -> getMe -> encrypted credential store
     -> destination added as administrator -> Bot API verification
     -> destination state ACTIVE/eligible -> partnership matching
```

Post offer flow:

```text
Forwarded post -> terms/category/content gate -> enforcement gate
 -> verified eligible partners + receive contract match
 -> audit offer -> partner decision
 -> direct/chain route if permitted -> delivery result -> audit/metrics
```

## 5. Existing state and persistence

### JSON compatibility store

`PARTNER_STORE_PATH` defaults to `partnership_state.json` under `STORE_DIR`. Existing partnership data includes compatibility ledger/member records, contracts, delivery/audit-related data, reports, review queues, roles/invites, sessions, and legacy managed-channel data depending on migration state.

### Shared SQLite stores

- `VERIFICATION_DB_PATH`: encrypted user-owned credentials, destinations, verification checks, state history, and due re-check indexes.
- `PLATFORM_DB_PATH`: shared delivery audit, roles/shared key-value records, and platform events.
- `BROADCAST_DB_PATH`: campaign/delivery queue state; scope separation is required so Partnership deliveries do not consume Reward deliveries.
- `WORKER_DB_PATH`: delivery worker metrics and rate coordination.
- `ENFORCEMENT_DB_PATH`: enforcement records, reports, incidents, safe mode, and related evidence foundations.

### Important migration reality

The project intentionally contains a mixed JSON/SQLite migration architecture. A new Partnership Bot repository should not blindly copy both authorities. Before production use, select one authoritative store per entity and provide explicit migrations/backups/reconciliation.

## 6. Existing controls and safety behavior

Positive existing controls:

- user-owned bot credential onboarding;
- Bot API permission checks;
- no manual member count as authority in the current verification path;
- fail-closed participation when destination setup is incomplete;
- per-user session state with TTL rather than one global submission session;
- terms/category/content gate;
- hard block plus human-review routing;
- contract receive-type filtering;
- daily post cap enforcement;
- enforcement gate before registration, participation, and delivery;
- owner/scoped-admin roles;
- one-time invite codes;
- report records and human review;
- safe-mode pause behavior;
- bounded worker retries and stale-processing recovery;
- delivery audit and metrics.

Limitations and risks:

- The current bot is a large monolithic dispatcher module, so feature changes have high regression risk.
- JSON compatibility state is still present and can race or diverge from SQLite authorities.
- Background loops use in-process scheduling; multiple instances would require coordination/leases.
- Partnership offer chain is not a durable marketplace reservation system.
- Partner response/acceptance is not proof that a destination actually published a post when the flow is manual.
- Telegram views/engagement are not generally available through the Bot API; optional MTProto observation is not a safe default.
- There is no complete commercial settlement/withdrawal system in this bot.
- Live production failure/recovery and high-volume behavior are not proven.

## 7. Recommended separate-project architecture

### Keep for the first rebuild

- Python and aiogram 3.
- One Partnership Bot process for a small pilot.
- SQLite with WAL for a single-host pilot, provided all writes are transactionally designed.
- One shared Telegram adapter/service boundary.
- One verification/permissions service.
- One contract service.
- One submission/content policy service.
- One delivery service with durable job state.
- One audit/event service.
- Existing HTTPS Mini App onboarding pattern, with server-side initData validation.
- Offline tests and mocked Telegram sessions.

### Introduce before growth

- Router-based aiogram structure instead of a single large dispatcher file.
- Typed domain objects and explicit service interfaces.
- Durable state transitions with compare-and-set/transaction boundaries.
- Separate tables for users, destinations, contracts, submissions, offers, decisions, deliveries, reports, enforcement, and audit events.
- Outbox/worker model for notifications and deliveries.
- Idempotency keys for task/offer decisions and reward events.
- Structured JSON logs and correlation IDs.
- Backup/restore drills.

### Postpone

- Microservices, Kubernetes, Redis/Celery, cross-platform integrations, AI moderation, MTProto analytics, crypto/withdrawals, and a rich visual partner CRM until usage proves the need.

## 8. Recommended project structure

```text
partnership-bot/
  app/
    bot/
      routers/
        start.py
        onboarding.py
        destinations.py
        contracts.py
        submissions.py
        offers.py
        reports.py
        admin.py
      keyboards/
      messages/
      middleware/
    services/
      users.py
      destinations.py
      verification.py
      contracts.py
      submissions.py
      matching.py
      offers.py
      delivery.py
      notifications.py
      reports.py
      enforcement.py
    domain/
      models.py
      states.py
      policies.py
      events.py
    adapters/
      telegram.py
      storage.py
      clock.py
    persistence/
      migrations/
      repositories/
      sqlite.py
    workers/
      delivery_worker.py
      verification_worker.py
      notification_worker.py
  webapp/
  tests/
    unit/
    integration/
    telegram_fakes/
  docs/
  scripts/
  pyproject.toml
  .env.example
```

This structure is a recommendation, not a request to copy the current monolith mechanically.

## 9. Recommended data model for the separate project

Minimum entities:

- `users`: Telegram ID, username snapshot, role, state, created/updated timestamps.
- `user_sessions`: short-lived flow state, expiry, correlation ID.
- `bot_credentials`: encrypted user-owned bot credential metadata; never expose token.
- `destinations`: chat ID, public handle, kind, owner ID, state, category, consent flags.
- `destination_permissions`: required/observed permissions and last checked timestamp.
- `verification_runs`: each check result, reason, source, and timestamp.
- `contracts`: partner-to-partner terms, categories, schedule, volume, version, state.
- `submissions`: source message reference, sender, category, content/evidence reference, policy result.
- `offers`: submission-to-destination offer, state, expiry, idempotency key.
- `offer_decisions`: accept/reject/skip/report actor and timestamp.
- `deliveries`: route, destination, Telegram message ID, attempt count, state, error, timestamps.
- `reports`: reporter, subject, submission/offer/delivery references, status, notes.
- `enforcement_cases`: entity state, reason, actor, duration, related evidence.
- `notifications`: recipient, type, dedupe key, state, attempts, Telegram message ID.
- `audit_events`: immutable-ish append-only actor/action/object/reason/correlation record.
- `jobs`: durable worker lease, retry, next attempt, terminal state.

Use foreign keys, unique constraints, indexes on owner/destination/state/due time, and explicit state-transition functions. Keep Telegram IDs as numeric identifiers where available; handles are mutable display values.

## 10. Recommended lifecycle

```text
REGISTERED
  -> VERIFYING
  -> ACTIVE / VERIFIED
  -> DEGRADED / DISCONNECTED / RESTRICTED / REMOVED
```

Submission:

```text
DRAFT -> SUBMITTED -> POLICY_REVIEW (if needed)
       -> ELIGIBLE -> OFFERED -> ACCEPTED/REJECTED/SKIPPED/REPORTED
       -> DELIVERY_PENDING -> DELIVERED/FAILED/EXHAUSTED
```

Contract:

```text
DRAFT -> PROPOSED -> OPEN -> ACTIVE -> CLOSE_REQUESTED -> CLOSED
```

Every transition must validate the current state, actor permission, expiry, and idempotency key.

## 11. Telegram integration requirements

Use official Bot API capabilities only:

- `getMe` for connected user-owned bot identity.
- `getChat` for destination metadata.
- `getChatMember`/`getChatAdministrators` for membership/administrator checks where applicable.
- `getChatMemberCount` for current member count.
- `sendMessage`, `forwardMessage`/supported copy routes only when the bot has a legal route and permission.
- Inline keyboards and callback queries for decisions.
- `editMessageText`/`editMessageReplyMarkup` to remove stale decision buttons after a decision.
- Webhook with secret token for production, or long polling for a small single-host pilot.
- Signed Mini App `initData` validation on the backend and `auth_date` freshness checks.

Important limitations:

- Bot API member count is not a full analytics API.
- Channel view history and detailed engagement commonly require MTProto/user authorization or Telegram-provided statistics access; do not invent metrics.
- A bot cannot read arbitrary private chats or forward a message from a place where it has no access.
- Channel and group administrator rights differ; channel `can_post_messages` assumptions must not be applied to groups.
- Manual partner posting cannot be guaranteed by the bot. Direct posting requires the required bot admin rights.
- Public usernames are not stable identifiers; persist canonical chat IDs after resolution.

References:

- Telegram Bot API: <https://core.telegram.org/bots/api>
- Telegram Mini Apps: <https://core.telegram.org/bots/webapps>
- aiogram documentation: <https://docs.aiogram.dev/>

## 12. Notifications and broadcast separation

The Partnership Bot should own partner-scope communication. The Reward Bot should own reward-user communication. Shared infrastructure may provide queue, delivery, retry, audit, and audience interfaces, but each campaign must carry a mandatory bot scope and audience policy.

Recommended notification types:

- destination verification changed;
- contract proposed/accepted/closed;
- submission approved/rejected/reviewed;
- new partner offer;
- partner decision;
- delivery success/failure;
- report status;
- enforcement/appeal result;
- maintenance/system notice.

Every notification should have a dedupe key and delivery state. Repeated events should update or coalesce where appropriate rather than spamming a partner.

## 13. Admin requirements for the separate project

Initial owner controls:

- view partners and destination verification;
- inspect contracts and lifecycle;
- review policy queue;
- review reports and evidence;
- restrict/suspend/restore entities;
- inspect delivery and worker state;
- pause partnership delivery/safe mode;
- inspect audit history;
- issue scoped admin invitations.

Future roles:

- owner;
- partnership manager;
- moderator;
- operations reviewer;
- read-only analyst.

Use capability checks, not scattered username checks. Every destructive action needs a reason, confirmation, and audit event.

## 14. Security requirements

- Never put BotFather tokens in chat when the Mini App is available.
- Encrypt user-owned credentials at rest and keep the key outside Git.
- Redact secrets from exceptions, logs, audit payloads, and responses.
- Validate Telegram Mini App initData server-side.
- Use private-chat restrictions for credential operations.
- Validate callback ownership; never trust an index or target supplied by a callback without reloading and checking the caller's rows.
- Use idempotency keys for accept/reject, delivery, reward, and state-changing operations.
- Rate-limit registration, scans, submissions, and notifications.
- Keep reports as allegations until reviewed.
- Use safe mode to pause risky automation without deleting history.
- Verify destination permissions again before delivery, not only at registration.
- Use webhook secret tokens in production.
- Do not implement account rotation, spam, fake engagement, or Telegram restriction bypasses.

## 15. Testing specification

### Unit tests

- contract category matching;
- contract transition rules;
- submission policy hard blocks and review routing;
- destination state transitions;
- enforcement gate decisions;
- daily cap calculation;
- offer matching;
- expiry and cancellation;
- idempotent decision handling;
- notification deduplication;
- retry/backoff;
- owner/scoped-role permissions.

### Integration tests

- onboarding credential storage without secret leakage;
- registration and Telegram verification service with a fake Bot API;
- permission removal fail-closed;
- restore/reverify;
- complete offer lifecycle;
- final-slot race with two users;
- repeated callback delivery;
- worker crash/recovery;
- broadcast scope separation;
- report-to-review-to-enforcement-to-notification;
- backup and restore.

### Live staging tests

Use dedicated bots, a dedicated channel/group, isolated databases, and HTTPS. Test actual Bot API identities, chat types, permissions, delivery, failure, and recovery. Never use production tokens or resources.

## 16. Deployment recommendation

### Zero-budget pilot

- One small always-on host or suitable free host with HTTPS support.
- Long polling is acceptable for a single pilot process; webhook is preferable once a stable HTTPS endpoint exists.
- SQLite WAL with local backups is acceptable only for a single-host, low-concurrency pilot.
- Run bot and worker processes under a supervisor.
- Keep staging and production directories, keys, tokens, and databases separate.
- Use the existing `preflight.py`/staging checks and a documented smoke test.

### Growth path

- PostgreSQL for authoritative relational state.
- Redis or a managed queue only after measured contention/throughput requires it.
- Dedicated delivery/verification workers.
- Webhook ingress with secret token and bounded processing.
- Central structured logging, metrics, alerting, and object-storage backups.
- Multiple bot instances only after update ownership, idempotency, and worker leases are proven.

## 17. Existing versus recommended implementation

| Area | Existing | Recommended separate project |
|---|---|---|
| Handler organization | One large `partnership_bot.py` dispatcher | aiogram routers by bounded feature |
| State | JSON sessions plus SQLite domains | durable session/state tables and explicit transitions |
| Verification | Shared verification service/store | retain concept, isolate behind service/repository interfaces |
| Contracts | Core/governance objects and JSON-backed records | relational contract/version/transition model |
| Offers | In-process offer chain and audit | durable offer records, expiry, idempotency, assignment state |
| Delivery | in-process loops and shared queue | leased durable jobs and one delivery service |
| Reports | report registry/review queue | report/evidence/case/notification relationship |
| Admin | bot-scoped roles and panels | capability-based role service plus optional Mini App |
| Analytics | reliability/performance foundations | explicit measurable metrics with known Telegram limits |
| Scaling | single-process/local storage assumptions | single-host first, migration-ready service boundaries |

## 18. Rebuild checklist

### Preserve

- Permission-first destination verification.
- User-owned bot model.
- Terms and content gate.
- Contract accept/receive distinction.
- Manual review for borderline content.
- Fail-closed enforcement.
- Owner mediation.
- Scoped roles.
- Audit and retry foundations.

### Redesign before production

- Split the monolithic handler file into routers/services.
- Remove ambiguous legacy manual-count paths.
- Establish one storage authority per entity.
- Make offers and decisions durable/idempotent.
- Make notifications durable/deduplicated.
- Add clear contract and delivery state machines.
- Add complete operational dashboards.
- Define recovery and backup procedures.

### Do not build initially

- Paid advertising marketplace.
- Crypto deposits/withdrawals.
- Fake engagement or guaranteed results.
- MTProto scraping as a default requirement.
- Microservices/Kubernetes.
- Autonomous AI enforcement.
- Unreviewed mass messaging.

## 19. Handoff instructions for a new repository

1. Create a separate repository and environment; do not copy `.env`, databases, logs, or tokens.
2. Start from this specification plus the source modules listed above.
3. Decide which existing code is reusable only after tests and dependency review.
4. Recreate dedicated staging bots and a staging destination.
5. Build the domain/service/storage skeleton before porting handlers.
6. Port verification and permission tests first.
7. Port contract and submission policy tests next.
8. Port delivery and report workflows behind services.
9. Add Mini App/admin only after backend contracts are stable.
10. Keep a changelog and decision record from the first commit.
11. Run offline tests and live staging tests separately.
12. Never connect the new project to the old project's databases or credentials.

## 20. Known unknowns

The current source does not prove:

- a complete commercial partnership settlement model;
- guaranteed manual partner publication;
- comprehensive Telegram channel analytics;
- production webhook operation;
- multi-instance correctness;
- 1,000–10,000 user load behavior;
- a complete partner CRM/Mini App;
- legally reviewed monetization or payout operations.

These require separate product, legal, infrastructure, and live-staging decisions.
