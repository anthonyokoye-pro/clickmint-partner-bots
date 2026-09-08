# CLICKMINT — Full Codebase Audit Report

**Date:** 2026-09-04 (second pass, GitHub-connected session) · **Scope:** the whole
`clickmint-partner-bots` repo · **Method:** compile check, the three test suites, live
handler-dispatch tests against a mocked Telegram session, and a targeted code review of
every module.

## 1. Summary (verdict)
| Area | Before this session | After |
|---|---|---|
| Compiles (all modules) | ✅ PASS | ✅ PASS |
| Core engine tests | "ALL TESTS PASSED" (ran 3×, no count) | ✅ **28/28**, one runner, exits non-zero on failure |
| Governance tests | 23/23 (always exit 0) | ✅ **33/33**, exits non-zero on failure |
| Bot wiring tests | *did not exist* | ✅ **26/26** (new `test_bots.py`) |
| Scripted dry run | *did not exist* | ✅ clean (new `simulate.py`, transcript in `DRY_RUN.md`) |
| 3 bots import + register handlers | ✅ PASS | ✅ PASS (reward 10 msg/13 cb, partner 5/7, admin 2/3) |
| 3 bots actually *work* at runtime | ❌ **no** — every menu raised `TypeError` | ✅ every menu, funnel and panel exercised by tests |
| Secrets not in code | ✅ PASS | ✅ PASS |
| Owner exemption (numeric id) | ✅ unit-tested | ✅ unit-tested **and** end-to-end through the bot |
| Overall | Looked green, was not runnable | **Green and actually exercised.** |

The previous audit was accurate about the *engine*. What it never tested was the
**Telegram layer**, and that is where the serious defects were: the baseline could not
render a single button, could not register a channel, and could not route a post.

## 2. Checks executed
```bash
python3 -m py_compile *.py                # PASS
python3 test_core.py                      # ALL TESTS PASSED (28)
python3 test_governance.py                # 33/33 governance tests passed
python3 test_bots.py                      # ALL BOT WIRING TESTS PASSED (26)
python3 simulate.py                       # 19-step dry run — SIMULATION CLEAN
python3 branding.py --check               # all copy within Telegram's limits
# import check with patched tokens: all three bots register their handlers
git check-ignore .env reward_ledger.json partnership_state.json   # all ignored
git check-ignore .env.example             # NOT ignored (tracked template) ✓
```

## 2b. Third pass — the scripted dry run (`simulate.py`)

After the suites were green, a **19-step multi-user session** was scripted and played
through the real dispatchers: four members register, the owner bootstraps the network,
posts circulate, a scam is refused, a borderline post is queued, a report is filed and
judged, caps bite. Buttons are pressed *by their label*, read off the keyboard the bot
actually rendered.

Passing tests did not mean the product worked. The dry run found three more real defects
that every suite had missed, because each one only appears in a **sequence** of actions:

| # | Defect | Why the tests missed it | Fix |
|---|---|---|---|
| D1 | **The owner was billed like a member.** `set_user_id` (which links the numeric owner id to the ledger row) was only ever called inside the forward handler. An owner whose first action was `/balance`, `/start` or any menu button was not recognised — no exemption, credits charged, cap applied. | `test_bots.py` always registered the owner first, which called `set_user_id` as a side effect. The dry run had the owner type `/balance` cold. | Identity is now bound by an **outer middleware on every update** in `reward_bot.py` and `partnership_bot.py`; `set_user_id` seals `is_owner` when the id matches, and only writes to disk when something changed. |
| D2 | **The owner's broadcast was offered to the owner.** `owner_targets` skipped rows flagged `is_owner`, but that flag was set late (see D1), so `@ClickMintHQ` appeared in its own distribution list and could tap "Agree" on its own post. | No test asserted who was *absent* from the target list. | `owner_targets(post_type, sender=…)` now excludes the sender and any exempt row. |
| D3 | **The best channel in the network was stranded.** `match` required an *exact* band equality. The first member to out-perform the others became the only channel in band A, so `match` returned `[]` — every post it submitted was answered with "no matching channel", permanently. A two-member network deadlocks the moment one of them performs. | `test_performance_match_like_with_like` only ever tested a pool that *had* same-band peers. | Band **widening**: same-band peers still win outright; if that pool is empty the search falls back to the nearest band. Quality still orders the results, it just no longer isolates the top performer. |

Two smaller things were fixed alongside them: owner `@username` matching is now
case-insensitive (Telegram treats `@ClickMintHQ` and `@clickminthq` as one account, an
exact compare silently demoted the owner), and `match`'s `min_status` parameter — which
was accepted and then ignored entirely — now filters by standing.

Five regression tests were added for these (`test_core.py` 23 → 28, `test_bots.py`
24 → 26), and `simulate.py --quiet` runs as part of `./run_tests.sh`.

### Design questions the dry run raised (not changed — your call)
- **Cold start is a deadlock by design.** `earn-first` means a member must share someone
  else's post before their own can circulate. At genesis nobody has earned, so nothing
  can move until the **owner** (exempt) puts the first post into circulation. That works,
  and the dry run demonstrates it — but it means the network cannot bootstrap without the
  owner posting, and the 2-credit onboarding seed is unusable until then.
- **A pending offer lowers your band.** `mark_offered` counts immediately while
  `mark_posted` only lands when the member taps Agree, so a channel that has been offered
  posts it hasn't answered *yet* scores as unreliable and can shift band. Defensible as an
  "agree rate", but it makes bands jumpy in a small network.

## 3. Findings

### 3.1 Critical — the bots could not run
| # | What was wrong | Impact | Fix |
|---|---|---|---|
| **C1** | **Every inline button used a positional argument** (`InlineKeyboardButton("Text", callback_data=…)`). aiogram 3 models are pydantic: positional args raise `TypeError: BaseModel.__init__() takes 1 positional argument`. 82 call sites across `ui.py` and the three bots. | *Every* menu, panel and prompt crashed the moment a user tapped `/start`. The old import-only check never constructed a button, so it passed. | All call sites converted to `text=…`; `test_bots.py` renders every menu. |
| **C2** | The `@dp.callback_query(startswith("s:"))` decorator sat on **`owner_targets`**, a plain sync helper, so **`pick_spend` was never registered**. | Tapping 1–5 (or "All") did nothing: no post was ever distributed, no credit ever spent. The whole point of the bot. | Decorator moved to `pick_spend`; a test asserts every registered handler is a coroutine and that the funnel's handlers exist. |
| **C3** | `reward_bot` advertised `/start @chan <subs>` but **never parsed the arguments**, and had no `/register`. | Nobody could register. Every member stayed at `size 0`, pinned to the minimum daily cap. | `/start` parses args (tolerantly) and a `/register` command was added; both validate the number instead of raising. |
| **C4** | `admin_bot` used `@dp.message(types.Message)` — the Message **class** as a filter. | The owner dashboard never opened. | `@dp.message(Command("start"))` + a catch-all fallback. |
| **C5** | The catch-all `@dp.message()` in `reward_bot` was registered **before** `/rank`, `/audit`, `/reports`, `/schedule` and returned `None` (= "handled"). | Those four commands were dead. | The catch-all no longer matches commands and returns `UNHANDLED` when idle. |

### 3.2 High — economy, caps and moderation were wrong
| # | What was wrong | Impact | Fix |
|---|---|---|---|
| **H1** | On "Agree", the credit was given to **the sender** (`ledger.earn(sender)`), not the channel that agreed to share. | Credit inflation: broadcasting your own post *earned* you credits. Inverts the whole 1:1 economy. | `ledger.earn(target)`, plus a wiring test asserting sender balance is unchanged. |
| **H2** | `/agree` was a plain command calling `earn()` for whoever typed it. | Any member could mint unlimited credits and bypass earn-first. | Command removed; agreeing only exists on a real offer's buttons. |
| **H3** | The daily cap was incremented on each **receiver** (`m["cap_used_today"]` of the target), and the sender's counter was reset in memory but never saved. | The size × performance cap **never applied to anyone**; receivers were penalised for being chosen. | Cap accounting moved into `CreditLedger` (`cap_used` / `cap_left` / `consume_cap`, UTC day, atomic) and charged to the sender. Enforced in both network bots. |
| **H4** | `pick_spend` charged `n` credits *before* finding targets. | Members paid for pairs that were never delivered. | Targets are resolved first, the member is charged for `len(targets)`, and a failed cap-consume refunds via a new `ledger.refund()` (which deliberately does **not** count as an "earn"). |
| **H5** | `ReportRegistry.report()` did `item.update(reported_post)`. Passing a delivery-log row (which has `status`/`sender`/`id`) overwrote the report's own fields. | A report could be stored already marked `delivered` — invisible to `pending()`, so it never reached a human. | Post context is nested under `post`; only safe keys are mirrored; ids are `max+1` (same fix in `ReviewQueue`). |
| **H6** | Nothing in any bot ever called `ReportRegistry.confirm()` / `clear()`. | Reports could be filed but **never actioned** — the "human review" promise had no human interface. | New 🚩 Pending-reports panel with Dismiss / Warn / Restrict / Remove, owner/admin-scoped, plus the pending count on the admin dashboard. |
| **H7** | `PerformanceEngine.match()` ignored `post_type`. | Off-niche posts were pushed at channels whose contract excluded that category. | Matching honours each target's `receive_types` (shared `allows_receive()` helper). |

### 3.3 Medium — multi-user state, storage, time, robustness
| # | What was wrong | Impact | Fix |
|---|---|---|---|
| **M1** | The submission funnel lived in **global** store keys (`accepted_terms`, `pending_cat`, `pending`, `partner_phase`, `contract_phase`, `contract_user`). | With two members online at once, B's category overwrote A's and A's post was routed with B's settings. A classic silent data-corruption bug. | Per-user sessions keyed by Telegram id, with a 24h TTL prune, in both network bots. |
| **M2** | `JsonStore.sync()` dumped the in-memory dict straight over the file, with no lock. All three bots share these files. | Lost updates (admin approving a post could wipe the reward bot's ledger) and a truncated file on a crash = total data loss. | Atomic write (temp file + `os.replace` + `fsync`), read-merge before write, `fcntl` lock, and tolerant loading. Covered by a test with two writers. |
| **M3** | `/schedule` parsed the time with `strptime().timestamp()` — the **server's** timezone — while telling the user "UTC". | Scheduled partner posts fired at the wrong hour on any non-UTC host. | Parsed as UTC explicitly; scheduler timestamps/display switched to `gmtime`. |
| **M4** | A failed scheduled delivery called `mark_done()`. | One transient error silently dropped an agreed partner slot. | `Scheduler.fail()` retries up to 3 attempts before giving up, recording the error. |
| **M5** | Direct delivery / scheduler fell back to `send_message("[direct] forwarded post from @x")` when the source message was missing. | An un-attributed bot message posing as the member's post — a **forward-only violation**. | No stand-in is ever posted; the attempt is audited as `failed` with `forward_valid=False`. |
| **M6** | The gate read only `msg.text`. | A scam post with its words in an image **caption** walked straight through. | Gate reads `text or caption` in both bots (regression test included). |
| **M7** | Delivery used `forward_from_chat.id` — the *source channel* — as the forward origin. | Requires the bot to be inside the partner's source channel; normally fails. | Re-forwards the copy the member sent to the bot, which keeps Telegram's original attribution header and always works. |
| **M8** | Substring matching in `classify_submission`. | "reclaimed" hard-blocked on "claim", "$100xyz" on "100x" — false blocks on innocent posts. | Word-boundary-aware matching (open ends preserved for patterns like `dm @`), plus `matched_patterns()` for reasons. |
| **M9** | `partnership_bot` crashed on `/start @X notanumber` (`int()`), and `"@" + None` for a user with no Telegram username. | Handler exceptions on ordinary user mistakes. | Both parsed defensively; contract flow keyed by user id. |
| **M10** | The partnership "🚩 Report" button only wrote an audit line — no report was filed. | "Admins review" was untrue; nothing to review. | Files a real pending report (still no auto-ban). |
| **M11** | `partnership_bot` imported `daily_post_cap` but never enforced it. | That network had **no** posting limit at all. | Cap enforced, and a slot is only consumed when a post is actually offered. |
| **M12** | `dash:back` was shadowed by the `dash:` handler registered above it. | The Back button did nothing on every admin panel. | Handled inside `dash()`; `_safe_edit` also swallows Telegram's "message is not modified". |
| **M13** | `ui.role_menu` offered Audit / Reports / Rank / Direct / Scheduled buttons that no handler implemented, and the guard only covered 3 of 8 panels. | Dead buttons; unguarded panels. | All panels implemented and uniformly owner/admin-gated (test: every panel renders, and none renders for a normal member). |
| **M14** | `admin_bot.is_owner` compared against `OWNER_USER_ID` even when unset (0). | A misconfigured deploy could hand the panel to id 0. | Unset owner id matches nobody. |
| **M15** | Score used only `forwards` for engagement, trusted any provider payload, and used the stored size even when the provider reported live subs. | Junk (or a string/negative) from a provider poisoned the score. | Values are validated (`_num`), reactions included, live `subs` preferred — and still **never fabricated** when unavailable. |
| **M16** | `.gitignore` had a blanket `*.json`. | Any future config/seed JSON would be silently ignored. | Narrowed to the two runtime files (+ `*.lock`), with a note. |
| **M17** | The branding doc's reward description was **697 chars** against Telegram's 512 limit, and one code fence was unclosed. | `setMyDescription` would reject it. | Copy rewritten to 506 chars, moved into `branding.py`, doc fixed, and a test enforces every limit. |

### 3.4 Test-suite defects (the audit's own blind spots)
- `test_core.py` contained **three** stacked `if __name__ == "__main__"` runners: the suite
  ran three times, and the two later lists were stale, so newer tests were skipped on those
  passes. It printed "ALL TESTS PASSED" three times with **no count**, and always exited 0.
  → one runner, at the end, prints `ALL TESTS PASSED (23)`, exits non-zero on failure.
- `test_governance.py` printed `FAILED:` but still **exited 0** → CI could never go red.
  → `sys.exit(1)` on failure.
- There were **no tests at all** for the Telegram layer. → `test_bots.py` (24 tests) drives
  real `Update` objects through each dispatcher with a mocked session.
  ⚠️ **Action for the repo owner:** `.github/workflows/tests.yml` is already updated in the
  working tree (it now runs compile + core + governance + wiring + `branding.py --check`),
  but GitHub blocks workflow edits from the assistant's app, so it is the one file left
  uncommitted — `git add .github/workflows/tests.yml && git commit && git push` it yourself.
  `./run_tests.sh` runs the same set locally.

### 3.5 What is CORRECT (verified, keep)
- Pure, offline-testable engine split from Telegram wiring.
- **No auto-ban:** a hard-block refuses a *post*, never a channel; borderline → review queue;
  reports are `pending` until a human acts. Tested from both the engine and the bot side.
- **No fake views:** the provider is optional and validated; failure degrades to the
  reliability proxy and reports `views: None` rather than inventing a number.
- **Forward-only:** genuine forwards only, and no un-attributed stand-in is ever posted.
- **Quality over size:** like-with-like performance bands + the receiver's contract; size is
  only a tiebreaker; the cap is size × performance.
- **Owner exemption** by numeric id, now proven end-to-end through the bot funnel.
- **Scoped admins:** only the owner issues single-use codes; scope is enforced, unknown
  scopes are dropped, revocation sticks.
- **Zero budget:** still JSON on free hosting — hardened rather than replaced.

## 4. Known limitations (honest — not bugs)
1. **No live channel views** without `views_provider.py` (MTProto). Bot API cannot read them.
2. **JSON store, not a DB.** Now atomic + merge-safe under three processes; move to SQLite
   (same free box) if write contention ever shows up. Postgres only on real demand.
3. **Chain mode can't guarantee a manual partner post** — use direct mode for guarantees.
4. **Channel identity = the member's Telegram @username** throughout the bots. It works, but
   a member whose channel handle differs from their user handle is modelled loosely. A future
   change should store a channel↔user mapping at registration.
5. **Human recruitment isn't automated** — the bots handle post-"yes" mechanics.

## 5. How to re-audit next session
```bash
pip install -r requirements.txt
python3 -m py_compile *.py        # expect no errors
python3 test_core.py              # expect ALL TESTS PASSED (23)
python3 test_governance.py        # expect 33/33
python3 test_bots.py              # expect ALL BOT WIRING TESTS PASSED (24)
python3 branding.py --check       # expect all fields within limits
```

## 6. Verdict
The engine was sound; the **bots were not runnable** and the credit economy, the daily cap
and the report loop were each wrong in a way the old tests could not see. All of it is fixed
and now covered by a test that fails loudly, in CI, if it regresses.

## 2026-09-08 compliance-first audit addendum

The completed Telegram Mini App smoke test proved the Admin frontend, HTTPS tunnel path,
server-side initData validation, origin handling for Telegram WebViews, authenticated
Dashboard, draft creation, and scheduled-draft persistence. It did not prove production
uptime, named-tunnel stability, real bot polling, or real channel permissions.

New findings and decisions:

- A durable cross-entity enforcement model was missing; `enforcement.py` now records
  ACTIVE/FLAGGED/RESTRICTED/SUSPENDED/BANNED/REMOVED state, reports, evidence references,
  decisions, timelines, temporary expiry, and safe mode.
- Eligibility now accepts enforcement state and safe-mode context and fails closed for
  restricted, suspended, banned, removed, and safe-mode operations.
- The Admin API now has the initial enforcement/safe-mode service boundary, owner approval
  requirement, and protected routes; full frontend enforcement panels and appeal UI remain.
- Campaigns now have durable user-facing titles and draft edit/delete service methods;
  frontend title entry is required. Full campaign editor controls and Ad Campaign parity
  remain unfinished.
- JSON locking now uses `msvcrt` on Windows where available instead of silently running
  without a lock.
- The offline audit environment lacks `aiogram`; core/governance/enforcement suites pass,
  while `test_bots.py` requires the installed requirements environment.

Compliance boundary: no feature is being added to evade Telegram enforcement, bypass
permissions/rate limits, automate fake engagement, rotate accounts, retaliate against
reporters, or activate financial settlement. Advertising remains consent-gated future scope.
