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
