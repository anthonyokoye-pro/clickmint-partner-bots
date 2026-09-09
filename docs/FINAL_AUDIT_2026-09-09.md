# Final UX, security and live-Telegram audit — 2026-09-09

Scope: everything landed on `arena/01a087db-clickmint-partner-bots` since `d9fcb6d`.
Offline portions were executed in this session; the live portion is a checklist that
requires real credentials and is intentionally **not** marked done.

## Offline audit — executed

| Area | Check | Result |
|---|---|---|
| Test suite | `run_all_tests.py` (core 28, wiring 37, ads 6, state 5, relay 8, verification store 4, onboarding 5, admin/mint/governance suites) | all green |
| Simulation | `simulate.py` full member journey | clean |
| State integrity | no `verified_state="…"` literal writes remain in any bot | confirmed by grep |
| Relay | regression test fails against the pre-relay code (verified by reverting the resolver) | confirmed |
| Secret hygiene | bot-token shapes redacted in: onboarding errors/audit, connect failures in both bots, `classify_telegram_error` output (aiogram network errors embed the API URL, which contains the token) | fixed this session |
| Cross-process consistency | shared SQLite store invalidates its cache on `PRAGMA data_version` change (bot A sees bot B's writes without restart) | tested |
| Ownership scoping | `dest:*` callbacks resolve rows via `channels.mine(uid)` only; onboarding identity from signed `initData` only | tested |
| Enforcement gate | re-verify tap and Web App connect both consult the gate; background worker pauses in safe mode | tested |
| Fail-closed defaults | no relay route → no post + audit; degraded destination ≠ platform-admin route; unknown Telegram errors do not change state | tested |
| Go-live | `token_onboarding` check added (HTTPS + allowed origin; unset = warning) | tested |

### Findings fixed during the audit
1. `partnership_bot` connect failure echoed the raw exception (could include a token in a URL) → redacted.
2. `ClassifiedError.message` carried raw exception text → redacted at the source so every consumer is covered.
3. Go-live check did not know about the onboarding URL → added.

### Known limitations (documented, not hidden)
- ~~Ledger, audit, roles and sessions still live in per-bot JSON.~~ *Updated later the same day:*
  audit + roles moved to `platform.sqlite3` (`platform_store.py`); sessions stay JSON by decision;
  ledger moves via `MINT_LEDGER_MODE=transactional`.
- `relay.destination_from_registry` infers "platform bot is admin" from
  `telegram_bot_id == platform bot id`. Owners who add the platform bot manually without
  re-verifying will resolve to `unavailable` until they tap **Re-verify**.
- `platform_may_try=True` (chain-agree and legacy `/schedule`) attempts the platform route
  without proof of admin rights; Telegram's answer is classified and stored, so a wrong guess
  is visible in `last_error_kind`, not silent.
- Background re-verification cadence is per-state only; there is no global rate budget across
  many owners' bots. At >1k destinations add a token-bucket per owner bot.

## Live Telegram audit — checklist (requires real credentials)

Do these in order with a **throwaway** bot token and a private test channel + test group.
Never use the production owner token.

1. **Onboarding page** — set `ONBOARDING_WEBAPP_URL`, run `admin_server.py` behind HTTPS,
   send `/connectbot` in the reward bot. Expect the 🔐 button; paste the throwaway token;
   confirm `verification.sqlite3` has one row and the token does not appear in any log.
2. **Cross-bot ownership** — open the partnership bot, `/botstatus` should show the same bot.
   From a second Telegram account try to connect the same token → refused.
3. **Register + verify** — add the throwaway bot as admin of the test channel; `/register
   @channel`. Expect `VERIFIED`. Remove admin rights → wait ≤30 min or tap **Re-verify** →
   expect `DEGRADED` with `not_enough_rights`, and a DM only on the status change.
4. **Kick + revoke** — kick the bot from the channel → `DISCONNECTED` (`bot_kicked`). Revoke the
   token in BotFather → next check yields `REVOKED` (`token_revoked`) and the worker stops
   probing it (interval = never).
5. **Relay route 1** — make the *platform* bot admin of the test channel, register it with the
   platform bot's id; submit a forwarded post from another account; direct delivery should
   succeed with `relay="platform"` in the audit row.
6. **Relay route 2** — from the test channel, forward one of its own posts into the reward bot
   with the owner account; target a second destination owned by the same account; expect
   `relay="owner_origin"`.
7. **Relay fail-closed** — target a destination whose owner bot is *not* admin of the origin
   and where the platform bot is not admin; expect no post, the "isn't possible" message, and
   `relay="unavailable"`.
8. **Flood behaviour** — trigger a 429 (e.g. tight loop of Re-verify taps); confirm
   `last_error_kind=flood`, state unchanged, and `retry_after` honoured in logs.
9. **Safe mode** — enable from the admin Mini App; confirm the worker stops probing and members
   get the safe-mode message on Re-verify.

Record outcomes in `docs/ENGINEERING_HISTORY.md` under a new dated heading.
