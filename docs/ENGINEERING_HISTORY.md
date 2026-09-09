# CLICKMINT Engineering History

## 2026-09-09 — Consent-aware advertising and group-scoped reports

**Problem:** The compliance directive required advertising to be separate from announcements
and tasks, with consent, versioned terms, a scoped Ads Manager role and safety review — none
of which existed. Reports could only be filed from inside an offer card, so abuse seen in a
group had no entry point.

**Alternatives considered:** reuse `BroadcastQueue` with a flag — rejected (audience is
"all known members", the opposite of opt-in; no review step; no terms binding). A global
"ads on/off" per user — rejected (consent must be per destination, since each channel's
audience is distinct).

**Decision:** `ad_campaigns.py` as an isolated store with hard preconditions raised as
`AdPolicyError`; `ADS_ENABLED` kill switch defaulting off; `"ads"` added to `RoleRegistry`
scopes; delivery through the destination owner's bot after fresh verification, always
labelled. `/report` in groups resolves the live chat id to the registered row and attaches the
replied message as evidence.

**Result:** `ad_campaigns.py`, `test_ad_campaigns.py` (6), `/ads`, `/report`, Mini App
Advertising panel + 9 routes, admin-bot Ads Manager invite, `go_live_check` advertising
check. `_execute_task_payload` now uses `TelegramVerificationService.make_bot()` so the
owner-bot path is a single mockable seam. Bot wiring tests 32→34.

**Remaining:** none from the audit list. Live validation needs real Telegram credentials.

## 2026-09-09 — Shared enforcement gate, appeals, and admin compliance surface

**Problem:** `enforcement.py` existed but nothing in the running bots consulted it; a
banned channel could still forward, claim tasks and receive partnership offers. Reports
filed in Telegram lived only in the legacy JSON registry, invisible to the Mini App, and a
restricted member had no way to be heard.

**Decision:** One `EnforcementGate` object (fail-closed, owner exempt from entity state but
not from safe mode) injected into every participation path instead of per-handler checks.
Appeals are a separate table: filing is inert; only an owner OVERTURN restores. In-bot
reports are mirrored into the compliance store rather than migrated, so the legacy panel
keeps working.

**Result:** `enforcement_gate.py`; gate calls in reward (register, forward, claim, scheduled
delivery), partnership (register, forward, target filter) and admin bots; `/appeal`
command; Reports & appeals panel + 9 new Admin API routes; admin-bot Trust & safety panel;
CI now runs the full suite. Tests: bots 26→31, enforcement 4→7, admin api/http +2.

**Follow-up (same day):** broadcast workers now honour safe mode and skip enforced
recipients (`BroadcastQueue.release()` added so a worker pause never burns a retry);
appeal decisions are delivered to the member via the outbox.

**Remaining:** consent-aware advertising; group-scoped report entry points.

## 2026-09-08 — Compliance enforcement foundation

**Problem:** Existing channel status and review queues did not provide a durable, system-wide way to flag, restrict, suspend, ban, restore, or remove users, channels, and groups. Reports and evidence could not be tied to an enforcement timeline, and there was no emergency safe mode.

**Alternatives considered:**

- Reuse channel `status` fields only — rejected because it cannot represent user/group incidents, evidence, reports, expiry, or system-wide controls.
- Auto-ban based on keywords/report counts — rejected because it creates false positives and conflicts with compliance-first human review.
- Add a separate in-memory moderation layer — rejected because incidents must survive restarts and be auditable.

**Decision:** Add a small SQLite enforcement store with explicit states, report workflow, evidence references, immutable enforcement events, temporary expiry, capability checks, and safe mode. Keep it separate from payment/economics and connect it to shared eligibility first.

**Result:** `enforcement.py` and `test_enforcement.py` added. Banned/restricted/suspended/removed entities and safe mode now fail closed in task eligibility when the application supplies the state.

**Remaining:** Admin UI/API wiring, bot report entry points, appeal workflow, and shared enforcement service injection.

## 2026-09-08 — Telegram compliance boundary

**Finding:** Direct posting is only legitimate where the bot has the required Telegram administrator rights. Quick Cloudflare tunnels are useful for testing but not stable production infrastructure.

**Decision:** Preserve permission verification and server-side Mini App initData validation. Do not add workarounds for Telegram restrictions, fake engagement, automated account rotation, or payout functionality.

## 2026-09-08 — Test environment limitation

**Finding:** Core and governance suites pass, but the environment used for offline audit does not have `aiogram`; bot wiring tests therefore cannot execute here.

**Decision:** Keep aiogram-dependent tests explicit and require `pip install -r requirements.txt` in Windows/CI before claiming full bot coverage. Never treat import-only checks as live Telegram validation.

## 2026-09-10 — Continuous Phase 0–6 reliability pass

**Findings addressed:** Admin campaign queueing depended on an empty compatibility ledger facade; draft deletion erased audit history; automated delivery passed uncontrolled content as HTML; the Admin server bind host was fixed to localhost; the Mini App exposed too few state-aware campaign actions; and the dependency-free environment could not run bot tests.

**Changes:** Added `audience.py` and explicit `AudienceDirectory` injection; added audience and Admin queue tests; changed draft deletion to an auditable tombstone; added Mini App Edit/Continue/Cancel/Delete controls; removed uncontrolled HTML parse mode from automated delivery paths; added configurable `ADMIN_BIND_HOST` defaulting to `0.0.0.0`; updated README and handoff/status docs.

**Validation:** Created `/tmp/cmvenv`, installed `requirements.txt`, and ran `run_all_tests.py` successfully. The suite passed core, governance, audience, bot wiring, offline simulation, enforcement, verification, broadcast, task, performance, credibility, economics, Mint, backup, integration, Admin, idempotency, and Web App authentication checks.

**Remaining:** The full Telegram entity editor, distinct Ad Campaign domain, complete task-service extraction, full Admin capability parity, and real external Telegram permission/deployment tests remain future work.

## 2026-09-09 — Documentation reconciliation and source-of-truth handoff

**Problem:** Requirements, research, decisions, implementation status, and future roadmap items were distributed across conversation history and partially overlapping Markdown files. A new session could not reliably distinguish implemented behavior from proposals.

**Investigation:** Compared the current checkout with fetched remote tip `d9fcb6d` without resetting the dirty worktree. Sampled implementation and documentation files matched the remote tip. Inspected the repository Markdown inventory and ran the available validation commands.

**Decision:** Add three explicit portable documents:

- `docs/CLICKMINT_AI_HANDOFF.md` for continuation context;
- `docs/IMPLEMENTATION_STATUS.md` for evidence-based feature state;
- `docs/DECISION_REGISTER.md` for approved, pending, rejected, and binding constraints.

**Validation result:** Python compilation passed. Core (28) and governance (34) tests passed. The broad non-Aiogram tests passed, including broadcast lifecycle, Mint ledger, admin, verification, and integration checks. The suite stopped at `test_bots.py`, and `simulate.py` could not run, because `aiogram` is not installed in the active environment. This is recorded as an environment limitation, not hidden as a green result.

**Lesson:** A test runner and a feature description are not proof of production completeness. Future sessions must reconcile source, tests, Git state, and documentation before implementation.

## 2026-09-08 — AI and verification scope decision

**Decision:** Keep AI and human verification out of the core transaction path. Add provider-neutral interfaces and structured risk/audit data first. Use progressive, risk-based challenges only for sensitive or suspicious actions. Do not add vector databases, autonomous production changes, or a CAPTCHA on every screen at the 1,000–10,000 target.

**Reason:** The current bottlenecks are reliability, enforcement integration, campaign lifecycle, and dependency-complete testing—not model intelligence. A provider-neutral boundary preserves replacement freedom and zero-budget operation.

## 2026-09-09 — Centralized destination state machine

**Finding:** Both bots wrote `verified_state=` in ~20 call sites with inconsistent status derivation. Reward `/disconnectbot` left destinations VERIFIED with no credential; the reconcile loop probed every destination of every member every 15 s; all Telegram failures collapsed into DEGRADED with a raw error string.

**Decision:** Single `DestinationStateMachine` (`destination_state.py`) with an explicit legal-edge table, derived `status`, mandatory reason+source, and a per-row history. Structured `classify_telegram_error` chooses the target state (revoked / inaccessible / disconnected / degraded) and whether to back off. Background re-verification is schedule-driven per state with a bounded batch and pauses in safe mode. Owners get inline Re-verify/Details/Remove controls in `/mychannels`.

**Not done here:** live Telegram integration tests — they require real bot tokens and a test channel, which this environment does not have.

## 2026-09-09 — Source-message relay architecture

**Finding:** Owner-bot delivery paths forwarded from the member's private chat with the *platform* bot. A bot cannot read another bot's private chats, so every owner-bot forward would fail live with "message to forward not found". The offline mock accepted every `ForwardMessage`, hiding it.

**Decision:** `relay.py` resolves one legal route per destination — platform bot forwards its inbox copy when it administers the destination; owner's bot forwards from the origin channel only when it administers that channel; otherwise fail closed with audit. No `copyMessage`, no rebuilt posts, no MTProto. The mock session now enforces Telegram's read rule so this class of bug cannot regress silently. See `docs/RELAY_ARCHITECTURE.md`.

## 2026-09-09 — Shared verification database (SQLite)

**Finding:** Reward and Partnership each kept `managed_channels` and `telegram_bot_credentials` in their own JSON file. The "one bot per ClickMint account" rule was only enforced within one bot, and every destination update rewrote the whole JSON file with a merge-on-sync that races across processes.

**Decision:** `verification_store.py` — one SQLite (WAL) database, `VERIFICATION_DB_PATH`, opened by both bots (and the admin bot read-side). `ChannelRegistry` and `BotCredentialStore` are unchanged; they now sit on the shared store. Legacy JSON rows are migrated once on first open and kept as `*_migrated`. The background re-verifier now queries `ix_dest_next` instead of loading every row. All other keys (ledger, audit, roles, sessions) stay in the per-bot JsonStore for now; they are the next migration candidate, not this one.

## 2026-09-09 — Web App token onboarding

**Finding:** `/connectbot <token>` transmits the member's bot secret through Telegram chat; message deletion is best-effort and the token is already in history on every device.

**Decision:** Mini App onboarding (`onboarding_api.py`, `/onboarding_web/`). Identity is taken solely from server-validated `initData` signed by the reward bot; the token goes HTTPS → server → `getMe` → AES-GCM in the shared verification DB and is redacted from every error, audit and response. The in-chat command remains as fallback when no HTTPS URL is configured and warns the member to rotate. See `docs/ONBOARDING_WEBAPP.md`.

## 2026-09-09 — Final offline audit

**Done:** secret redaction closed in three more places (partnership connect error, `ClassifiedError.message`, go-live check for onboarding URL). Full offline suite and simulation green. See `docs/FINAL_AUDIT_2026-09-09.md` for the executed checks, known limitations and the live-Telegram checklist that still needs real credentials.

## 2026-09-09 — Five architecture decisions (approved)

Recorded in full in `docs/STATUS.md § Decisions`. In short:

1. **Direct mode is a destination capability.** The sender-side Forward/Direct chooser was removed; the receiving owner opts into "⚡ Auto-post" per destination, and only when `relay.auto_post_blocker` finds a legal route. Implemented.
2. **Storage:** delivery audit + roles moved from JSON to one shared SQLite DB (`platform_store.py`, `PLATFORM_DB_PATH`): `DeliveryAudit` does targeted UPDATEs and indexed lookups instead of rewriting a JSON list; `RoleRegistry` runs unchanged on `SharedKV`, so one invite code now carries the whole scope list and works in either bot. Ledger via `MINT_LEDGER_MODE`; sessions stay JSON; Postgres rejected until a measured need exists. Implemented.
3. **Formatting:** broadcast drafts only, captured as `text` + Telegram `MessageEntity[]` from a message the owner sends to the admin bot (`tg_entities.sanitize_entities`), replayed with `entities=` and no `parse_mode`; Mini App shows an escaped preview. Never hand-typed markup, never for ads. Implemented.
4. **Views:** the honest proxy stays. Telethon/MTProto user-session observer (`views_provider.py`) is rejected for this stage and remains unwired.
5. **Docs:** `docs/STATUS.md` is canonical and must change in the same commit as the code; `MASTER_SYSTEM_DOCUMENTATION.md` and `MASTER_PLAN.md` Parts 2–3 are historical.
