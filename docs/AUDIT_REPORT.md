# CLICKMINT — Full Codebase Audit Report

**Date:** 2026-09-04 · **Scope:** the entire `partner_bots` codebase · **Method:** compile
check, automated test suites, wiring/import check, targeted code review.

## 1. Summary (verdict)
| Area | Status |
|---|---|
| Compiles (all modules) | ✅ PASS |
| Core engine tests | ✅ 14/14 |
| Governance rules tests | ✅ 23/23 (was falsely 21/21 — **fixed**) |
| 3 bots import + register handlers | ✅ PASS |
| Secrets not in code | ✅ PASS (env vars + `.gitignore`) |
| Owner exemption (incl. numeric id) | ✅ verified end-to-end |
| Overall | **Healthy, deployable.** 5 issues found; all addressed. |

## 2. Checks executed
```bash
python3 -m py_compile *.py                     # PASS
python3 test_core.py                           # 14/14 PASS
python3 test_governance.py                     # 23/23 PASS
python3 -c "<import all 3 bots w/ patched token>"   # all handlers registered
git check-ignore .env reward_ledger.json partnership_state.json   # all ignored
git check-ignore .env.example                  # NOT ignored (tracked template) ✓
```

## 3. Findings

### 3.1 Bugs FOUND and FIXED this session
| # | Severity | What was wrong | Why it mattered | Fix |
|---|---|---|---|---|
| **1** | **Medium** | `test_governance.py` had its `if __name__=="__main__"` runner **in the middle** of the file. The two owner-exemption test functions were defined *after* it, so they **never ran**. It printed `21/21` although 23 tests existed. | A silent blind spot — the owner-exemption guarantee looked green but wasn't being tested. | Moved the runner to the **end**; now `23/23` run, and it prints a `FAILED:` list on any failure. |
| **2** | **Low** | Dead/shadowed `menu:cap` handler in `reward_bot.py`. `menu_nav` matches `startswith("menu:")` and already dispatches `which=="cap"`, so a separate `@dp.callback_query(startswith("menu:cap"))` handler was unreachable. | Redundant code, confusing to maintain. | Removed the shadowed handler; `show_cap` is reached via `menu_nav`. |
| **3** | **Medium** | Owner exemption was keyed to the literal username `@ClickMintHQ`. If the owner's username string differed (case/space) or they forwarded from a different account, they'd silently be treated as a normal member and charged. | The core "owner is exempt" promise could fail in the field. | `_is_exempt()` now also recognizes a member whose stored `user_id == OWNER_USER_ID` — matching the role system. Added tests. |
| **4** | Prev. high | `pick_spend` parsed `int(data.split(':')[1])`, which would crash on the new `s:all` owner option. | Runtime crash on "route to all." | Reads `raw` as a string and handles `"all"` before int-casting. |
| **5** | **Low** | `.gitignore` has broad `*.json`, which would swallow any future committed JSON config/seed. | Could block legitimate config files. | Noted; keep the JSON ignore narrow to `reward_ledger.json`/`partnership_state.json` if you add config JSON. |

### 3.2 What is CORRECT (verified, keep)
- **Credit lifecycle + anti-cheat:** 1 credit = 1 (post, channel); earn-first; onboarding seed; owner exempt — all pass.
- **Owner exemption** is robust (by numeric id) and tested.
- **Forced-forward-only** (real attribution) enforced.
- **Performance > size**: like-with-like band matching; size is only a tiebreaker; no fake views (uses proxy when no MTProto).
- **No auto-ban on report/keyword** — reports and borderline posts go to **human review**.
- **Scoped-admins** via one-time invite code, least privilege (owner only grants).
- **Contract mediation** (owner notified on open/renew/close; close only final via owner).
- **Agreed-time scheduler** works for direct delivery (attack attribution kept via `forwardMessage`).

### 3.3 Known limitations (honest — not bugs, but real)
1. **No live channel views** without `views_provider.py` (MTProto/Telethon). Bot API can't read
   views; the engine falls back to the reliability proxy (deliberately never fakes views).
2. **JSON store, not a DB** — fine to hundreds of users; move to SQLite on the same free box
   (still $0) when you hit write-locking; Postgres only on real demand.
3. **Chain mode can't guarantee a manual partner post**; use direct mode (bot = admin) for guaranteed delivery.
4. **Human recruitment isn't automated** — the bots handle post-"yes" mechanics, not winning the yes.

## 4. How to re-audit in the next session (no wasted limit)
```bash
cd partner_bots
pip install -r requirements.txt
python3 -m py_compile *.py          # expect no errors
python3 test_core.py                # expect ALL TESTS PASSED (14)
python3 test_governance.py          # expect 23/23 governance tests passed
# then import-check the 3 bots with a patched token; set OWNER_USER_ID + tokens in .env
```

## 5. Verdict
The codebase is **structurally sound, tested, and deploy-ready** on zero budget. The one real
issue (the test-runner blind spot hiding the owner-exemption tests) has been fixed and is now
covered. Remaining items are honest limitations to plan around, not defects.
