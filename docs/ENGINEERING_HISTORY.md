# CLICKMINT Engineering History

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
