# CLICKMINT AI Handoff and Source-of-Truth Guide

**Generated/updated:** 2026-09-10
**Branch:** `arena/01a0713e-clickmint-partner-bots`
**Current checkout:** `c241627` plus the continuous implementation pass in the working tree
**Previous implementation baseline:** `d9fcb6d` — `Apply verification gate to scheduled delivery`

## Purpose

This document allows a new AI session or developer to continue CLICKMINT without relying on an earlier chat. It summarizes the verified current architecture, separates implementation from proposals, records binding constraints, and points to the authoritative supporting documents.

This is a handoff, not a claim that every requested future feature has been implemented.

## Read order

1. `docs/CLICKMINT_AI_HANDOFF.md` — this orientation and status boundary.
2. `docs/IMPLEMENTATION_STATUS.md` — implementation matrix.
3. `docs/DECISION_REGISTER.md` — binding decisions and rejected approaches.
4. `docs/MASTER_SYSTEM_DOCUMENTATION.md` — detailed system description; reconcile it against source where it is older.
5. `docs/ARCHITECTURE_AUDIT_2026-09.md` — architecture findings.
6. `docs/AUDIT_REPORT.md` — audit evidence and known limitations.
7. `docs/ENGINEERING_HISTORY.md` — recorded engineering decisions.
8. `docs/MASTER_PLAN.md` — roadmap and older handoff material.
9. Feature documents such as `docs/ADMIN_API.md`, `docs/REWARD_SYSTEM.md`, `docs/TELEGRAM_USER_BOT_VERIFICATION.md`, `docs/COMPLIANCE_AND_ENFORCEMENT.md`, and `docs/ECONOMICS.md`.

## Mandatory operating rules for a new session

Before changing code:

- Inspect the current Git branch, status, and remote tip.
- Do not discard dirty work or reset the checkout without explicit confirmation.
- Read the documents above and compare material claims with source code.
- Label every claim as implemented, partial, research/design, future, rejected, or unknown.
- Run the relevant tests before and after changes.
- Preserve the security and Telegram constraints in `docs/DECISION_REGISTER.md`.
- Do not implement deposits, withdrawals, Boost, advertising, advanced AI, or autonomous production engineering merely because they appear in the roadmap.
- Ask only when a decision is genuinely ambiguous, high-impact, destructive, requires credentials, or requires an external account.

## Repository synchronization status

The earlier checkout/remote mismatch was reconciled by rebasing onto `d9fcb6d` without discarding local work. The documentation commits and the continuous implementation pass are now on the fixed session branch. Always inspect `git status` and compare the remote before future destructive operations. The retained pre-existing-work stash was not dropped.

## Current architecture

```text
Telegram users/admins
        |
        +--> Reward Bot --------------------+
        |                                    |
        +--> Partnership Bot                 |
        |                                    v
        +--> Admin Bot / Telegram Mini App -> application services
                                             |
                              +--------------+----------------+
                              |                               |
                         Governance / eligibility       Admin API/HTTP
                              |                               |
                         Core reward/partner logic      Mini App frontend
                              |                               |
                 JSON stores + SQLite queue/ledger + audit data
                              |
                    Telegram Bot API / external Telegram operations
```

### Main runtime layers

- **Interaction layer:** `reward_bot.py`, `partnership_bot.py`, `admin_bot.py`, `ui.py`, and `admin_web/`.
- **Application/service layer:** `admin_api.py`, `admin_service.py`, `admin_http.py`, `broadcast_queue.py`, channel/verification modules, and supporting services.
- **Business and governance layer:** `core.py`, `governance.py`, `eligibility.py`, `credibility.py`, `enforcement.py`, `economics.py`, `currency.py`, `referral_ranking.py`, `task_marketplace.py`.
- **Persistence layer:** `store.py`, JSON-backed domain state, SQLite-backed queue/backup/ledger components, and runtime files excluded by `.gitignore`.
- **External boundary:** Telegram Bot API and Telegram Mini App authentication data. Future payment, AI, and cross-platform systems are not core dependencies.

## Important source modules

| Module | Responsibility | Status |
|---|---|---|
| `core.py` | Core domain and reward logic | Current core |
| `governance.py` | Roles, permissions, governance decisions | Current core |
| `store.py` | JSON persistence and locking | Current core |
| `reward_bot.py` | Reward/exchange bot handlers and flows | Current bot |
| `partnership_bot.py` | Partner workflows and bot handlers | Current bot |
| `admin_bot.py` | Owner/admin bot controls | Current bot |
| `ui.py` | Telegram presentation and keyboards | Current UI layer |
| `branding.py` | Bot copy/branding constraints | Current support |
| `scheduler.py` | Scheduling/background behavior | Current support |
| `views_provider.py` | View/statistics integration boundary | Current support; capabilities limited by Telegram |
| `admin_api.py` | Authenticated Admin Mini App application API | Current foundation |
| `admin_http.py` | WSGI/HTTP route boundary | Current foundation |
| `admin_service.py` | Admin service helpers | Current foundation |
| `broadcast_queue.py` | Campaign and delivery queue/state | Current foundation |
| `channel_connection.py` | User-owned bot/channel connection data | Current foundation |
| `channel_registry.py` | Registered destination/channel records | Current foundation |
| `telegram_verification.py` | Telegram destination/bot verification | Current foundation |
| `webapp_auth.py` | Server-side Mini App authentication | Current foundation |
| `admin_idempotency.py` | Duplicate mutation protection | Current foundation |
| `enforcement.py` | Reports, evidence, states, safe mode | Current foundation |
| `human_verification.py` | Human-verification abstraction/foundation | Partial; no universal provider claim |
| `eligibility.py` | Participation/eligibility rules | Current foundation |
| `credibility.py` | Credibility/performance support | Current foundation |
| `performance_snapshots.py` | Performance history | Current foundation |
| `referral_ranking.py` | Referral/ranking support | Partial |
| `mint_ledger.py` | Internal Mint/credit ledger foundation | Partial economic system |
| `task_marketplace.py` | Task marketplace foundation | Partial/product design incomplete |
| `economics.py`, `currency.py` | Economic rules/currency support | Foundation; not deposit/withdrawal system |

## 2026-09-10 continuous implementation pass

The approved Phase 0–6 pass completed the following safe reliability and UX work:

- `audience.py` now provides an explicit audience-resolution boundary.
- Admin Reward campaign queueing now resolves known Reward Bot recipients through `AudienceDirectory`; it no longer reads the empty Admin ledger compatibility facade.
- Campaign draft deletion now creates an auditable tombstone with `deleted_at` and `deleted_by`; deleted records are not queueable.
- Mini App campaign cards now expose state-appropriate Edit, Continue, Cancel, and Delete controls, with destructive confirmations.
- Automated Reward and Partnership broadcast/task delivery no longer passes uncontrolled text through HTML parse mode; entity payloads are validated and preserved for Admin campaigns.
- Admin bind host is configurable through `ADMIN_BIND_HOST` and defaults to `0.0.0.0` for container/live-preview compatibility.
- Audience and Admin queue regression tests were added.
- A dependency-aware run using `/tmp/cmvenv` installed `requirements.txt`; `run_all_tests.py` completed successfully.

This pass did not claim complete implementation of every roadmap phase. Ad Campaign semantics, full Telegram entity editing, complete task-service extraction, complete Admin parity, and full verification scan UX remain partial and are explicitly not represented as complete.

## What is implemented now

The implementation set contains a functioning foundation for:

- Reward, Partnership, and Admin bots.
- Governance, eligibility, credibility, enforcement, and safe-mode concepts.
- JSON persistence and SQLite-backed queue/ledger additions.
- Server-side Telegram Mini App authentication.
- User-owned bot/channel connection and permission verification foundations.
- Credential encryption support.
- Admin API and HTTP boundary with idempotency and audit-related operations.
- Broadcast campaign/queue lifecycle foundations, including titles, draft operations, pause/resume/cancel, and scheduled-delivery verification in `d9fcb6d`.
- Internal Mint/credit ledger foundations and referral/ranking support.
- Tests for many of the above modules.

Implemented does not mean production-complete. See `IMPLEMENTATION_STATUS.md` for partial areas.

## What is partial or incomplete

- Reward draft controls are not yet a complete polished frontend lifecycle.
- Dedicated Ad Campaign lifecycle and data semantics are not complete.
- Queue view/edit/cancel/delete/retry/reschedule actions are not all exposed with state-aware UI.
- The Mini App dashboard is functional but not the complete redesign requested.
- The composer remains short of a complete Telegram-compatible visual editor and entity validation flow.
- Channel verification and statistics paths exist, but the complete visible scan UX and all failure remediation flows require verification.
- Task Marketplace capacity, claims, recommendations, notifications, and channel-execution integration require completion and tests.
- Human-verification provider integration and adaptive risk policy are not complete.
- Full admin capability parity is not established.

## What is not implemented and must remain future/approval-gated

- Deposits, payment processing, withdrawals, and custody.
- Boost as a separate paid/deposit utility currency.
- Telegram Stars/TON/crypto payment or payout integration.
- Complete advertising marketplace and automatic ad distribution.
- Complete referral economy, attribution, and join-based rewards.
- AI knowledge base, autonomous engineering, and multi-agent ecosystem.
- Cross-platform integrations.
- Large-scale distributed infrastructure.
- Full channel-growth intelligence and AI assistant.

## Core workflows and safety boundaries

### User-owned bot and channel verification

The intended sequence is:

```text
User supplies/starts user-owned bot setup
  -> bot is added to the required channel/group
  -> bot receives required administrator permissions
  -> server validates Telegram Mini App/user identity
  -> server queries Telegram for destination and bot status
  -> required permission and eligibility checks run
  -> participation is enabled only if all required checks pass
```

If a check fails, the system must state what failed and what the user must correct. Client-side flags are not authoritative. Manual member counts are not authoritative when Telegram can provide the value.

### Broadcast lifecycle

The current lifecycle model includes:

```text
draft -> queued -> running/processing -> completed
                    |                 \
                    +-> paused         +-> failed delivery records
                    +-> cancelled
```

Only valid state transitions may queue or resume campaigns. Deleted/cancelled campaigns must never be reintroduced into the queue. The frontend must expose only actions valid for the current state and confirm destructive actions.

### Future Task Marketplace lifecycle

The desired, not fully implemented, model is:

```text
created -> published -> eligible users see task -> claim/start
       -> execute through verified channel/bot -> verify completion
       -> award exactly once -> update capacity -> close/expire/cancel
```

Capacity belongs to the task, not to a user. Claims and completion require race-safe duplicate prevention.

### Future economy boundary

Keep these separate:

- **Mint:** internal reward/credit concept earned through approved contributions.
- **Boost:** proposed future utility/access balance associated with approved deposits/purchases.
- **External rewards:** future supported payout assets subject to Telegram, provider, regional, and compliance constraints.

Do not create conversion or withdrawal assumptions between them.

## Security and Telegram constraints

- Never expose bot tokens or credential material.
- Encrypt user bot credentials and protect the encryption key operationally.
- Validate Mini App `initData` on the server.
- Use official Telegram API responses for channel metadata, member counts, bot status, and permissions where available.
- Respect Telegram chat-type-specific permission fields.
- Do not claim access to statistics or views that the selected Telegram API does not expose.
- Do not fake engagement, views, subscribers, attribution, or performance.
- Use idempotency, audit events, rate limits, and governance for sensitive mutations.
- Treat human verification as one risk signal, not proof of humanity.
- Keep high-impact restrictions reviewable.
- Do not allow AI or client input to bypass authorization.

## Research-only and pending decisions

No final project decision has been recorded for:

- The replacement for `Band` terminology.
- Exact Ad Campaign model and advertising economics.
- Final Task Marketplace state machine and matching algorithm.
- Final Telegram editor/entity implementation.
- Human-verification provider and challenge thresholds.
- Referral attribution and reward formulas.
- Mint issuance, sinks, limits, and reversals.
- Boost name, packages, pricing, utility, and expiration.
- Payment/deposit/withdrawal providers and supported assets.
- Future hosting/database migration thresholds.

Do not silently choose any of these as though they were already approved.

## Rejected or prohibited approaches

- Shared-bot credentials instead of user-owned bots.
- Manual authoritative subscriber/member counts.
- Participation without required verification and permissions.
- Queueing deleted/cancelled/non-queueable campaigns.
- Unsupported Telegram markup or invented capabilities.
- CAPTCHA everywhere or CAPTCHA as a definitive human identity.
- Fake engagement, guaranteed views, guaranteed subscribers, or artificial popularity.
- Crediting deposits based on user claims.
- Treating Mint, Boost, and external rewards as interchangeable.
- Unreviewed high-impact autonomous production changes.
- Premature microservices and paid infrastructure without need.

## Test and validation commands

The repository provides these primary validation entry points:

```bash
python3 -m py_compile *.py
python3 run_all_tests.py
./run_tests.sh
```

`run_all_tests.py` is intended to run the broad Python test set. `run_tests.sh` includes compilation, ledger/integration, core, governance, bot wiring, branding, and dry-run checks. Complete results must be recorded in `docs/ENGINEERING_HISTORY.md` after execution; a test runner definition alone is not a test result.

## Safe continuation plan

1. Preserve the current dirty work and compare it with `FETCH_HEAD`/remote.
2. Run the complete test suite and capture failures without hiding them.
3. Reconcile documentation claims with source and tests.
4. Finish the Mini App/campaign state design before adding new economic systems.
5. Implement complete Reward lifecycle and distinct Ad Campaign lifecycle only after data-model decisions.
6. Add Telegram editor validation based on actual Telegram entities/parse modes.
7. Complete channel verification/statistics UX with real backend checks.
8. Add focused regression tests for every state transition.
9. Update this handoff, the implementation matrix, decision register, audit, and engineering history after meaningful changes.
10. Do not begin financial, advertising, or advanced AI implementation without a separate approved recommendation.

## Known limitations

- The current checkout/remote relationship requires careful Git reconciliation.
- Some bot tests may depend on the installed requirements environment, including `aiogram`.
- Telegram API capabilities differ by chat type and cannot be inferred from UI requirements.
- Production hosting, uptime, named-domain stability, real bot polling, and real destination permissions require deployment validation.
- Future financial, advertising, and AI systems cannot be represented as current functionality.

## Documentation reconciliation and validation result

The documentation gap register, decision register, and this handoff were created during the 2026-09-09 reconciliation pass. The source tree was compared with fetched `d9fcb6d` without resetting the dirty checkout.

Validation performed:

- `python3 -m py_compile *.py` — passed.
- `python3 run_all_tests.py` — stopped at `test_bots.py` because `aiogram` is not installed in the active Python environment.
- Core tests — passed: 28 tests.
- Governance tests — passed: 34 tests.
- Broadcast lifecycle tests — passed.
- Platform foundation tests — passed.
- Task marketplace test module — exited successfully with no output.
- Performance, credibility, economics, Mint ledger, database backup, integration smoke, Admin service/deploy/HTTP/API/idempotency, and Web App authentication tests — passed.
- `branding.py --check` — passed.
- `simulate.py --quiet` — blocked by the same missing `aiogram` dependency.

This is a partial green result, not a claim that the complete suite passed. Install the repository requirements in a controlled environment and rerun the full suite before calling bot wiring and simulation validation complete.

## Handoff instruction for the next AI

Read this file, `IMPLEMENTATION_STATUS.md`, and `DECISION_REGISTER.md` first. Inspect the source before making claims. Report conflicts between documentation and code. Do not reset the checkout. Do not expose secrets. Do not implement future systems just because they are described in roadmap documents. Continue with the safest clearly in-scope work, and stop only when a strategic, destructive, credential, external-account, or genuinely ambiguous decision requires human input.
