# CLICKMINT — Telegram Bot Branding Pack

Public-facing identity for the CLICKMINT bots. Every field conforms to Telegram's
actual limits (verified), and the usernames are given as **multiple availability-safe
candidates** because the username is the one field that **cannot be changed later**.

The three bots: **Reward bot** (main / public), **Partnership bot** (public), and
**Admin panel** (owner-only, so it needs less public polish).

---

## ⚠️ Two rules that matter most (from Telegram itself)
1. **Username is permanent.** Once you set it in @BotFather you can only change it by
   picking a *new* one (the old link breaks + becomes claimable). So choose carefully,
   and test availability in @BotFather via `/newbot` before committing. [1](https://www.botract.com/tools/bot-name-generator)
2. **The image is cropped to a circle** by Telegram, whatever shape you upload. Design
   with the subject centered and good safe margins — the corners get cut off.

> Symbol must be: "Mint" (coin/mint leaf — value, freshness, 'making', trustworthy green)
> + "Click" (a cursor/click — action, engagement). Teal/green = trust + growth. Safe for a
> crypto/airdrop audience. This is the CLICKMINT brand you already use everywhere.

---

## BOT 1 — REWARD / EXCHANGE BOT  (the main public bot)

### Name  (≤64 chars, spaces OK)
```
CLICKMINT — Partner & Reward Network
```
Shorter alternative (if you want one line): `CLICKMINT Partner Network`

### Username candidates  (must end in "bot", 5–32 chars, letters/digits/underscores)
| Candidate | note |
|---|---|
| `@ClickMintBot` | shortest + strongest brand — **likely taken**, test first |
| `@ClickMintPartnerBot` | ⭐ best balance of brand + clarity |
| `@ClickMintRewardBot` | 🟢 clear "reward" hook for the credit network |
| `@ClickMintNetworkBot` | 🟢 if "partner/reward" taken |
| `@ClickMintPartnersBot` | 🟢 plural variant |
| `@ClickMintBot_2026` / `@ClickMintBotNG` | if you want to guarantee a free handle |

> Order these by preference and try them in @BotFather. The first one that goes through
> is your username. **Set it / confirm it before you build the t.me links.**

### Description  (≤512 chars — shown at the start of the chat)
```
CLICKMINT is a partner & reward network for AI, crypto and airdrop channels.

Earn credits by sharing another channel's post, then spend them to get your own posts
shared across the network. Every share is tracked, every post is vetted: niche-related
content only, no spam, no money-asking, no scam, no third-party ad posts.

Performance matters more than size — your daily post cap scales with your channel size
× how well it actually performs, so small high-engagement channels get a fair shot and
nobody can spam.

Partner directly with quality channels you choose, agree a contract (type, volume,
timing, duration), and the owner mediates so neither side is cheated. Safe. Fair. Built
to grow.
```

### About / short description  (≤120 chars — profile page + share)
```
Verified partner & reward network for AI/crypto channels. Earn credits, share posts, partner safely.
```

---

## BOT 2 — PARTNERSHIP BOT  (curated quality partners)

### Name
```
CLICKMINT Partnerships
```

### Username candidates
| Candidate |
|---|
| `@ClickMintPartnershipBot` |
| `@ClickMintPartnersOfficial` |
| `@ClickMintPartnerOnline` |
| `@ClickMintPartnershipsBot` |

### Description  (≤512)
```
Curated partnership exchange for quality AI/crypto/airdrop channels.

Choose a partner, agree a contract — post types, volume, schedule, duration — and both
sides honour it. Niche-related content only, no spam, no ads, no scams. The owner is the
mediator: you're notified on open, renew and close, and a partnership can't just vanish.

Ideal for channels that want to cross-promote with a trusted, complementary partner
without losing quality or trust.
```

### About  (≤120)
```
Cross-promote with trusted complementary channels. Contract-bound, owner-mediated, safe.

---

## BOT 3 — ADMIN PANEL  (owner-only; light branding is enough)

### Name
```
CLICKMINT Admin
```
### Username candidates
| Candidate |
|---|
| `@ClickMintAdminBot` |
| `@ClickMintOwnerBot` |
| `@ClickMintControlBot` |

### Description  (≤512)
```
Owner dashboard for the CLICKMINT partner network. View live daily caps, approve or
reject queued posts, review open contracts, and generate one-time admin invite codes —
all by button. Owner-only.
```
### About  (≤120)
```
Owner-only dashboard for the CLICKMINT partner network.
```

---

## Applying this in @BotFather
For each bot (`/mybots` → tap bot → Edit):
- **Name** → `/setmyname`
- **Description** → `/setdescription`
- **About** → `/setabouttext`
- **Username** → `/setusername` (permanent)
- **Profile photo** → `/setuserpic` (upload the bot image)

> If you'd rather automate these via the Bot API, the methods are `setMyName`,
> `setMyDescription`, `setMyShortDescription` (the About), `setMyCommands` (menu), and
> `setChatPhoto` (the image). The username is the only one you set in @BotFather.

## Images
See the generated bot profile images in `docs/` — designed for Telegram's **circular
crop** (subject centered, safe margins). Files:
- `docs/bot_reward_logo.png`      (reward bot: mint coin + cursor)
- `docs/bot_partnership_logo.png` (partnership bot: two interlocking coins)
- `docs/bot_admin_logo.png`       (admin bot: mint coin + control sliders)
