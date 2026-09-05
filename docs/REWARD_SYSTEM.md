# 🪙 MINT Reward System

## 1. What the reward bot does

The reward bot is a **1:1 post-exchange network**. It has one simple promise:

> **Share another member's approved post, earn MINT, then spend MINT to distribute your own post.**

The unit is written consistently as **🪙 MINT**. The icon is a coin because MINT
is a spendable exchange unit, not a cryptocurrency, investment, or cash balance.

## 2. The complete lifecycle

### A. Register a destination

A member registers one or more channels/groups. Each destination has its own:

- audience size;
- niche categories;
- bot-access state;
- performance band: **A, B, or C**;
- status: **ACTIVE, WATCH, RESTRICTED, or REMOVED**;
- delivery limit.

A member can manage several destinations. A problem with one destination must not
ban or demote every destination owned by that person.

### B. Accept the posting terms

Before submitting, the member accepts **Mint Post Exchange Posting Terms** and
selects a niche. The post must match the receiving destination's accepted niche.
Clear scam, phishing, money-asking, seed-phrase, and third-party ad-service
content is blocked. Ambiguous content goes to human review.

### C. Submit a real forward

The member forwards the original Telegram message to the bot. The bot stores the
source chat and message id. Delivery uses Telegram's forward operation, preserving
attribution, media, captions, and inline buttons.

### D. Match destinations

The engine matches by:

1. accepted category;
2. destination status;
3. performance band, preferring the same band and widening carefully when needed;
4. destination type and notification preference;
5. remaining sender limit and available MINT.

Audience size is a tiebreaker, not the only quality signal.

### E. Spend MINT

Every **post → destination** pair costs **🪙 1 MINT**.

- One post → one destination = 🪙 1 MINT.
- One post → five destinations = 🪙 5 MINT.
- A failed batch is refunded.
- A member must complete at least one genuine share before spending, except the owner.
- New-member onboarding MINT is a seed, not earned MINT.

### F. Receive and complete

A destination receives an offer. If accepted, the bot forwards the original
message. The destination that completes the share earns **🪙 1 MINT**. The sender
does not earn from sending their own post.

## 3. Band and status model

### Performance band

The score uses only available, real observations:

- reach: views relative to subscribers, when available;
- engagement: reactions and forwards relative to views, when available;
- reliability: posts completed versus posts offered;
- reputation: confirmed reports and invalid submissions.

If Telegram does not expose a metric, the system leaves that component absent and
rebalances across available components. It never invents views or engagement.

### Status

- **ACTIVE** — eligible for normal matching.
- **WATCH** — visible to admins and matched cautiously.
- **RESTRICTED** — temporarily excluded from normal matching.
- **REMOVED** — excluded until an owner/admin decision restores access.

A status belongs to the destination, not automatically to the person who owns it.

## 4. Owner controls

The owner is exempt from MINT costs and daily limits. Owner posts are placed first
in the persistent available-post queue. The owner can also:

- inspect category totals;
- control public-rank visibility, private by default;
- publish announcements;
- review reports and borderline posts;
- manage scoped administrators.

## 5. Copy and visual writing standard

Every user-facing screen follows this order:

1. **Emoji + uppercase section title**
2. One-line explanation
3. Short labelled facts
4. One clear next action
5. Back/cancel navigation where the user is in a flow

Use:

- **bold** for titles and labels;
- short paragraphs, not walls of text;
- one blank line between sections;
- `✅` success, `⚠️` attention, `⛔` blocked, `📤` sending, `📥` receiving,
  `🪙` MINT, `📈` performance, `📂` destinations;
- `🪙 1 MINT` for a full amount and `🪙 1` only in compact buttons.

Do not use “credits” in user-facing copy. Keep `balance`, `earned`, and `spent`
as internal storage fields for backward compatibility.

Telegram supports bold, italic, underline, strikethrough, spoiler, quotations,
links, and preformatted text through message entities or MarkdownV2/HTML. The bot
uses HTML for new structured copy so labels render as intended instead of showing
literal Markdown markers. See the official [Telegram Bot API formatting
reference](https://core.telegram.org/bots/api#formatting-options).
