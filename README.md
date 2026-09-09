# CLICKMINT Partner Bots

Three bots (reward · partnership · owner admin panel) sharing one verified engine
(`core.py` + `governance.py`). Zero budget, free hosting, JSON store.

**Status:** `python3 test_core.py` 28/28 · `python3 test_governance.py` 33/33 ·
`python3 test_bots.py` 26/26 (bot wiring) · `python3 simulate.py` clean dry run.
See `docs/AUDIT_REPORT.md` and the readable transcript in `docs/DRY_RUN.md`.

**New here / not comfortable with the terminal?** Read **[START_HERE.md](START_HERE.md)** —
it walks the whole setup in plain language. Short version: `bash setup.sh` asks for your
three bot tokens and your Telegram id, then `bash run_bots.sh start` turns the bots on.

## 0. Important reality check (read first)
- **A bot can only post into a channel where it is an ADMIN with "Post Messages."**
  There is no legitimate way around channel posting permission.
- So distribution is by: **(A) chain accept/reject** (default — the target taps Agree and
  posts it into their own channel; no admin needed), OR **(B) direct** (a channel owner
  grants your bot admin, then the bot posts straight at the agreed time — true automation).
- Both delivery modes are supported. For "auto-post at agreed time" on YOUR side you can
  use Telegram's built-in scheduler or a scheduler bot; for a PARTNER's channel you need
  their admin grant (option B).

## 1. The reward / exchange bot (`reward_bot.py`)
Tiered network, strict 1:1 credits, accept/reject chain.
- **Earn** = share another's post (+1 credit). **Spend** = have yours spread.
- **Earn-first:** must share ≥1 before your posts are spread.
- **Anti-cheat:** 1 credit = 1 (post → channel) pair. Sending ONE post to 5 channels = 5 credits.
- **Quality over size:** targets are matched **like-with-like by performance band**
  (A/B/C), and only to channels whose contract accepts that category. Subscriber size is
  only a tiebreaker. Size tiers still exist (`tier_for_size`) but are *not* the match key.
- **Daily post cap = size × performance**, charged to the **sender**, reset at 00:00 UTC.
- **+2 onboarding seed** on join.
- **Owner (CLICKMINT) is exempt** — no funnel, no credits, no cap; can route anywhere.

Commands: `/start @chan 1200` · `/register @chan 1200` · `/balance` · `/rank` · `/audit` ·
`/reports` · `/schedule` · `/appeal <why>` · `/report @target <why>` (or reply `/report <why>` inside a
registered group) · `/ads` (advertising consent, see `docs/ADVERTISING.md`) · `/adminlogin <CODE>` ·
or just **forward a post** to distribute.
(There is no `/agree` command — agreeing happens on the offer's buttons, so the credit
goes to the channel that actually shares someone else's post.)

## 2. The partnership bot (`partnership_bot.py`)
For the curated MAIN partners you agreed terms with.
- `/start @chan 1200` register · `/contract` set your post types (ACCEPT + RECEIVE).
- Forward a post → offered to partners whose RECEIVE contract matches.
- Each target gets [✅ Agree / ❌ Disagree] → chain moves on automatically.
- **Owner exempt** — posts to any partner channel freely.

## 3. Setup (secrets in ENV vars, not in the code)
```bash
pip install -r requirements.txt
# create three bots in @BotFather -> get three tokens
cp .env.example .env            # then fill in REWARD/PARTNER/ADMIN tokens + OWNER_USER_ID
python reward_bot.py            # run the reward bot
python partnership_bot.py       # run the partnership bot
python admin_bot.py             # run the admin panel bot
```
All secrets are read from env vars (`config.py`; `.env` auto-loaded, git-ignored). See
`DEPLOY_FROM_GITHUB.md` to host this repo on GitHub and deploy from there (CI tests +
auto-deploy).

## 4. Verify everything (no Telegram, no network)
```bash
python3 -m py_compile *.py     # compile check
python3 test_core.py           # ALL TESTS PASSED (28)  — engine
python3 test_governance.py     # 33/33 governance tests passed — rules & roles
python3 test_bots.py           # ALL BOT WIRING TESTS PASSED (26) — handlers/menus/funnel
python3 simulate.py            # scripted dry run: prints every screen, asserts every rule
```
Or just `./run_tests.sh`, which runs all of the above plus `python3 branding.py --check`.

`simulate.py` is the human-readable counterpart: same machinery, but it prints each screen and
clicks buttons **by their label**, so a dead button or a missing menu entry fails the run.
`test_bots.py` drives real Telegram Update objects through each bot's dispatcher against a
mocked session, so a dead handler, an unrenderable menu or a broken funnel is caught before
deploy.

> **CI note:** `.github/workflows/tests.yml` in this working tree already runs all four
> checks, but that file is **not pushed** — GitHub refuses workflow edits from the
> assistant's app ("without `workflows` permission"). Commit it yourself:
> ```bash
> git add .github/workflows/tests.yml
> git commit -m "CI: run the bot wiring suite + branding check"
> git push
> ```
> (Or paste the same two steps into the file via GitHub's web editor.)

## 5. Files
| File | Purpose |
|---|---|
| `core.py` | The engine: tiering, credit ledger, earn/spend, anti-cheat, **report system**, **performance engine**, contract, distribution. Verified. |
| `store.py` | Legacy JSON persistence for existing network state. |
| `mint_ledger.py` | Transactional Mint/referral/posting foundation backed by SQLite; migrate to PostgreSQL before scale. |
| `migrate_mint.py` | Additive migration and balance verification from the legacy JSON ledger. |
| `reward_bot.py` | aiogram wiring for reward bot. |
| `partnership_bot.py` | aiogram wiring for partnership bot. |
| `governance.py` | Rules layer: daily cap, submission gate, review queue, partner contracts, roles. |
| `admin_bot.py` | aiogram wiring for the owner's admin panel. |
| `scheduler.py` | Agreed-time delivery queue (UTC), with retries. |
| `ui.py` | Shared button menus + role routing. |
| `branding.py` | The approved branding copy (`docs/BOT_BRANDING.md`) + a pusher for the Bot API. |
| `views_provider.py` | **Optional** MTProto observer — the only way to read live channel views. |
| `telegram_verification.py` | User-owned Telegram bot credential storage, Bot API verification, and real member-count retrieval. |
| `test_core.py` | Engine tests (28). |
| `test_governance.py` | Rules/roles/branding tests (33). |
| `test_bots.py` | Bot wiring tests (26) — handlers, menus, funnel, roles, owner bypass. |
| `simulate.py` | Offline dry run: plays a 19-step multi-user session and prints the whole conversation. |
| `preflight.py` | Checks your tokens/owner id/store before launch, and confirms each token with Telegram. |
| `setup.sh` | Guided first-time setup: asks four questions, writes `.env`, installs deps, verifies. |
| `run_bots.sh` | start / stop / status / logs for all three bots at once. |
| `START_HERE.md` | Beginner-friendly setup guide (no terminal experience assumed). |

## 6. Report system & performance engine (added)
- **Report button** on every offered post: receiver flags scam/fraud. The bot never auto-bans —
  the report is filed as `pending` and a human decides in the owner/admin panel
  (🚩 Pending reports → Dismiss / Warn / Restrict / Remove). `/reports` lists them.
- **Performance engine** replaces subscriber-size matching. Score = reach ratio + engagement +
  reliability + reputation. Bands A/B/C; match **like-with-like**; promote/demote as scores change;
  subscriber size is only a tiebreaker. Real views need `views_provider.py` (MTProto); without it
  the engine uses the reliability proxy (no fake views).
- **`/rank`** shows the live score/performance-tier/status per channel; the user-facing menu label is `🏆 Leaderboard`.
- **User-owned Telegram verification:** each destination owner connects their own BotFather bot with `/connectbot`, adds it as an administrator, and registers with `/register @destination`. Telegram supplies member counts; manual counts are not accepted.
- **Fail-closed participation:** destinations receive zero participation capacity until bot identity, administrator status, permissions, accessibility, eligibility, and active state are verified.

## 7. Governance features (governance.py — 33 tests)
The terms & limits + roles you asked for. Pure logic in `governance.py`, tested by `test_governance.py`.
- **Post limit scales with size × performance after verification.** `daily_post_cap(size, band, status, is_owner, connected)`:
  unverified destinations receive 0 slots; verified size base (1–4 slots) × performance multiplier
  (A=1.0, B=0.75, C=0.5) applies a floor of 1; RESTRICTED/REMOVED = 0; owner is unlimited only
  after verification. No unconnected participation allowance remains.
- **Submission gate (terms & regulations):** sender must accept the terms (button), declare a
  **niche category** (from `POST_CATEGORIES`), and the category must be one the target channel
  agreed to *receive*. Hard-block list catches scam / money-asking / fraud / phishing / free-stuff
  spam / **third-party ad-service posts**. Ambiguous posts go to a **human Review queue** (never
  an instant auto-ban). Verified by `test_hard_block_patterns`, `test_soft_review_patterns_route_to_human`.
- **Attribution tip (advice only):** "add your @username at the bottom" — never required.
- **Peer-to-peer partnership** with owner as **mediator/registrar**: owner is notified on open,
  renew, and close; a close is **only final once the owner confirms it** (`request_close` →
  `finalize_close`). Contract fields = type, volume, schedule, delivery pref, duration, terms, exit.
- **Post style selector:** forward / direct / pin + **loud / silent**.
  ⚠️ Telegram fact: a **channel pin is ALWAYS silent** via the API; loud-pin only works in a group.
- **Button menus everywhere** (commands still work). `/start` branches by role.
- **Roles + admin login:** owner / scoped-admin / user. An admin **cannot self-request** — they
  contact you, you **generate a one-time invite code**, they `/adminlogin <CODE>` to unlock a
  **scoped** admin menu (reward / partnership / both). Owner is the only one who grants codes.

Commands kept for power users: `/rank`, `/audit`, `/reports`, `/balance`, `/schedule`,
`/adminlogin`.

## 8. Admin panel bot (`admin_bot.py`) + review notifications (added)
A **third bot** that is NOT extra hosting — it reads the *same* JSON store files the reward
and partnership bots write, so it's another process on the same free machine.
- **Live dashboard** by button: registered channels, review-queue counts, open contracts.
- **Daily caps** panel: every channel's capped slots (size × performance).
- **Review queue** (both networks): approve / reject a queued post by button. The matching
  network bot then **DMs the sender the outcome** (`notify_loop`), because a bot can only
  message users it has chatted with — so the sender notification is sent by the network bot,
  not the admin bot.
- **Partnership contracts** panel: active / close-requested / closed.
- **Admin logins**: generate ONE-TIME invite codes by button (reward / partnership / both),
  written into the correct store so `/adminlogin <CODE>` works in the right bot.

**Admin login flow (both network bots):** an admin contacts the owner → owner generates a
one-time code in the admin panel → admin runs `/adminlogin <CODE>` → gets a **scoped** admin
menu (`reward` / `partnership` / `both`). Only the owner grants codes.

Fill in: `BOT_TOKEN` + `OWNER_USER_ID` in all three bots. The admin panel needs the same
store paths as the network bots (`reward_ledger.json`, `partnership_state.json`).

## Docs (planning, audit, research, branding)
The full project documentation lives in `docs/` — read this before changing code:
- `docs/MASTER_PLAN.md` — growth plan + bot design/directory + audit + next-session handoff
- `docs/AUDIT_REPORT.md` — the formal full codebase audit (what's verified, what was fixed)
- `docs/PARTNERSHIP_RESEARCH.md` — growth/partner research (quality filter, 2:1 offer, terms, paid-ads)
- `docs/BOT_BRANDING.md` — bot names, usernames, About, Description, and images (in `docs/`)
- `docs/GITHUB_PUSH.md` — step-by-step Git Bash push guide
- `DEPLOY_FREE.md` (repo root) — run everything for $0 (free hosting, SQLite vs Postgres)

## 9. Before you run live
- Replace `YOUR_BOT_TOKEN` with your real tokens, and set `OWNER_USER_ID` (your numeric Telegram id)
  in **both** bots — this is what splits the Owner vs Admin vs User menus.
- Add each bot as **admin (Post Messages)** in your own channel so it can post for you.
- For partners who want true automation, have them add your bot as admin in their channel.
- Keep volume low and always respect a partner's choice — this is a trust network, not a spam tool.

## Compliance and enforcement

CLICKMINT is compliance-first: it verifies Telegram permissions before direct posting,
validates Mini App initData server-side, keeps borderline content in human review, and
never treats one report or keyword as proof of wrongdoing. The durable enforcement,
report, evidence, timeline, and safe-mode foundation is in `enforcement.py`; its policy and
current gaps are documented in `docs/COMPLIANCE_AND_ENFORCEMENT.md`. Revenue, deposits,
Boost purchases, withdrawals, and external payouts remain disabled.
