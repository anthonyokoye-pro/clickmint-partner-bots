# CLICKMINT — Status (canonical)

_Last updated: 2026-09-09 · branch `arena/01a087db-clickmint-partner-bots`._
This file is the single place that says what is real. Update it in the same commit as any change.

## Vocabulary
**Implemented** = in code, tested offline · **Partial** = some of it · **Pending** = agreed, not built ·
**Approved** = decision taken, work not started · **Recommended** = proposed, awaiting decision ·
**Researched / Discussed** = documented only · **Rejected** = do not build · **Unknown** = not determinable.

## Standing constraints (never relax)
User-owned bot architecture · tokens never exposed/echoed/logged · admin rights required where participation
depends on them · Telegram API data over manual stats · cancelled/deleted campaigns never queued · only
Telegram-supported formatting (entities) · Mint / Boost / external rewards kept separate · deposits,
withdrawals, ads billing, advanced AI, autonomous engineering are approval-gated · server-side initData,
audit logs, governance, safety, consent, anti-abuse preserved · no auto-ban from keywords or report counts.

## Status matrix

| Area | Status | Where |
|---|---|---|
| Three polling bots (reward / partnership / admin) | Implemented | `*_bot.py` |
| Admin Mini App + WSGI server, initData verified server-side | Implemented | `admin_server.py`, `admin_http.py`, `admin_api.py` |
| User-owned bot verification (getChat/getChatMember/member count) | Implemented | `telegram_verification.py` |
| Destination state machine, history, illegal-edge rejection | Implemented | `destination_state.py` |
| Telegram error classification + backoff | Implemented | `destination_state.py` |
| Inline Re-verify / Details / Remove per destination | Implemented | `/mychannels` both bots |
| Scheduled background re-verification (per-state interval, batch, safe-mode aware) | Implemented | `_background_reverify` both bots |
| Relay routing: platform / owner_origin / fail-closed; strict forward mock | Implemented | `relay.py`, `test_relay.py` |
| Direct mode as **destination capability** (owner opt-in ⚡ Auto-post in `/mychannels`, only with a legal route; sender no longer chooses) | Implemented | `relay.auto_post_blocker`, `_auto_post_enabled`, `test_relay.py`, `test_bots.py` |
| Shared SQLite verification DB (destinations + encrypted credentials), JSON migration | Implemented | `verification_store.py` |
| Delivery audit log + roles → shared SQLite (`PLATFORM_DB_PATH`, one-time JSON migration, one role table for all bots) | Implemented | `platform_store.py`, `test_platform_store.py` |
| Sessions stay JSON; ledger via `MINT_LEDGER_MODE=transactional`; Postgres | Rejected for now | decision #2 |
| MINT ledger: transactional SQLite mode | Partial (`MINT_LEDGER_MODE`) | `mint_ledger.py` |
| Sessions in JSON | Implemented, stays | — |
| PostgreSQL | **Rejected until measured need** | decision #2 |
| Web App token onboarding (HTTPS, initData, AES-GCM, redaction) | Implemented; live HTTPS check Pending | `onboarding_api.py`, `onboarding_web/` |
| Enforcement gate, reports, evidence, appeals, safe mode | Implemented | `enforcement.py`, `enforcement_gate.py` |
| Consent-aware advertising, kill switch default off, labelled delivery | Implemented | `ad_campaigns.py` |
| Ads billing / pricing / payouts | **Rejected for now** (approval-gated) | — |
| Broadcast lifecycle; cancelled campaigns cannot be queued/claimed | Implemented | `broadcast_queue.py` |
| Broadcast drafts with Telegram entities (owner composes in Telegram → admin bot captures `text`+`entities` → worker sends with `entities=`, no parse_mode; Mini App preview) | Implemented | `tg_entities.py`, `admin_bot._capture_broadcast_draft`, `test_tg_entities.py`, `test_bots.py` |
| Rich formatting for ads | **Rejected** | decision #3 |
| Economics model | Implemented (modelling only) | `economics.py` |
| Deposits / withdrawals / Boost purchase / Stars / TON / payouts | Pending, approval-gated — **not implemented** | `ECONOMICS.md` |
| Boost | Discussed only (`credibility.distribution_radius` is a pure function; no issuance) | — |
| Live channel views via MTProto user session | **Rejected for current stage** | decision #4 |
| Live Telegram integration tests | Pending — needs throwaway token + test channel | `FINAL_AUDIT_2026-09-09.md` |
| AI layer, autonomous engineering, universal human-verification | Discussed / Researched | `MASTER_PLAN.md` |
| Auto-ban on keyword/report count; copy-rebuilt forwards; account rotation | **Rejected** | — |

## Decisions record — 2026-09-09 (all five approved)
1. **Direct mode** is a per-destination capability the *receiving owner* enables, only when `relay.resolve`
   has a legal route; the sender no longer chooses "direct". Rationale: the sender cannot see admin
   rights; recommending the platform bot as admin everywhere re-centralises posting.
2. **Storage**: migrate delivery audit + roles to SQLite; ledger via existing `MINT_LEDGER_MODE` cut-over;
   sessions stay JSON; Postgres rejected until a measured need.
3. **Formatting**: broadcast drafts only, captured as `text`+`MessageEntity[]` from a message the admin
   sends to the admin bot; no hand-typed HTML/MarkdownV2; not for ads.
4. **Views**: keep the honest proxy; no Telethon/MTProto user session (account-takeover risk, ToS profile).
5. **Docs**: `MASTER_SYSTEM_DOCUMENTATION.md` and `MASTER_PLAN.md` Parts 2–3 demoted to historical;
   this file + topic docs are canonical.

## Test counts (offline, `run_all_tests.py`)
core 28 · governance 34 · bot wiring 38 · ads 6 · destination state 5 · relay 9 · verification store 4 · platform store 3 · tg entities 2 ·
onboarding 5 · transactional mint 10 · plus admin/enforcement/feature/economics/credibility suites · `simulate.py` clean.
