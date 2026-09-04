"""CLICKMINT partner bots — shared core logic.

Pure, offline-testable engine that BOTH bots run on:

  1) REWARD / EXCHANGE bot  — tiered network, 1:1 credit ledger, accept/reject chain.
  2) PARTNERSHIP bot        — curated partners, terms contract, accept/reject, owner-exempt.

Design decisions (from the agreed plan):
  • Two SEPARATE systems. Nothing is shared between them except this common toolkit.
  • REWARD: strict 1:1 credits. Earn 1 (share another's post) -> can spend 1 (have yours shared).
      - You must EARN at least 1 before you can spend (earn-first).
      - New members get a +2 ONBOARDING SEED balance (goodwill / seeds the network).
      - Anti-cheat: each credit = exactly one (post, channel) pair. Sending ONE post to 5
        channels costs 5 credits (you cannot do it for the price of 1).
      - STRICT same-tier: only channels of similar size share with each other.
      - Accept/Reject chain: bot DM's a forwarded post; owner taps Agree (post it, +1 credit,
        chain continues) or Reject (moves to next target). No admin needed on their side.
      - Owner (CLICKMINT) is EXEMPT: no earn/spend, can route posts to any tier freely.
  • PARTNERSHIP: curated main partners only; each sets a terms contract (post types they
      ACCEPT and RECEIVE); senders forward a post to the bot; targets Agree/Reject.
      - Delivery: chain-forward (accept/reject in DM) by default; if a partner grants the bot
        channel-admin, the bot posts directly instead.
"""
from __future__ import annotations
import time

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
OWNER_USERNAME = "@ClickMintHQ"          # exempt from the reward system
ONBOARDING_SEED = 2                      # free starting credits for new members
EARN_MIN_BEFORE_SPEND = 1                # must share >= this many before spending

# Size tiers (STRICT: share only within your own band)
TIERS = [
    ("T1", 700, 1000),
    ("T2", 2000, 3000),
    ("T3", 4000, 5000),
    ("T4", 5001, None),                 # "and the rest"
]

# Post content/passion types available for terms contracts
POST_TYPES = [
    "Airdrops", "Testnets", "AI Tools", "AI x Web3", "Scam Alerts",
    "Guides", "Deadlines", "DeFi", "TON / Web3", "General",
]

DAILY_DELIVERY_CAP = 3                  # per member: max channels one single post hits/day

# Performance bands and channel status (replaces subscriber-size as the match key)
BANDS = ["A", "B", "C"]                 # High / Medium / Low performer
CHANNEL_STATUS = ["ACTIVE", "WATCH", "RESTRICTED", "REMOVED"]

# Weighting for the performance score (rebalanced among the components actually available)
PERF_WEIGHTS = {
    "reach": 0.40,          # views / subscribers          (what the user really cares about)
    "engagement": 0.25,     # (reactions + forwards) / views
    "reliability": 0.25,    # posts actually forwarded / posts offered
    "reputation": 0.10,     # minus penalties for reports / invalid (copied) posts
}
# Band thresholds (score 0..1)
BAND_HIGH = 0.66
BAND_MED = 0.33

# Reputation penalties
REPORT_PENALTY = 0.5        # per confirmed report (reputation component -> 0)
INVALID_POST_PENALTY = 0.2  # per post that arrived as a copy/non-forward

# FORWARDED-ONLY RULE: a post submitted for distribution MUST be a genuine forward
# (carries attribution from its source channel). Copied / re-typed / re-uploaded
# content is REJECTED. This keeps attribution honest and prevents "air-dropped"
# unverifiable content.
FORWARD_ONLY = True


def is_forward(msg) -> bool:
    """True only if the incoming Telegram message is a real forward.

    We recognise a real forward by the fact that the source is exposed:
    either a forward_from (a user) or a forward_from_chat (a channel/group).
    A forward_origin is also present for forwarded content from elsewhere.
    Someone re-typing or re-uploading content produces neither -> rejected.
    """
    if not FORWARD_ONLY:
        return True
    return bool(getattr(msg, "forward_from", None)
                or getattr(msg, "forward_from_chat", None)
                or getattr(msg, "forward_origin", None))


def forward_source(msg) -> str:
    """Best-effort human-readable source of a forward, for the audit log."""
    fc = getattr(msg, "forward_from_chat", None)
    if fc is not None:
        name = getattr(fc, "username", None) or getattr(fc, "title", None)
        if not name:
            try:
                name = fc.id
            except AttributeError:
                name = fc
        return f"chat:{name}"
    ff = getattr(msg, "forward_from", None)
    if ff is not None:
        if isinstance(ff, str):
            return f"user:@{ff.lstrip('@')}"
        un = getattr(ff, "username", None)
        if un:
            return f"user:@{un}"
        try:
            return f"user:{ff.id}"
        except AttributeError:
            return "user:?"
    fo = getattr(msg, "forward_origin", None)
    if fo is not None:
        return f"origin:{type(fo).__name__}"
    return "unknown"


# ---------------------------------------------------------------------------
# TIERING
# ---------------------------------------------------------------------------
def tier_for_size(subs: int) -> str:
    """Assign a size to its tier band. Gap sizes go to the NEAREST band (closest midpoint)."""
    # band midpoints
    mid = []
    for name, lo, hi in TIERS:
        hi2 = hi if hi is not None else lo * 3
        mid.append((name, (lo + hi2) / 2))
    # if subs falls inside a band, use it directly
    for name, lo, hi in TIERS:
        if subs >= lo and (hi is None or subs <= hi):
            return name
    # else nearest band by midpoint distance
    best = min(mid, key=lambda x: abs(x[1] - subs))
    return best[0]



# ---------------------------------------------------------------------------
# CREDIT LEDGER (reward bot)
# ---------------------------------------------------------------------------
class CreditLedger:
    """Shared, JSON-backed state. One ledger per bot deployment."""

    def __init__(self, store):
        self.store = store                      # dict-like persistence (see store.py)
        self.ledger = self.store.get("ledger", {})
        # numeric owner id, so the owner is recognised by their Telegram id (matching
        # the role system) — not only by username. Set by the bot at startup. When set,
        # any member whose stored user_id equals this is treated as the owner (exempt).
        self.owner_user_id = None

    # --- helpers ---
    def _m(self, username: str) -> dict:
        if username not in self.ledger:
            self.ledger[username] = {
                "size": 0, "tier": "T1", "balance": ONBOARDING_SEED,
                "earned": 0, "spent": 0, "times_shared": 0,
                "is_owner": False, "is_partner": False,
                "accept_types": [], "receive_types": [],
                "delivered_today": 0, "last_deliver_day": "",
                "status": "ACTIVE",          # ACTIVE | WATCH | RESTRICTED | REMOVED
                "offered": 0, "posted": 0, "invalid_posts": 0,
                "direct_mode": False,        # bot is admin here -> automatic direct delivery
            }
            self.store["ledger"] = self.ledger
        return self.ledger[username]

    def register(self, username: str, size: int, is_owner: bool = False,
                 is_partner: bool = False) -> dict:
        m = self._m(username)
        m["size"] = size
        m["tier"] = tier_for_size(size)
        m["is_owner"] = is_owner or username == OWNER_USERNAME
        m["is_partner"] = is_partner
        self.store["ledger"] = self.ledger
        self.save()
        return m

    def save(self):
        self.store["ledger"] = self.ledger
        self.store.sync()

    def set_user_id(self, username: str, uid) -> dict:
        """Remember the Telegram user id behind a username so the bot can DM them
        later (e.g. to notify that a queued post was approved)."""
        m = self._m(username)
        m["user_id"] = uid
        self.save()
        return m

    def user_id(self, username: str):
        return self._m(username).get("user_id")

    # --- earning / spending ---
    def earn(self, username: str, amount: int = 1) -> dict:
        """Call when a member AGREES to post someone else's post."""
        if self._is_exempt(username):
            return self._m(username)
        m = self._m(username)
        m["balance"] += amount
        m["earned"] += amount
        m["times_shared"] += amount
        self.save()
        return m

    def can_spend(self, username: str) -> tuple[bool, str]:
        """earn-first rule + balance + not exempt."""
        if self._is_exempt(username):
            return True, "owner is exempt"
        m = self._m(username)
        if m["earned"] < EARN_MIN_BEFORE_SPEND:
            return False, "earn-first: share at least one of others' posts before yours are spread."
        if m["balance"] <= 0:
            return False, "no credit balance. Share others' posts to earn credits."
        return True, "ok"

    def spend(self, username: str, n_pairs: int) -> tuple[bool, str]:
        """
        Spend credits for n (post, channel) pairs.
        Anti-cheat: sending ONE post to N channels = N credits (n_pairs = N).
        """
        ok, why = self.can_spend(username)
        if not ok:
            return False, why
        m = self._m(username)
        if self._is_exempt(username):      # owner routes freely, pays nothing
            return True, "owner exempt — routed freely."
        if n_pairs < 1:
            return False, "must request at least one (post, channel) pair."
        if m["balance"] < n_pairs:
            return False, f"need {n_pairs} credits, have {m['balance']}."
        m["balance"] -= n_pairs
        m["spent"] += n_pairs
        self.save()
        return True, f"spent {n_pairs} credits. Balance now {m['balance']}."

    # --- anti-flood ---
    def mark_delivered(self, username: str) -> bool:
        m = self._m(username)
        day = time.strftime("%Y-%m-%d")
        if m["last_deliver_day"] != day:
            m["delivered_today"] = 0
            m["last_deliver_day"] = day
        if m["delivered_today"] >= DAILY_DELIVERY_CAP:
            return False
        m["delivered_today"] += 1
        self.save()
        return True

    def _is_exempt(self, username: str) -> bool:
        """Owner is exempt. Recognised by (a) the reserved username, (b) the is_owner
        flag, or (c) the member's stored user_id == the numeric OWNER_USER_ID."""
        m = self._m(username)
        if m.get("is_owner", False):
            return True
        if username == OWNER_USERNAME:
            return True
        if self.owner_user_id and m.get("user_id") == self.owner_user_id:
            return True
        return False

    def balance(self, username: str) -> dict:
        m = self._m(username)
        return {"balance": m["balance"], "earned": m["earned"],
                "spent": m["spent"], "times_shared": m["times_shared"],
                "tier": m["tier"], "is_owner": m["is_owner"]}

    # --- DIRECT MODE: channel granted the bot admin (Post Messages) ---
    def grant_direct(self, username: str) -> dict:
        """Mark a channel as 'bot is admin here' -> delivery becomes automatic
        (bot posts directly at the agreed time, no accept/reject needed)."""
        m = self._m(username)
        m["direct_mode"] = True
        self.save()
        return m

    def revoke_direct(self, username: str) -> dict:
        m = self._m(username)
        m["direct_mode"] = False
        self.save()
        return m

    def is_direct(self, username: str) -> bool:
        return bool(self._m(username).get("direct_mode", False))


# ---------------------------------------------------------------------------
# PARTNERSHIP TERMS CONTRACT
# ---------------------------------------------------------------------------
class Contract:
    SETTABLE = POST_TYPES

    def set_contract(self, member: dict, accept_types: list, receive_types: list) -> dict:
        for t in accept_types + receive_types:
            if t not in self.SETTABLE:
                raise ValueError(f"unknown post type: {t}")
        member["accept_types"] = sorted(set(accept_types))
        member["receive_types"] = sorted(set(receive_types))
        return member

    def allows_receive(self, member: dict, post_type: str) -> bool:
        rec = member["receive_types"]
        return (not rec) or (post_type in rec) or ("General" in rec)


# ---------------------------------------------------------------------------
# DELIVERY LOG (admin audit trail)
# ---------------------------------------------------------------------------
class DeliveryLog:
    """One row per (post -> target channel) delivery. Used by the admin to audit
    the chain and catch any post that arrived as a copy (forward-validity flag).

    Field per row:
      ts, bot (reward|partnership), sender, source (forward_from), post_type,
      target_channel, mode (chain|direct), status (offered|agreed|delivered|skipped),
      forward_valid (bool), message_id
    """

    def __init__(self, store, key="delivery_log"):
        self.store = store
        self.key = key
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def record(self, **row) -> dict:
        row.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        log = self.store[self.key]
        log.append(row)
        self.store[self.key] = log
        self.store.sync()
        return row

    def all(self) -> list:
        return self.store.get(self.key, [])

    def last(self, n: int = 20) -> list:
        return self.all()[-n:]

    def count(self) -> int:
        return len(self.all())

    def summary(self) -> str:
        log = self.all()
        total = len(log)
        delivered = sum(1 for r in log if r.get("status") == "delivered")
        agreed = sum(1 for r in log if r.get("status") == "agreed")
        skipped = sum(1 for r in log if r.get("status") == "skipped")
        invalid = sum(1 for r in log if not r.get("forward_valid", True))
        lines = [
            f"AUDIT SUMMARY — {total} deliveries",
            f"  delivered: {delivered}   agreed: {agreed}   skipped: {skipped}",
        ]
        if invalid:
            lines.append(f"  ⚠ INVALID (arrived as copy/non-forward): {invalid}")
        else:
            lines.append("  forward-validity: all passed ✅")
        return "\n".join(lines)

    def invalid_records(self) -> list:
        return [r for r in self.all() if not r.get("forward_valid", True)]


# ---------------------------------------------------------------------------
# REPORT SYSTEM (receiver reports an inappropriate / scam / fraud post)
# ---------------------------------------------------------------------------
class ReportRegistry:
    """Holds reports raised by receivers. The bot never auto-bans — it flags, and
    the ADMIN reviews and confirms. Only confirmed reports affect a channel's status."""

    def __init__(self, store, key="reports"):
        self.store = store
        self.key = key
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def report(self, sender: str, reported_post: dict, reporter: str,
               reason: str = "", note: str = "") -> dict:
        """A receiver reports a post that a sender offered to their channel."""
        item = {
            "id": len(self.all()) + 1,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sender": sender,                       # the channel that offered the post
            "reporter": reporter,                   # the receiver who flagged it
            "reason": reason, "note": note,
            "status": "pending",                    # pending | reviewed-ok | confirmed
            "action": None,                         # None | warn | restrict | remove
        }
        item.update(reported_post)                  # attach post/forward context
        log = self.store[self.key]
        log.append(item)
        self.store[self.key] = log
        self.store.sync()
        return item

    def all(self, status: str | None = None) -> list:
        items = self.store.get(self.key, [])
        if status:
            return [i for i in items if i.get("status") == status]
        return items

    def pending(self) -> list:
        return self.all("pending")

    def count(self) -> int:
        return len(self.all())

    def confirm(self, report_id: int, action: str = "restrict", note: str = "") -> dict:
        """Admin confirms a report and applies an action to the SENDER channel.
        action: warn | restrict | remove"""
        log = self.store[self.key]
        for it in log:
            if it.get("id") == report_id:
                it["status"] = "confirmed"
                it["action"] = action
                it["action_note"] = note
                self.store[self.key] = log
                self.store.sync()
                return it
        raise KeyError(f"no report #{report_id}")

    def clear(self, report_id: int) -> dict:
        """Mark a report as reviewed-OK (false positive)."""
        log = self.store[self.key]
        for it in log:
            if it.get("id") == report_id:
                it["status"] = "reviewed-ok"
                self.store[self.key] = log
                self.store.sync()
                return it
        raise KeyError(f"no report #{report_id}")

    def reports_against(self, sender: str, confirmed_only: bool = True) -> int:
        items = self.all()
        if confirmed_only:
            items = [i for i in items if i.get("status") == "confirmed"]
        return sum(1 for i in items if i.get("sender") == sender)


# ---------------------------------------------------------------------------
# PERFORMANCE ENGINE (replaces subscriber-size as the match key)
# ---------------------------------------------------------------------------
class PerformanceEngine:
    """Scores each channel on actual PERFORMANCE (reach ratio + engagement +
    reliability + reputation), buckets them into High/Medium/Low, and matches
    'like-with-like' so it stays fair and every channel can grow.

    View source is PLUGGABLE:
      • `views_provider(channel) -> {"views": int, "forwards": int, "subs": int}`
        (e.g. an MTProto Telethon observer, or manual import)
      • if no view data -> the engine degrades to the reliability proxy and a
        neutral score (no fake views).

    Subscriber size is only a tiebreaker, never the match key.
    """

    def __init__(self, ledger: CreditLedger, store, views_provider=None):
        self.ledger = ledger
        self.store = store
        self.views_provider = views_provider

    # --- per-channel scoring ---
    def report_data(self, username: str) -> dict:
        m = self.ledger._m(username)
        offered = m.get("offered", 0)
        posted = m.get("posted", 0)
        invalid = m.get("invalid_posts", 0)
        return m, offered, posted, invalid

    def score(self, username: str) -> dict:
        m, offered, posted, invalid = self.report_data(username)
        comp = {}

        # reach ratio (views / subs) — the core "is it actually read" measure
        views_count = None
        extra = {}
        if self.views_provider:
            try:
                extra = self.views_provider(username) or {}
            except Exception:
                extra = {}
        subs = max(m.get("size", 0), 1)
        views = extra.get("views")
        if views is not None:
            comp["reach"] = min(views / subs / 0.60, 1.0)     # 60% reach = max
            views_count = views
        fwd = extra.get("forwards", 0)
        if views_count:
            comp["engagement"] = min((fwd) / max(views_count, 1) / 2.0, 1.0)

        # reliability (did they actually forward what was offered?)
        comp["reliability"] = min(posted / max(offered, 1), 1.0)

        # reputation (minus penalties for confirmed reports / invalid copies)
        penalties = (self._confirmed_reports(username) * REPORT_PENALTY
                     + invalid * INVALID_POST_PENALTY)
        comp["reputation"] = max(0.0, 1.0 - penalties)

        # rebalance weights over the components actually present
        avail = {k: v for k, v in comp.items() if v is not None}
        wsum = sum(PERF_WEIGHTS[k] for k in avail)
        total = sum(avail[k] * PERF_WEIGHTS[k] for k in avail) / (wsum or 1)
        return {"score": total, "components": avail, "views": views_count,
                "band": self.band(total), "status": self.status(username)}

    def _confirmed_reports(self, username: str) -> int:
        rep = self.store.get("reports", [])
        return sum(1 for r in rep if r.get("status") == "confirmed"
                   and r.get("sender") == username)

    def band(self, score: float) -> str:
        if score >= BAND_HIGH:
            return "A"
        if score >= BAND_MED:
            return "B"
        return "C"

    def status(self, username: str) -> str:
        m = self.ledger._m(username)
        return m.get("status", "ACTIVE")

    # --- status mutations (admin-driven on confirmed reports) ---
    def set_status(self, username: str, status: str) -> dict:
        m = self.ledger._m(username)
        m["status"] = status
        if status == "REMOVED":            # kick + clear credits + block
            m["balance"] = 0
            m["earned"] = 0
            m["spent"] = 0
        self.ledger.save()
        return m

    def mark_offered(self, username: str):
        m = self.ledger._m(username)
        m["offered"] = m.get("offered", 0) + 1
        self.ledger.save()

    def mark_posted(self, username: str):
        m = self.ledger._m(username)
        m["posted"] = m.get("posted", 0) + 1
        self.ledger.save()

    def mark_invalid(self, username: str):
        m = self.ledger._m(username)
        m["invalid_posts"] = m.get("invalid_posts", 0) + 1
        self.ledger.save()

    # --- like-with-like matching by performance band ---
    def match(self, sender: str, want_channels: int,
              post_type: str = "General", min_status="ACTIVE") -> list[str]:
        """Same performance BAND as the sender, ignoring subscriber size.
        Subscriber size is only a tiebreaker. Only ACTIVE (and better) channels
        listed as targets by default."""
        if self._blocked(sender):
            return []
        m = self.ledger._m(sender)
        sender_band = self.score(sender)["band"]
        pool = []
        for username, mm in self.ledger.ledger.items():
            if username == sender:
                continue
            if self._blocked(username) and username != OWNER_USERNAME:
                continue
            if self.score(username)["band"] != sender_band:
                continue
            pool.append(username)
        # tiebreak: similar size preferred; then sort by size closeness
        ssize = m.get("size", 0)
        pool.sort(key=lambda u: abs(self.ledger._m(u).get("size", 0) - ssize))
        return pool[:want_channels]

    def _blocked(self, username: str) -> bool:
        return self.status(username) in ("RESTRICTED", "REMOVED")



# ---------------------------------------------------------------------------
# DISTRIBUTION (backward-compatible wrapper; now matches by PERFORMANCE)
# ---------------------------------------------------------------------------
class Distribution:
    """Thin compatibility wrapper. Real matching now lives in PerformanceEngine.
    `candidates()` is kept for existing callers but delegates to like-with-like
    performance matching (subscriber size is no longer the match key)."""

    def __init__(self, ledger: CreditLedger, engine: "PerformanceEngine" | None = None):
        self.ledger = ledger
        self.engine = engine if engine is not None else PerformanceEngine(ledger, ledger.store)

    def candidates(self, sender: str, post_type: str, tier: str,
                   want_channels: int) -> list[str]:
        """Like-with-like by performance band. The `tier` arg is accepted for
        backward compatibility but is no longer the matching criterion."""
        return self.engine.match(sender, want_channels=want_channels, post_type=post_type)
