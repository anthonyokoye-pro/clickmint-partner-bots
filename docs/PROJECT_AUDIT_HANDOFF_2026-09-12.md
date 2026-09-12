# ClickMint Partner Bots — Project Audit, Research Record, and Freeze Handoff

**Audit date:** 2026-09-12  
**Repository:** `anthonyokoye-pro/clickmint-partner-bots`  
**Session branch:** `arena/01a0713e-clickmint-partner-bots`  
**Purpose:** Freeze the project after the current staging verification work and preserve an evidence-based handoff for a future developer or AI agent.

> This document records what the checkout actually contains and what was actually exercised. A source file or test is not treated as proof of production readiness. Live Telegram evidence is explicitly separated from offline evidence.

## 1. Executive decision

**Project state: strategically paused, not production-ready.**

The project has a substantial non-financial Telegram platform foundation: three bots, shared governance, channel verification, user-owned bot onboarding, internal Mint accounting, task and broadcast foundations, enforcement/safe-mode foundations, and an Admin HTTP/Mini App surface.

The core staging identity and destination-permission path was exercised successfully with dedicated staging resources:

- Reward, Partnership, and Admin bot processes were configured separately from production.
- A user-owned staging bot was connected through the Telegram Mini App.
- A staging channel was registered and verified through Telegram Bot API.
- Telegram-reported member count, chat type, administrator state, and posting permission were observed.
- Removing `can_post_messages` caused the destination verification to fail closed; restoring it allowed re-verification.
- The Admin Mini App was opened in Telegram after its frontend syntax issue was corrected.

The following are **not** proven by this audit or the live staging work: production deployment, durable operations, complete task marketplace lifecycle, complete broadcast lifecycle, restart recovery under real Telegram traffic, financial safety, revenue generation, scalability, or legal/compliance readiness.

**Freeze rule:** do not add new product features to this repository until the project is explicitly reopened. Documentation and preservation work are the only permitted changes during this pause.

## 2. Repository and source-control state

At audit time, the checkout had a dirty working tree with a large set of modified and untracked files. This is important: the apparent implementation is not represented by a clean, reproducible local commit.

Observed local state:

- Current local `HEAD`: `c7e4b8e` (`Merge audit fixes: runnable bots, corrected credit economy, tests + guided setup`).
- The session branch exists locally.
- `origin/main`: `c7e4b8e`.
- The session branch on GitHub was later advanced to `2db46e2`, which contains the Admin Mini App JavaScript startup syntax correction.
- The local checkout still contains many modifications/untracked files and should not be force-reset without an explicit preservation plan.
- `.env`, `.env.staging`, database files, logs, PID files, virtual environments, and other local runtime material must remain outside Git.

**Required preservation action when the project is reopened:** create a clean archival branch/tag or patch bundle after reconciling the dirty tree. Do not use `git clean -fd` or `git reset --hard` against a working staging checkout until secrets and local data have been backed up safely.

## 3. Complete project structure

### Runtime and bots

- `reward_bot.py` — reward/exchange bot handlers, marketplace commands, destination registration, offers, reports, referrals, and owner operations.
- `partnership_bot.py` — curated partnership contracts, partner offer/accept/reject flow, channel registration, and partner-specific workflows.
- `admin_bot.py` — owner/admin Telegram bot, admin login/scope handling, and administrative commands.
- `admin_server.py` — WSGI-style local HTTP server composing Admin API, onboarding API, and static Web App assets.
- `admin_http.py` — HTTP routing, static files, API dispatch, origin checks, rate limiting, and response handling.
- `admin_api.py`, `admin_service.py`, `admin_idempotency.py` — Admin API/service boundaries and idempotency handling.
- `admin_web/` — Admin Mini App HTML, JavaScript, and CSS.
- `onboarding_api.py`, `onboarding_web/` — HTTPS user-owned bot credential onboarding.
- `telegram_verification.py` — Bot API identity, destination, member, administrator, permission, and eligibility checks.

### Core/domain modules

- `core.py` — legacy/core exchange rules, categories, tier/performance constants, distribution concepts, and compatibility behavior.
- `governance.py` — roles, terms, categories, caps, contracts, reports, owner bypasses, and policy rules.
- `eligibility.py` — participation and task eligibility.
- `destination_state.py` — explicit destination state machine and failure classification.
- `channel_registry.py`, `channel_connection.py` — destination and user-owned bot connection boundaries.
- `verification_store.py` — shared SQLite verification/credential store.
- `user_directory.py` — canonical SQLite account/user boundary.
- `mint_ledger.py`, `migrate_mint.py`, `currency.py`, `economics.py` — internal Mint/account/economic foundations; not a live money system.
- `task_marketplace.py`, `services/task_service.py` — task storage, claims, slots, completion, and recovery foundations.
- `broadcast_queue.py`, `services/broadcast_service.py`, `audience.py` — broadcast audience, queue, lifecycle, retry, and recovery foundations.
- `delivery_worker.py`, `scheduler.py`, `relay.py` — delivery rate limiting, scheduled work, legal Telegram forwarding routes, and recovery.
- `platform_store.py`, `db_backup.py` — shared SQLite platform/audit/role persistence and backup support.
- `credibility.py`, `performance_snapshots.py`, `referral_ranking.py` — performance, credibility, ranking, and referral foundations.
- `ad_campaigns.py` — consent/review-gated advertising campaign foundation; advertising is disabled by default.
- `enforcement.py`, `enforcement_gate.py`, `human_verification.py` — safety, restriction, appeals/reporting foundations, and human-verification abstraction.
- `tg_entities.py` — Telegram entity validation/preservation for captured formatting.
- `launch_scope.py`, `features.py`, `preflight.py`, `config.py` — launch guardrails, feature flags, environment configuration, and preflight checks.

### Operations and documentation

- `run_bots.sh`, `run_staging.sh` — local supervisor scripts.
- `setup.sh`, `deploy.sh`, `DEPLOY_FROM_GITHUB.md`, `DEPLOY_FREE.md`, service files — setup/deployment material.
- `staging_preflight.py` — non-secret staging isolation and financial-flag validator.
- `staging_telegram_check.py` — read-only live Telegram smoke checker.
- `go_live_check.py`, `admin_deploy.py` — offline deployment/readiness checks.
- `simulate.py`, `integration_smoke.py`, `run_all_tests.py`, `run_tests.sh` — offline validation and test runners.
- `docs/` — architecture, policy, deployment, research, status, history, and handoff material.

## 4. Dependencies and external services

Declared dependencies are intentionally small:

- `aiogram>=3.4.0` — Telegram bot framework.
- `python-dotenv>=1.0.0` — local `.env` loading.
- `cryptography>=42.0.0` — encrypted user-owned bot credential storage.

The code also uses Python standard-library components including `sqlite3`, `http.server`/WSGI-adjacent interfaces, `hmac`, `hashlib`, `json`, file locks/temporary files, and subprocess/process controls.

External services and protocols:

- Telegram Bot API for bot updates, `getMe`, chat metadata, membership/administrator checks, member counts, posting, forwarding, and Web Apps.
- Telegram Web Apps for signed `initData` identity.
- Cloudflare Quick Tunnel or ngrok for temporary local HTTPS exposure during staging.
- BotFather for dedicated bot creation and tokens.
- Optional MTProto/Telethon-style view observation is described but deliberately not part of the active transaction path.

The application does not currently have a production-grade external database, queue broker, secret manager, observability stack, payment provider, or domain/infrastructure contract.

## 5. Architecture and communication flow

### Bot-to-domain flow

1. aiogram receives Telegram updates in each bot process.
2. Bot handlers authenticate the Telegram user/role and call domain/service modules.
3. Domain modules persist to JSON compatibility stores or SQLite boundaries, depending on subsystem.
4. Delivery/scheduler/worker components call Telegram Bot API through aiogram sessions.
5. Audit, role, verification, queue, and account state are written to their respective stores.

### User-owned bot onboarding

1. A user opens the onboarding Mini App from the staging Reward Bot.
2. Telegram supplies signed `initData`.
3. The server validates `initData` using the Reward Bot token and freshness/signature checks.
4. The user submits a BotFather token over HTTPS.
5. The server calls `getMe`, encrypts the credential with `CLICKMINT_CREDENTIAL_KEY`, stores it in the shared verification database, and returns only non-secret identity data.
6. The user adds the owned bot as a destination administrator and runs `/register`.
7. Telegram verification checks destination access, chat type, bot status, permissions, and metadata.
8. Participation remains disabled when required checks fail.

### Admin Mini App flow

1. The Admin Bot launches `/admin_web/` through Telegram.
2. The frontend sends `X-Telegram-Init-Data` to the Admin API.
3. The server validates identity with `ADMIN_BOT_TOKEN` and applies allowed-origin checks.
4. Dashboard data and administrative actions route through `admin_http.py`, `admin_api.py`, and `admin_service.py`.
5. Destructive/idempotent operations use reason fields and idempotency keys where implemented.

### Important architecture tension

The system currently combines a mature legacy JSON bot engine with newer SQLite-backed account, verification, queue, platform, and service-boundary work. This is a migration architecture, not a fully unified production architecture. It increases the risk of split-brain state, inconsistent transactions, and difficult recovery across processes.

## 6. Persistence and data flow

The current configuration exposes these separate SQLite paths: Mint, tasks, broadcasts, verification, platform, credibility, enforcement, ads, Admin API, and worker metrics. Legacy reward/partnership/session/state data still uses JSON stores in parts of the codebase.

### Canonical/near-canonical boundaries

- Mint/user accounts: `mint_ledger.py` and `user_directory.py` are intended SQLite authorities; legacy JSON is migration/compatibility metadata.
- User-owned credentials and destinations: shared `verification_store.py` SQLite is the intended authority.
- Delivery audit and roles: `platform_store.py` shared SQLite is the intended authority.
- Task state: `task_marketplace.py`/task service SQLite foundation.
- Broadcast state: `broadcast_queue.py` SQLite foundation.

### Risks

- Not all domain state is on one transactional boundary.
- Bot processes can be started with different environment files or stale loaded configuration.
- File/path assumptions differ between Linux deployment templates and Windows Git Bash.
- Migration/compatibility code makes it possible for old JSON and new SQLite state to disagree.
- Backup/restore and cross-database transaction semantics are not proven under real crash conditions.

## 7. Functional status: what exists versus what is proven

### Reward/exchange system

**Implemented foundation:** registration, verification gate, categories, offer/accept/reject concepts, internal credit rules, owner bypass, reports, performance/credibility concepts, caps, and delivery paths.

**Not proven:** sustained real multi-user traffic, complete economic fairness, abuse resistance at scale, or production delivery correctness across all Telegram message types.

### Partnership system

**Implemented foundation:** partner registration, contracts/post types, forwarding/offers, agree/disagree chain, owner mediation, schedules and policy foundations.

**Not proven:** a complete reliable revenue-producing partnership operation, real partner network growth, complete commercial terms lifecycle, or production analytics.

### Admin system

**Implemented foundation:** Admin Bot, owner/admin roles, Admin API, static Mini App, dashboard, destination scan, campaigns, safety/enforcement sections, audit/performance/worker views, and idempotency foundations.

**Known current boundary:** the Admin Mini App had a JavaScript syntax error in its current source that prevented startup; it was corrected in commit `2db46e2`. Frontend capability parity and UX completeness remain partial.

### User-owned verification

**Live staging evidence:** passed. A user-owned bot was connected through the Telegram Mini App; a staging channel returned real Telegram metadata; administrator and required posting permission passed; removing posting permission failed closed; restoring it allowed verification again.

**Limitations:** only the tested staging path is proven. Group/supergroup/channel differences, blocked/private destinations, revocation, bot-token rotation, network faults, and long-term credential key management need separate verification.

### Task marketplace

**Implemented foundation:** task creation command, publishing, slots/claims, eligibility, completion/credit foundations, cancellation/recovery tests.

**Live status:** not completed in this session. Do not claim that real Telegram task creation, claim, completion, or duplicate-credit behavior passed live.

### Broadcast and advertising

**Implemented foundation:** draft/queue/pause/resume/cancel/delete lifecycle, retry/recovery foundations, delivery inspection, entity preservation, audience boundary, advertising consent/review model.

**Live status:** not completed in this session. Advertising is intentionally disabled and must remain disabled.

### Referrals, credibility, currency, deposits

Internal Mint/referral/credibility foundations exist. Deposits, withdrawals, fiat/crypto payments, Telegram Stars, boosts, external payouts, and currency conversion are guarded off. This project is not a financial product and must not be represented as one.

### Security

Positive controls include environment-based secrets, staging preflight, HTTPS-origin requirements, signed Telegram Web App validation, encrypted credential intent, token redaction work, permission verification, fail-closed eligibility, reports/human review, safe mode, idempotency, and rate limiting foundations.

Security gaps include local secret handling, lack of a production secret manager, mixed persistence, incomplete threat-model evidence, no independent penetration test, limited audit-log immutability, operational exposure of local servers/tunnels, and unproven key rotation/backup recovery.

## 8. Test and verification record

### Offline evidence

The repository includes broad unit/integration tests for core rules, governance, bots, task marketplace, broadcasts, verification, relay, destination state, enforcement, economics, Mint, backup, Admin API/HTTP/idempotency, Web App authentication, and service boundaries.

Historical documentation records broad test runs passing in a prepared environment. The current audit environment did not have `aiogram` importable (`ModuleNotFoundError`), so the complete runner was not re-certified during this audit. `py_compile` completed successfully for the top-level Python files.

Do not report “all tests passed” without recording the exact Python environment and command used.

### Live staging evidence

Passed:

- Dedicated bot identity and destination access.
- Channel type and member count behavior.
- Administrator and `can_post_messages` verification.
- User-owned token onboarding through Telegram Web App.
- Registration and permission removal/restoration behavior.
- Admin Mini App opening/authentication after the frontend fix.

Not completed/proven:

- Full live task lifecycle.
- Full live broadcast lifecycle.
- Real worker crash/restart recovery.
- Production deployment.
- Financial or advertising operation.

## 9. Problems discovered: problem → cause → investigation → result → future action

### Linux database paths on Windows

- **Problem:** bots failed with paths resolving to `C:\Program Files\Git\srv\...` and permission/path errors.
- **Cause:** staging template used Linux `/srv/...` paths; Git Bash/Windows Python interpreted them as local Windows paths.
- **Investigation:** startup logs and path resolution identified the failure before Telegram validation.
- **Attempted solution:** changed staging paths to `./.staging-data/...`, created the directory, and reran preflight.
- **Result:** staging preflight and bot startup path became viable.
- **Future action:** maintain platform-specific examples or normalize paths in code; test the actual deployment target separately.

### Python executable mismatch

- **Problem:** shell supervisors used `python3`, while Windows Git Bash required `python` in this setup.
- **Cause:** Linux-oriented scripts were reused on Windows.
- **Result:** local script substitutions were needed.
- **Future action:** make the interpreter configurable (`CLICKMINT_PYTHON`) and avoid editing tracked scripts ad hoc.

### Telegram Web App invalid signature

- **Problem:** `invalid initData signature` appeared.
- **Cause:** onboarding was opened from the wrong bot; the backend validates initData with the Reward Bot token.
- **Investigation:** source confirmed `platform_bot_token=config.REWARD_BOT_TOKEN`.
- **Result:** opening from the staging Reward Bot fixed authentication.
- **Future action:** make launch provenance clearer in UI and document which bot is allowed to sign each Mini App.

### Admin Mini App stuck authenticating

- **Problem:** UI stayed on `Authenticating with Telegram...`.
- **Cause:** syntax error in `admin_web/app.js` prevented the script from executing.
- **Investigation:** Node syntax check identified `if (...) &&` as invalid JavaScript.
- **Result:** one-line correction was committed as `2db46e2`; Admin Mini App then loaded.
- **Future action:** add a frontend lint/syntax check to CI and use a bundled/minified asset pipeline only after it is reproducible.

### ngrok API returned HTML instead of JSON

- **Problem:** onboarding showed `Unexpected token '<', "<!DOCTYPE ..." is not valid JSON`.
- **Cause:** free-plan ngrok warning/interstitial behavior was interfering with Telegram Web App API requests.
- **Result:** Cloudflare Quick Tunnel remained the working temporary Telegram test origin.
- **Future action:** use a production HTTPS origin or a tunnel without an incompatible interstitial; test the exact Telegram client behavior before selecting a tunnel provider.

### Missing `.env.staging`

- **Problem:** `run_staging.sh .env.staging` failed because the file did not exist.
- **Cause:** secrets are intentionally untracked and were absent from the local checkout after repository synchronization.
- **Result:** file must be recreated from `.env.staging.example` with dedicated resources and a new key.
- **Future action:** use a local secret manager/password manager or an encrypted operator backup; never put staging secrets in Git.

### Mixed state and dirty checkout

- **Problem:** current working tree contains many modified/untracked implementation files.
- **Cause:** iterative development and generated/added modules were not consolidated into a clean commit boundary.
- **Result:** reproducibility and pull/reset safety are reduced.
- **Future action:** perform a deliberate source-control reconciliation before reopening; do not casually reset or clean.

## 10. Architecture assessment and better path forward

The current approach is **conceptually sound for a non-financial Telegram MVP**, especially its permission-first distribution model, user-owned bot verification, fail-closed state machine, and separation of financial flags. It is not yet the best long-term implementation because too much functionality is split between a legacy JSON engine and newer SQLite/service boundaries.

A better next architecture would be:

1. Freeze current behavior and create executable contract tests around the live-verified flows.
2. Choose one authoritative persistence model. Prefer PostgreSQL for a multi-process/server deployment, or a deliberately bounded SQLite deployment for one host only.
3. Introduce explicit application services for user, destination, task, campaign, delivery, ledger, and enforcement operations.
4. Give each operation a transaction/idempotency boundary and append-only audit event.
5. Use one worker queue model with leases, retry policy, dead-letter state, and recovery tests.
6. Keep Telegram adapters thin and put policy in services/domain code.
7. Keep payments/withdrawals outside the core until compliance, accounting, and custody decisions are approved.
8. Keep the Admin Mini App as a client of versioned APIs rather than allowing frontend code to encode policy.
9. Add independent staging and production configurations with explicit startup checks.

A full rewrite is not justified yet. A targeted persistence/service-boundary consolidation is better than continuing to add features to the mixed architecture.

## 11. Multi-agent development recommendation

Multiple AI agents are useful only when they have separate responsibilities and do not edit the same working tree concurrently.

Recommended workflow after reopening:

- **Implementation agent:** makes a narrowly scoped change with tests and a migration note.
- **Independent audit agent:** reads the proposed diff and source, challenges claims, checks security and failure modes, and does not modify files.
- **Research agent:** checks official Telegram/API/deployment documentation and records dated sources.
- **Test agent/environment:** runs the exact test matrix in a clean checkout and reports environment facts.
- **Release agent:** reconciles Git state, changelog, deployment configuration, rollback plan, and secrets checklist.

Use worktrees or separate clones per agent. Require every change to include:

- Scope and non-goals.
- Tests run and exact environment.
- Data migration/rollback impact.
- Security impact.
- Live verification status versus offline status.

Do not use multiple agents to generate competing unreviewed rewrites. One owner should merge changes only after independent review.

## 12. Freeze checklist

Before leaving the project paused:

- Stop all local bots, Admin server, tunnels, and workers.
- Keep `.env.staging`, encryption keys, tokens, databases, logs, and PID files out of Git.
- Revoke/rotate any credential that was ever pasted into chat or exposed in logs.
- Preserve the exact live staging observations without preserving secrets.
- Record the clean Git baseline and reconcile dirty files before future development.
- Do not enable financial or advertising flags.
- Do not claim unexecuted task, campaign, or recovery tests as passed.
- Do not deploy the temporary tunnel URL as production.

## 13. Return-to-project sequence

When the project is reopened:

1. Create a clean clone from the selected archival commit/branch.
2. Reconcile the dirty working tree and decide which modules are authoritative.
3. Install dependencies in a recorded virtual environment.
4. Run the complete offline test suite and record failures.
5. Recreate isolated staging resources with fresh credentials.
6. Re-run the read-only Telegram verification gate.
7. Re-run only one lifecycle at a time: task, broadcast, safety, restart recovery.
8. Fix architecture and operational gaps before adding product features.
9. Obtain an independent security/compliance review before any revenue or payout functionality.

## 14. Separate Partnership Bot priority

The Partnership Bot should now be treated as a separate project and separate repository/environment, as requested. It should not share the ClickMint checkout, databases, bot tokens, or deployment secrets.

The practical objective is legitimate channel growth and partnership operations—not artificial engagement or unauthorized posting. The separate project should begin with:

- Clear partnership offer/contract model.
- Consent and opt-in records.
- Telegram permission checks.
- Human review for commercial content.
- Transparent partner reporting.
- No spam, fake engagement, credential sharing, or permission bypass.
- Separate staging credentials and a small, measurable pilot.

Revenue assumptions should be tested with real partner demand before investing in paid infrastructure. Free hosting/tunnels can support early experiments, but they are not a substitute for a stable production operation.

## 15. Final handoff summary

**Where we are:** substantial Telegram platform foundation, live staging verification path proven, project intentionally paused.  
**What works:** core offline rules and several Admin/verification foundations; real staging bot/channel onboarding and fail-closed permission behavior.  
**What went wrong:** platform path assumptions, interpreter mismatch, wrong Mini App signer, ngrok interstitial, missing local secrets, and a frontend syntax error.  
**What remains:** clean source reconciliation, complete offline dependency-aware test record, full live task/broadcast/restart tests, production operations, and architecture consolidation.  
**What must not happen:** no production financial activation, no credential reuse, no permission bypass, no claim of complete production readiness, and no feature expansion during the freeze.  
**Next strategic move:** preserve this handoff, freeze ClickMint, and develop the Partnership Bot separately.
