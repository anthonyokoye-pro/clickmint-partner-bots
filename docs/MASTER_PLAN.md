# CLICKMINT — Master Plan (Growth + Bot Design + Audit + Handoff)

This is the single source of truth for the whole project: the **growth strategy**, the
**bot system design / directory library**, the **full codebase audit**, and the **handoff
packet** to the next (GitHub-connected) session. Zero budget, hosted free, built to scale.

Read order: Part 1 (growth) → Part 2 (design/directories) → Part 3 (audit) → Part 4 (handoff).

---

## PART 1 — GROWTH PLAN (channel, <700 subs, AI/crypto/airdrop)

The whole ethos: **quality over quantity**, engagement over subscriber count, and your
leverage as a small channel is your **view/reaction rate** (small channels have the highest
in the ecosystem — see the research doc).

### 1.1 The goal
Grow a CLICKMINT network of **earned, engaged partners** — not bought numbers. Each partner
brings real audience, verifiable through the performance engine (view ratio, not subs).

### 1.2 The channel levers (in priority order)
1. **Consistent, useful content** — the #1 lever. Scannable: verified-vs-potential airdrops,
   AI-tool roundups, scam alerts, deadline calendars. This is what makes partners *want* to
   share you and makes users *click* on you.
2. **The partner network itself (the bots)** — cross-promotion is the highest-ROI organic
   tactic. Every partner you add is a compounding exposure channel (30-day retention 40–60%,
   5–15% net growth per exchange; barter ~$0.05–0.20/sub vs paid $0.30–1.00).
3. **Your group** (linked discussion) — the community/value multiplier that makes you a
   *destination*, not a feed.

### 1.3 The 3-tier outreach ladder (do this weekly)
| Tier | Subscriber range | Your offer | Target |
|---|---|---|---|
| **T1 (main)** | 700–2,500, high-engagement, **adjacent-niche** | 1:1 cross-post + content swap | Growth engine, says yes |
| **T2 (stretch)** | 2,500–5,000 | **2:1** (you share twice) + your content value | Break into bigger |
| **T3 (rare)** | 5,000+ | Only with a clear content/complementary fit + strongest offer | Occasional, not a goal |

**The honest rule:** 100K giants won't reply and gain nothing from you. The *quality*
channels at 700–2.5K are where your leverage compounds.

### 1.4 The pitch (value-first, specific)
> Reference a specific post → state audience overlap plainly → give YOUR engagement stats
> (not subs) → propose a concrete format + time → 2:1 if they're bigger → no exclusivity,
> stop if it fails. (Full template in the research doc.)

### 1.5 The terms (what makes a deal "safe")
Post type/format + card, date+time (UTC), pin duration, attribution, measurement (subs gained
+ 30-day retention), volume, renewal, non-exclusivity, no bought/bot engagement. The owner is
the **mediator** — notified on open/renew/close, gives the final close. (Implemented — see Part 2.)

### 1.6 Paid advertising (only when you have budget; NOT now)
- **Not now** — zero budget. Keep it barter/content-based.
- When funded: Telegram **sponsored messages / paid placement** on proven high-engagement
  channels (use the engagement benchmark, not subscriber count), and target the **stars/
  sponsored-message** route for reach. Measure cost-per-sub and 30-day retention before scaling.
- **Never** buy followers/bots — it destroys the engagement ratio that is your entire edge.

### 1.7 North-star metric
**Genuine cross-platform reach:** "how many of MY posts were shared into OTHER channels' feeds
this week" — tracked by the delivery log + performance engine. Growth in *partners and shared
posts* is the signal, not raw subscriber count.

---

## PART 2 — BOT SYSTEM DESIGN (directory library)

> ⚠️ **HISTORICAL (2026-09-04).** Parts 2 and 3 describe the original three-bot/JSON design and the
> audit of that date. Storage, verification, delivery routing, admin surface and test counts have all
> changed since; see **`docs/STATUS.md`** for the current state. Part 1 (growth strategy) and Part 4
> (handoff) remain valid.

This is how the codebase is structured, so the next AI session can read it fast and
accurately (no wasted session/limit).

### 2.1 Directory tree
```
partner_bots/                     <- the repo root (push this to GitHub as the repo)
├── config.py                     <- reads ALL secrets from env vars (never hardcode)
├── core.py                       <- ENGINE: tiering, credit ledger, earn/spend, anti-cheat,
│                                     Contract, DeliveryLog, ReportRegistry, PerformanceEngine
│                                     (score + A/B/C bands + like-with-like match + status demote),
│                                     Distribution (compat wrapper), owner exemption (incl. by id)
├── governance.py                 <- RULES LAYER: post limit (size x performance), SubmissionGate
│                                     (niche category + spam/ads ban + human review), ReviewQueue,
│                                     PartnerContractRegistry (mediator open/renew/close),
│                                     RoleRegistry (owner/scoped-admin/user, invite-code login)
├── views_provider.py             <- OPTIONAL MTProto observer (the only way to read live views);
│                                     falls back to reliability proxy (no fake views)
├── scheduler.py                  <- agreed-time scheduler for DIRECT-mode delivery
├── ui.py                         <- shared button MENUS + role routing
├── store.py                      <- JSON persistence (swap for SQLite/Postgres later)
│
├── reward_bot.py                 <- REWARD bot (aiogram): register, submit w/ terms+category,
│                                     post-style selector, owner bypass, chain/direct delivery,
│                                     reports, partner menu, admin panel, schedule, notify loop
├── partnership_bot.py            <- PARTNERSHIP bot (aiogram): curated partners, terms contract,
│                                     submission gate, review queue, owner/admin panel, notify loop
├── admin_bot.py                  <- ADMIN PANEL (aiogram): owner dashboard, caps, review queue,
│                                     contracts, invite-code generator (reuses the same store files)
│
├── branding.py                   <- the approved BOT_BRANDING copy + a Bot API pusher
│                                     (setMyName / Description / ShortDescription / Commands)
│
├── test_core.py                  <- ENGINE tests (23)
├── test_governance.py            <- RULES + roles + branding tests (33)
├── test_bots.py                  <- BOT WIRING tests (24): real Update objects through each
│                                     dispatcher against a mocked Telegram session
├── requirements.txt              <- aiogram, python-dotenv
├── .env.example                  <- copy to .env (git-ignored) for secrets template
├── .gitignore                    <- keeps secrets + runtime JSON + caches out of git
├── deploy.sh                     <- one-shot deploy from GitHub to a free box (systemd)
├── clickmint.service             <- reward bot auto-start service
├── clickmint-partner.service     <- partnership bot auto-start service
└── .github/workflows/
    ├── tests.yml                 <- runs all THREE suites on every push (CI safety net)
    └── deploy.yml                <- optional auto-deploy on push (SSH to your server)
```

### 2.2 The two-rule foundation (memorize this)
1. **A bot only enforces what it controls.** Direct mode (bot = admin) = full control.
   Chain mode (partner posts manually) = control only at the **submission gate**.
2. **The submission gate** is where niche/spam/ads rules live and are always enforceable. The
   **performance cap** scales with size × performance. The **owner is exempt** (by numeric id).

### 2.3 Data model (all JSON, same files shared by all three bots)
- **Ledger** keyed by `@username`: size, tier, balance, earned/spent, accept/receive types,
  status (ACTIVE/WATCH/RESTRICTED/REMOVED), performance counters (offered/posted/invalid),
  direct_mode, user_id, cap_used_today.
- **reports** / **review_queue** / **partner_contracts** / **scheduled** / **roles_***: per-section.
- Files: `reward_ledger.json` (reward + shared), `partnership_state.json` (partnership).
  **All three bots read the same files** → the admin panel sees the network's live state.

### 2.4 How the flows work (so the next session can reason fast)
- **Register:** `/start @chan <subs>` or `/register @chan <subs>` → tier + onboarding seed
  (+2 credits). Funnel state is per Telegram user id (never global).
- **Member submits:** accept terms → pick niche category → pick style (forward/direct/pin) +
  loud/silent → forward the post → gate check → daily-cap check → pick pair count → chain Agree/
  Skip/Report (or direct auto-post). Borderline posts → human Review queue.
- **Owner submit (bypass):** forward → owner route screen → pick count or "All" → routed to any
  channel, free, uncapped. No terms/category/gate/cap.
- **Partnership:** partner menu → "New partnership" → enter other @username → both must have the
  bot admin → contract opens → owner notified → either side renews/requests close → owner gives
  the final close.
- **Admin:** owner-only `/start` → dashboard → caps / review queue / contracts / invite codes
  / pending-report count. In the reward bot the owner panel also actions reports
  (Dismiss / Warn / Restrict / Remove) — the human step that a report waits for.
- **Roles:** owner (`OWNER_USER_ID`) / scoped-admin (reward/partnership/both via one-time code
  redeemed with `/adminlogin <CODE>`) / user.

### 2.5 Verified Telegram facts baked in
- Channel **pin via API is always silent**; loud-pin is group-only. Loud/silent posting works.
- Bot posting requires **admin + Post Messages** (direct mode) — set via BotFather/admin per channel.
- Rate limits: ~30 msgs/sec global, ~1/sec per chat, ~20/min per group/channel → **queue**, don't burst.
- Username **cannot be changed later**; 5–32 chars, ends in "bot", unique.

---

## PART 3 — FULL CODEBASE AUDIT

Runs: `python3 test_core.py` + `python3 test_governance.py` + `py_compile *.py` + handler wiring.

### 3.1 Current status (verified 2026-09-04, second pass)
| Check | Result |
|---|---|
| Compile all modules | **PASS** |
| Core engine tests | **23/23** (`ALL TESTS PASSED (23)`) |
| Governance + roles + branding tests | **33/33** |
| Bot wiring tests (new) | **24/24** |
| All 3 bots import + register handlers | PASS (reward 10 msg/13 cb, partner 5/7, admin 2/3) |
| Secrets in env, not code | PASS (`config.py`, `.gitignore` protects `.env` + runtime JSON) |

### 3.2 What the second pass found (full detail in `docs/AUDIT_REPORT.md`)
The first audit tested the **engine** and never the **Telegram layer** — which is where the
real defects were. Headlines:

**Critical (the bots could not run):**
1. Every inline button was built with a positional argument — aiogram 3 raises `TypeError`,
   so every menu crashed. 82 call sites fixed.
2. The `s:` callback decorator sat on a helper, so `pick_spend` was never registered:
   tapping 1–5 distributed nothing.
3. `/start @chan <subs>` was advertised but never parsed — nobody could register.
4. `admin_bot` used `types.Message` as a filter, so the dashboard never opened.
5. A catch-all message handler swallowed `/rank`, `/audit`, `/reports`, `/schedule`.

**High (economy / caps / moderation):**
6. "Agree" credited the **sender** instead of the sharer — broadcasting your own post minted
   credits. `/agree` was an open credit printer; removed.
7. The daily cap was charged to receivers and never to the sender → the size × performance
   cap applied to nobody. Cap accounting moved into `CreditLedger` (UTC, atomic).
8. Credits were charged before targets were known → members paid for undelivered pairs.
9. A report's post-context overwrote the report's own `status`/`sender`/`id`, so reports
   could vanish from the pending queue; and **no bot surface ever confirmed a report** —
   the human step had no interface. Both fixed (🚩 panel with Dismiss/Warn/Restrict/Remove).
10. Matching ignored the target's `receive_types` (off-niche offers).

**Medium (state / storage / time / robustness):** global funnel state clobbered between
concurrent members; `JsonStore` had no atomic write, no lock and no merge (three bots share
those files); `/schedule` parsed "UTC" in server-local time; a transient delivery error
dropped the slot; un-attributed placeholder posts violated forward-only; the gate ignored
captions; substring matching hard-blocked innocent words; the partnership bot's report
button filed nothing and its cap was never enforced; admin Back button shadowed; five panel
buttons had no handler; branding copy exceeded Telegram's 512-char limit.

**Test-suite defects:** `test_core.py` had three stacked runners (suite ran 3× with stale
lists, printed no count, always exited 0); `test_governance.py` exited 0 even on failure;
there were no tests for the bot layer at all. All three are fixed, and `test_bots.py` is new.

### 3.3 What's done well (don't touch / reuse)
- Pure, offline-testable engine split from Telegram wiring (easy to test, no network).
- Forward-only rule (attribution kept), owner exemption, performance-over-size matching,
  report system (manual review, no auto-ban), scoped admin, mediation on contracts.
- Consistent teal/mint branding + matching logos for the 3 bots.

### 3.4 Known limitations to carry forward (honest)
| Limitation | Why | Plan |
|---|---|---|
| **No real channel views** without `views_provider.py` (MTProto) | Bot API can't read views; the engine degrades to a reliability proxy (no fake views). | Wire the optional Telethon observer when you're ready; until then scores use the proxy. |
| **JSON storage** (not a DB) | Fine for hundreds of users; write-locking at scale. | Migrate to SQLite (free, same host) when you hit locks; Postgres later on real demand. |
| **Chain mode relies on the partner actually posting** | Bot can't control a manual post after handoff. | Use direct mode (bot = admin) where you want guaranteed delivery. |
| **No auto-ban on a single keyword** | False positives kill the network. | Borderline → human Review queue (by design). |
| **Human recruitment isn't automated** | The bots handle post-"yes" mechanics, not getting the yes. | That's your daily outreach (Part 1.4). |

### 3.5 Next-session testing checklist (so the new AI session doesn't waste limit)
1. `pip install -r requirements.txt`
2. `python3 -m py_compile *.py`
3. `python3 test_core.py`  → expect **ALL TESTS PASSED (23)**
4. `python3 test_governance.py`  → expect **33/33**
5. `python3 test_bots.py`  → expect **ALL BOT WIRING TESTS PASSED (24)**
6. `python3 branding.py --check`  → every field within Telegram's limits
7. Import check (patching tokens): all 3 bots register handlers without error.
8. Set `OWNER_USER_ID` + tokens in `.env`; run one bot locally; confirm `/start` menu branches.

---

## PART 4 — HANDOFF TO THE NEXT (GITHUB-CONNECTED) SESSION

### 4.1 Can everything happen in one chat session? Honest answer
- **You** must push to GitHub (I have no GitHub credentials). I've prepped the repo and
  provided the exact commands (see `DEPLOY_FROM_GITHUB.md`).
- The plan + design + audit in THIS file are **carry-over ready** — paste/share this file with
  the next session so nothing is re-derived and no limit is wasted.

### 4.2 What to push now (exact commands, in the repo dir)
```bash
cd partner_bots
git init && git add -A && git commit -m "CLICKMINT partner bots v1"
git branch -M main
# create a GitHub repo (name it clickmint-bots or clickmint-partner-bots; public or private)
git remote add origin https://github.com/<YOUR_USERNAME>/clickmint-bots.git
git push -u origin main
```
> Secrets are NOT in the repo (env vars only). Runtime JSON is git-ignored. `.env.example`
> is committed as a template. Safe to push.

### 4.3 What to paste into the next session (a short prompt)
> "Audit this repo. It's CLICKMINT, a zero-budget Telegram partner/reward network at
> `<repo>`. Read `docs/MASTER_PLAN.md` Parts 2 & 3 to understand the design and the
> current audited state. Run `python3 -m py_compile *.py`, `python3 test_core.py`
> (expect ALL TESTS PASSED (23)), `python3 test_governance.py` (expect 33/33),
> `python3 test_bots.py` (expect 24/24 bot wiring) and confirm all three bots
> (reward_bot, partnership_bot, admin_bot) import and register handlers. Then find and fix
> any remaining bugs, missing tests, and improve robustness — respecting: no auto-ban, no
> fake views, forward-only, quality-over-size, owner exemption, scoped-admin least privilege,
> zero budget, free hosting, and the branding in `docs/BOT_BRANDING.md`."

> **Note for the next session:** anything touching the Telegram layer must come with a
> `test_bots.py` case. The first audit was green on the engine while every menu in every bot
> raised `TypeError` — an engine-only suite cannot see that class of bug.

### 4.4 The "green of the channel" / paid-ads context to carry over
This was covered in the session: quality-over-size outreach, the 3-tier ladder, the engagement
benchmarks, the 2:1 asymmetric offer, contract terms, and the rule **don't pay for ads /
subscribers until the network earns it** — then use sponsored messages + measured cost-per-sub.
All saved in `CLICKMINT_Quality_Partnership_Research.md` and Part 1 above.

---

## Bottom line
- **Growth plan** (Part 1): quality partners, engagement leverage, 3-tier ladder, 2:1 offers.
- **Design** (Part 2): a clean, testable, 3-bot system on free hosting, all secrets in env.
- **Audit** (Part 3): 23/23 + 33/33 + 24/24 green. The second pass found 5 critical
  (bots literally could not render a menu or route a post), 7 high (credit economy, caps,
  report loop) and 17 medium issues — all fixed, all covered by tests.
- **Handoff** (Part 4): a ready repo to push + a ready prompt so the next session starts from
  verified state and doesn't burn its limit.

## 2026-09 compliance-first continuation

Before financial or growth expansion, the platform must complete:

- Admin enforcement/report/evidence/timeline UI and appeal workflow;
- safe-mode wiring into every broadcast, task, registration, reward, and posting worker;
- campaign title/edit/delete lifecycle and separate consent-aware Ad Campaign model;
- Telegram-supported formatting editor using message entities/parse modes, never invented markup;
- cross-platform persistence/concurrency verification and a real requirements-installed bot test run;
- named HTTPS deployment only when a stable domain/host is available.

Quick Cloudflare tunnels remain development/testing infrastructure only. Financial features
remain disabled until these safety and operational controls are validated.

## Future AI intelligence and anti-bot roadmap

AI is an optional intelligence layer, never a core dependency. The staged path is
Observe → Propose → Test → Controlled implementation → Limited automation. High-impact
security, enforcement, authentication, financial, and production changes require human
approval. The knowledge base should remain versioned documentation, structured events,
SQLite records, and engineering decision records until scale justifies search/indexing
infrastructure.

Human verification is risk-based, not universal. Telegram identity, rate limits, behavior,
reputation, enforcement, and a replaceable provider result must be combined. Turnstile is a
candidate for a free provider, but it must be validated server-side, single-use, and
short-lived; it is not proof of innocence or humanity. Do not challenge normal read-only
navigation or every Mini App session.
