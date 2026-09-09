# CLICKMINT Decision Register

**Status date:** 2026-09-09  
**Purpose:** Single record of decisions and constraints that must not be rediscovered or silently reversed.

## Decision states

- **Approved:** Explicit project constraint or confirmed implementation direction.
- **Implemented:** Approved decision reflected in current source.
- **Pending:** Requires a future product/technical decision.
- **Rejected/constrained:** Do not implement in the stated form.
- **Unknown:** No reliable evidence of a final decision.

## Approved and binding constraints

### Identity, credentials, and permissions

1. Use the user's own Telegram bot architecture. Do not substitute a shared CLICKMINT bot.
2. The user must add their bot to the relevant channel/group and grant administrator status and required permissions.
3. Bot tokens are secrets. They must never appear in logs, chat messages, callback data, URLs, browser state, or client-side code.
4. Telegram Mini App `initData` and user identity must be validated server-side.
5. Browser-submitted identity, permission, count, or verification flags are not authoritative.
6. Participation must fail closed when required verification, eligibility, active status, or permissions are absent.
7. Every verification item shown to a user must correspond to a real backend operation.
8. Verification failures must explain the cause and corrective action.

### Telegram data and compliance

9. Use official Telegram API responses for destination metadata, bot status, permissions, and member counts where available.
10. Do not accept manually entered subscriber/member counts as authoritative.
11. Respect chat-type-specific Telegram permissions and documented API limitations.
12. Do not invent unsupported message markup, custom emoji behavior, statistics, attribution, or payment capabilities.
13. Do not bypass Telegram restrictions, rate limits, permissions, or platform policies.

### Safety and governance

14. Preserve consent, safety, anti-abuse, governance, audit, and enforcement controls.
15. High-impact enforcement must remain reviewable and governed; an AI risk signal alone must not declare a user criminal or trigger irreversible punishment.
16. Cancelled, deleted, completed, or otherwise non-queueable campaigns must not enter the broadcast queue.
17. Destructive Mini App actions require confirmation and state-aware controls.
18. Human verification is a risk signal that complements Telegram identity, behavior, reputation, and rate limits. CAPTCHA is not proof of humanity.

### Architecture and cost

19. UI and bot handlers must not contain core business rules; shared application services should own them.
20. Keep the core functional without AI, paid services, deposits, withdrawals, or future ecosystem systems.
21. Avoid premature microservices, Redis/Celery/Kubernetes, large external assets, or other infrastructure without a demonstrated current-scale need.
22. Preserve bot command functionality for power users while making visual controls the primary Admin Mini App experience.
23. Future AI, payments, website, and cross-platform support must be optional extension layers, not core dependencies.
24. Documentation must distinguish implemented behavior from roadmap and research.

## Implemented decisions

- Server-side Mini App authentication boundary exists.
- User-owned bot credential/channel connection architecture exists in the current implementation set.
- Encrypted credential storage exists locally; production secret/KMS protection remains an operational requirement.
- Verification is applied to scheduled delivery in the remote implementation tip `d9fcb6d`.
- Broadcast queue and Reward campaign lifecycle foundations exist.
- Campaign titles are supported by the backend campaign model.
- Enforcement/safe-mode foundations exist.

## Pending decisions

These items were requested or recommended but have no reliable final approval in the available evidence:

1. Replacement terminology for `Band` (for example, Tier, Level, Reputation, or Credibility).
2. Final Mini App visual design system and navigation model.
3. Final Ad Campaign data model, audience, consent, and delivery semantics.
4. Whether the current task marketplace should replace all previous offer/post paths immediately or coexist during migration.
5. Exact task claim/slot/expiry state machine.
6. Exact Telegram-compatible visual editor implementation and custom emoji policy.
7. Human-verification provider and risk thresholds.
8. Exact referral attribution and qualification rules.
9. Exact Mint issuance, sinks, limits, and reversal policy.
10. Whether and when Boost should exist as a second currency.
11. Payment/deposit providers and supported assets.
12. Withdrawal assets, eligibility, review, and regional availability.
13. Advertising pricing, channel rewards, platform fees, and ad frequency.
14. Hosting/database migration decisions for larger scale.

## Rejected or explicitly constrained proposals

- Shared-bot credentials in place of user-owned bot credentials.
- Manual member/subscriber counts as authoritative values.
- Any participation bypass around bot/channel permissions or verification.
- Queueing cancelled or deleted campaigns.
- Unsupported or invented Telegram formatting.
- CAPTCHA on every page or as a universal human-proof mechanism.
- Treating Mint, Boost, and external payout assets as interchangeable.
- Crediting deposits because a user claims to have paid.
- Fake views, fake engagement, fake popularity, artificial subscribers, or guaranteed performance.
- Treating future deposits, withdrawals, advertising, AI, or autonomous engineering as currently available.
- Automatic high-impact enforcement solely from AI classification.
- Building large-scale infrastructure before current workload requires it.

## Research-only directions

The following remain research/design unless separately approved:

- Telegram Stars, TON, Wallet, crypto deposits, and withdrawals.
- Full referral economy and multi-level referrals.
- Channel join attribution and join-based rewards.
- Advanced advertising marketplace.
- AI knowledge base, agents, and autonomous engineering.
- Cross-platform adapters.
- Large media/branding ecosystem.

## Source-of-truth precedence

When sources disagree, use this order:

1. Explicit user safety/security constraints in the current handoff.
2. Current source code and passing tests.
3. The latest verified Git commit/remote implementation.
4. Current technical documentation.
5. Historical plans and older audit prose.
6. Conversation-only proposals.

A lower-priority source must not override a higher-priority security or Telegram-compliance constraint.
