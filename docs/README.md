# CLICKMINT — Project Documents

**Start with `STATUS.md`.** It is the one file kept current on every change: what is implemented,
partial, pending or rejected, plus the live test counts. Everything else is a topic document.

## Canonical (maintained)
| File | Covers |
|---|---|
| **`STATUS.md`** | Implementation status matrix, decisions record, current test counts. **Read first.** |
| `TELEGRAM_USER_BOT_VERIFICATION.md` | User-owned bot model, destination state machine, re-verification schedule, error classification, shared SQLite storage. |
| `RELAY_ARCHITECTURE.md` | Which bot may forward which copy of a post; fail-closed routing. |
| `ONBOARDING_WEBAPP.md` | Secure Mini App token onboarding. |
| `COMPLIANCE_AND_ENFORCEMENT.md` | Entity states, reports, appeals, safe mode, Telegram boundaries. |
| `ADVERTISING.md` | Consent-aware ads model (off by default). |
| `ADMIN_API.md` / `ADMIN_DEPLOYMENT.md` | Admin Mini App API and HTTPS deployment. |
| `ECONOMICS.md` | Economic modelling only — no payments implemented. |
| `REWARD_SYSTEM.md` | 🪙 MINT rules and the user-facing copy standard. |
| `ENGINEERING_HISTORY.md` | Dated decision records (append-only). |
| `FINAL_AUDIT_2026-09-09.md` | Executed offline audit + live-Telegram checklist. |

## Strategy & reference (valid)
| File | Covers |
|---|---|
| `MASTER_PLAN.md` Part 1 & 4 | Growth strategy (quality partners, 3-tier ladder) and handoff notes. |
| `PARTNERSHIP_RESEARCH.md` | Partner research and benchmarks. |
| `BOT_BRANDING.md` | Names, descriptions, images, Telegram field limits. |
| `DEPLOY_WORKFLOW.md`, `GITHUB_PUSH.md`, `DEPLOY_FREE.md` | Operations. |

## Historical (superseded — do not treat as current)
| File | Why kept |
|---|---|
| `MASTER_SYSTEM_DOCUMENTATION.md` | 2026-09-04 snapshot; original flow/formula explanations. |
| `MASTER_PLAN.md` Parts 2–3, `AUDIT_REPORT.md`, `ARCHITECTURE_AUDIT_2026-09.md`, `DRY_RUN.md` | Earlier audits; findings are all resolved or tracked in `STATUS.md`. |

## Running the suite
```
pip install -r requirements.txt
python3 run_all_tests.py      # every suite; counts are recorded in STATUS.md
python3 simulate.py           # scripted end-to-end dry run against a mocked Telegram
```
