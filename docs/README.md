# CLICKMINT — Project Documents

This folder holds the project's planning, audit, research and branding docs so the whole
story lives in the repo (and travels with the code). Read these to understand *what* the
system is and *why* it was built the way it was, before you touch any code.

## Index
| **`MASTER_SYSTEM_DOCUMENTATION.md`** | Complete source-based architecture, data model, workflows, commands, functions, diagrams, security, deployment, and known unknowns. | **Read first for technical orientation.** |
| **`REWARD_SYSTEM.md`** | 🪙 MINT wallet rules, reward lifecycle, band matching, and the user-facing writing standard. | Read before changing the reward bot. |
| File | What it contains | When to read it |
|---|---|---|
| **`MASTER_PLAN.md`** | The single source of truth: growth plan, bot design/directory library, full audit results, and the **handoff prompt** for the next session. | **Read first.** |
| **`AUDIT_REPORT.md`** | The formal full-codebase audit. What passed, what bugs were found and fixed (incl. the test-runner blind spot), and known honest limitations. | Before changing code, to know the verified baseline. |
| **`PARTNERSHIP_RESEARCH.md`** | The growth/partner research: quality-over-size filter, engagement benchmarks, channel types, the 2:1 offer, contract terms, paid-ads guidance. | When planning growth/outreach. |
| **`BOT_BRANDING.md`** | Name, usernames (with availability fallbacks), About, and Description for all three bots, plus the bot images and Telegram field limits. | When creating the bots in @BotFather. |
| **`GITHUB_PUSH.md`** | Step-by-step Git Bash walkthrough for pushing this repo, with errors and fixes. | When pushing/updating on GitHub. |
| **`DEPLOY_FREE.md`** | How to run everything for $0: free hosting, SQLite vs Postgres, systemd services. | When deploying to a free server/Pi. |

## The next-session short prompt (paste into a GitHub-connected agent)
> Audit this repo (CLICKMINT, a zero-budget Telegram partner/reward network).
> Read `docs/MASTER_PLAN.md` Parts 2 & 3 to understand the design and the current
> audited state. Run `python3 -m py_compile *.py`, `python3 test_core.py` (expect 14/14),
> `python3 test_governance.py` (expect 23/23), and confirm all three bots
> (`reward_bot.py`, `partnership_bot.py`, `admin_bot.py`) import and register handlers.
> Then find and fix remaining bugs / missing tests / robustness, respecting: no auto-ban,
> no fake views, forward-only, quality-over-size, owner exemption, scoped-admin least
> privilege, zero budget, free hosting, and the branding in `BOT_BRANDING.md`.

## Quick top-level layout of the repo
- `core.py` — engine (tiering, credits, performance, reports, contract, owner exemption)
- `governance.py` — rules layer (post limit, submission gate, review queue, partner contracts, roles)
- `reward_bot.py` / `partnership_bot.py` / `admin_bot.py` — the three Telegram bots
- `config.py` — reads all secrets from environment variables (never hardcoded)
- `store.py` — JSON persistence; `scheduler.py`; `ui.py`; `views_provider.py` (optional MTProto)
- `test_core.py` (14) / `test_governance.py` (23) — offline test suites, run by CI on every push
- `.github/workflows/` — `tests.yml` (run tests) and `deploy.yml` (optional auto-deploy)
- `deploy.sh` + `clickmint*.service` — free-host deployment kit
