"""CLICKMINT governance — pure, offline-testable rules layer.

Adds the NEW feature logic on top of core.py, kept free of any Telegram code so it
can be unit-tested exactly like the rest of the engine:

  1) POST LIMIT     — a daily cap that scales with BOTH subscriber size and the
                      channel's performance band (high performers get more slots).
  2) SUBMISSION GATE — the terms & regulations you asked for, applied to every post
                      before it reaches a partner channel:
                        • niche/category must be declared AND match the target
                        • no spam / money-asking / scam / fraud
                        • no third-party ad-service posts
                        • borderline -> human Review queue (never auto-ban on one hit)
  3) ADVICE TIP     — the "put your @username at the bottom" tip (advice only, never enforced).
  4) PARTNER CONTRACT — peer-to-peer (partner <-> partner) contract so BOTH are bound,
                        with the owner as mediator / registrar (notified on open,
                        renew, and close; the owner gives the final close).
  5) ROLE REGISTRY  — owner / scoped-admin / user, entered via a one-time invite
                        code (an admin cannot self-request; only the owner grants).
  6) REVIEW QUEUE   — the human step behind the submission gate.

Everything here runs on plain dicts + a JsonStore, so test_governance.py can run it
with no bot and no network.
"""
from __future__ import annotations
import re
import time
import uuid

# Reuse the existing post-type list as the NICHE CATEGORY list.
import core as _core
POST_CATEGORIES = list(_core.POST_TYPES)

# A channel cannot post about anything outside this list, and the category MUST be
# one the target channel has agreed to receive.
ALLOWED_CATEGORIES = set(POST_CATEGORIES)


# ---------------------------------------------------------------------------
# 1) POST LIMIT (size + performance)
# ---------------------------------------------------------------------------
# Subscriber size -> base daily slots.
SIZE_BASE_CAP = [
    (1000, 1),      # < 1,000 subs  -> 1 slot
    (2500, 2),      # 1,000-2,499   -> 2
    (5000, 3),      # 2,500-4,999   -> 3
    (None, 4),      # 5,000+        -> 4
]
# Performance band -> multiplier.
CAP_BAND_MULT = {"A": 1.0, "B": 0.75, "C": 0.5}


def base_cap_for_size(size: int) -> int:
    for limit, cap in SIZE_BASE_CAP:
        if limit is None or size < limit:
            return cap
    return SIZE_BASE_CAP[-1][1]


def daily_post_cap(size: int, band: str, status: str = "ACTIVE",
                   is_owner: bool = False, connected: bool = True) -> int:
    """Daily slot cap = size base x performance multiplier.

    Rules:
      • owner is UNLIMITED (returns a large sentinel, -1 = unlimited).
      • non-active channels (RESTRICTED / REMOVED) get 0.
      • unverified destinations receive exactly 0 slots (fail closed).
      • verified channels use size + performance and get a hard floor of 1.
    """
    if status in ("RESTRICTED", "REMOVED"):
        return 0
    # Participation is fail-closed for every account, including the owner.
    if not connected:
        return 0
    if is_owner:
        return -1                       # unlimited after verification
    base = base_cap_for_size(size)
    n = int(base * CAP_BAND_MULT.get(band, 0.5))
    return max(1, n)


def daily_cap_text(size: int, band: str, status: str = "ACTIVE",
                   is_owner: bool = False, connected: bool = False) -> str:
    cap = daily_post_cap(size, band, status, is_owner, connected=connected)
    if cap == -1:
        return "unlimited (owner)"
    return str(cap)


# ---------------------------------------------------------------------------
# 2) SUBMISSION GATE — terms & regulations
# ---------------------------------------------------------------------------
# Hard violations -> the bot always blocks (clear fraud / money-asking / scam /
# phishing / free-stuff spam / third-party ad-service).
HARD_BLOCK_PATTERNS = [
    # money-asking / fraud / scam
    "seed phrase", "private key", "backup code", "recovery phrase", "phishing",
    "activation fee", "withdrawal fee", "fee to withdraw", "pay to unlock",
    "deposit to receive", "send money", "wire transfer", "wire it",
    "guaranteed return", "guaranteed profit", "guaranteed roi",
    "risk free profit", "double your money", "double your investment",
    "100x", "1000x", "x100", "x1000", "make 100x", "guaranteed 10x",
    # "claim your free" / scammy giveaway pledges
    "claim your free", "free crypto giveaway", "win free crypto", "get free crypto",
    # phishing bait
    "dm me", "dm us", "dm @", "message me", "telegram me", "contact my assistant",
    # third-party ad-service / advert networks
    "adservice", "ad service", "ad network", "advertise your channel",
    "promote your channel", "paid promotion", "boost your channel",
    "sponsored advertisement", "buy followers", "buy subscribers",
]
# Ambiguous but suspicious -> route to a HUMAN review (never auto-blocked).
SOFT_REVIEW_PATTERNS = [
    "guaranteed", "risk-free", "risk free", "profitable", "passive income",
    "earn money", "make money", "instant profit", "no loss", "100% win",
    "limited time offer", "offer expires", "only today", "hurry", "act now",
    "claim", "giveaway", "bonus", "cashback", "reward", "prize", "payout",
    "deposit", "invest", "withdraw", "donate",
]
# Terms & regulations text shown to every sender BEFORE they can submit a post.
TERMS_TEXT = (
    "📜 <b>MINT POST EXCHANGE POSTING TERMS</b>\n\n"
    "You must accept every rule before submitting a post.\n\n"
    "1️⃣ <b>NICHE-RELATED</b>\n"
    "Your post must use a valid category and match what the receiving destination "
    "accepts. No off-niche content.\n\n"
    "2️⃣ <b>NO SPAM · NO MONEY-ASKING · NO SCAMS</b>\n"
    "No requests for money, free-stuff or airdrop bait, guaranteed returns, fraud, "
    "phishing, or seed-phrase tricks.\n\n"
    "3️⃣ <b>NO THIRD-PARTY AD POSTS</b>\n"
    "Do not share ad-service or paid-promotion posts for another network.\n\n"
    "4️⃣ <b>ATTRIBUTION ADVICE</b> <i>(optional)</i>\n"
    "Adding your @username can help people find you. It is never required.\n\n"
    "5️⃣ <b>VIOLATIONS</b>\n"
    "Obvious violations are blocked. Ambiguous cases go to human review. Repeated "
    "violations can lower a destination's performance band or status.\n\n"
    "Tap ✅ <b>Accept &amp; Continue</b> to proceed."
)


def _pattern_regex(pattern: str):
    """Compile a pattern with word boundaries where they make sense.

    Plain substring matching hard-blocks innocent posts: "reclaimed" contains
    "claim", "$100xyz" contains "100x", "broadcast" contains "adca"… A wrongly
    refused post costs a real member a slot, so boundaries are applied at each
    end that is a word character (patterns like "dm @" keep their open end).
    """
    left = r"(?<!\w)" if pattern[:1].isalnum() else ""
    right = r"(?!\w)" if pattern[-1:].isalnum() else ""
    return re.compile(left + re.escape(pattern) + right, re.IGNORECASE)


_HARD_RE = [(p, _pattern_regex(p)) for p in HARD_BLOCK_PATTERNS]
_SOFT_RE = [(p, _pattern_regex(p)) for p in SOFT_REVIEW_PATTERNS]


def matched_patterns(text: str) -> tuple[list[str], list[str]]:
    """Return (hard hits, soft hits) — useful for the review queue's reason."""
    t = text or ""
    hard = [p for p, rx in _HARD_RE if rx.search(t)]
    soft = [p for p, rx in _SOFT_RE if rx.search(t)]
    return hard, soft


def classify_submission(text: str) -> str:
    """Return 'block' | 'review' | 'pass'.

    • any HARD_BLOCK hit            -> 'block' (bot refuses the POST — note that
                                       refusing a post is never a ban; only a
                                       human can act against a channel)
    • else any SOFT_REVIEW hit      -> 'review' (human decides)
    • else                          -> 'pass'
    """
    hard, soft = matched_patterns(text)
    if hard:
        return "block"
    if soft:
        return "review"
    return "pass"


class SubmissionGate:
    """Applies the terms to a submission. Pure logic; the bot does the messaging."""

    def __init__(self, store):
        self.store = store

    def gate(self, category: str, text: str,
             target_receive_types: list[str]) -> tuple[bool, str, str]:
        """Validate a submission.

        Returns (ok, verdict, reason) where verdict in {'pass','block','review'}.
        ok is True only when the bot may accept it now (verdict 'pass'); 'review'
        entries are accepted into the human queue, 'block' entries are refused.
        """
        category = (category or "").strip()
        if category not in ALLOWED_CATEGORIES:
            return False, "block", f"Invalid category '{category}' — must be one of {POST_CATEGORIES}"
        rec = target_receive_types or []
        if rec and category not in rec and "General" not in rec:
            return False, "block", (f"'{category}' is not a category this channel receives "
                                    f"(it accepts {rec}).")
        verdict = classify_submission(text)
        if verdict == "block":
            return False, "block", ("This post breaks the terms (scam / money-asking / "
                                    "ad-service). It was refused.")
        if verdict == "review":
            return True, "review", "Accepted for HUMAN review (borderline pattern)."
        return True, "pass", "ok"

    # The optional attribution tip — advice ONLY, never a requirement.
    @staticmethod
    def attribution_tip() -> str:
        return ("💡 Tip (optional, not required): add your @username at the bottom of "
                "your post so people who like it can find and join you.")


# ---------------------------------------------------------------------------
# 3) REVIEW QUEUE — the human step behind the gate
# ---------------------------------------------------------------------------
class ReviewQueue:
    def __init__(self, store, key="review_queue"):
        self.store = store
        self.key = key
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def submit(self, category: str, sender: str, text: str, source: str,
               target: str = "", reason: str = "") -> dict:
        item = {
            "id": self._next_id(),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category, "sender": sender, "target": target,
            "text": (text or "")[:600], "source": source,
            "reason": reason, "status": "pending",   # pending | approved | rejected
        }
        items = self.store[self.key]
        items.append(item)
        self.store[self.key] = items
        self.store.sync()
        return item

    def _next_id(self) -> int:
        """Monotonic id. `len()+1` re-issues an id as soon as anything is pruned
        from the queue, and two items sharing an id make approve/reject ambiguous."""
        ids = [i.get("id", 0) for i in self.store[self.key]
               if isinstance(i.get("id"), int)]
        return (max(ids) + 1) if ids else 1

    def pending(self) -> list:
        return [i for i in self.store[self.key] if i.get("status") == "pending"]

    def get(self, rid: int) -> dict | None:
        for i in self.store[self.key]:
            if i.get("id") == rid:
                return i
        return None

    def decide(self, rid: int, approve: bool, note: str = "") -> dict | None:
        for i in self.store[self.key]:
            if i.get("id") == rid:
                i["status"] = "approved" if approve else "rejected"
                i["decided_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                i["decision_note"] = note
                self.store[self.key] = self.store[self.key]
                self.store.sync()
                return i
        return None

    # --- sender notification (approve/reject) ---
    def mark_notify(self, rid: int, via: str = "reward") -> bool:
        """Flag an item so the matching network bot DMs the sender the outcome.
        (The admin bot can't DM a sender it never chatted with, so the queue is
        handed to the bot that actually owns that sender's chat, identified by `via`.)"""
        it = self.get(rid)
        if not it:
            return False
        it["notify_pending"] = True
        it["notify_via"] = via
        self.store[self.key] = self.store[self.key]
        self.store.sync()
        return True

    def pending_notify(self, via: str | None = None) -> list:
        out = [i for i in self.store[self.key] if i.get("notify_pending")]
        if via:
            out = [i for i in out if i.get("notify_via") == via]
        return out

    def clear_notify(self, rid: int) -> None:
        it = self.get(rid)
        if it:
            it["notify_pending"] = False
            self.store[self.key] = self.store[self.key]
            self.store.sync()


# ---------------------------------------------------------------------------
# 4) PARTNER CONTRACT (peer-to-peer, owner-as-mediator)
# ---------------------------------------------------------------------------
PARTNER_STATUSES = ["ACTIVE", "RENEWED", "CLOSE_REQUESTED", "CLOSED"]


class PartnerContractRegistry:
    """Holds partner<->partner contracts. The OWNER is the registrar/mediator:
    they are notified on open, on renewal, and on close; a close is only FINAL once
    the owner confirms it (so neither side can just vanish without a record)."""

    def __init__(self, store, key="partner_contracts"):
        self.store = store
        self.key = key
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def open(self, a: str, b: str, fields: dict) -> dict:
        rec = {
            "id": uuid.uuid4().hex[:10],
            "a": a, "b": b,                       # the two partner usernames
            "contract": fields,                   # see fields below
            "status": "ACTIVE",
            "opened": time.strftime("%Y-%m-%d %H:%M:%S"),
            "renewed": [], "close_requested": None, "closed": None,
        }
        items = self.store[self.key]
        items.append(rec)
        self.store[self.key] = items
        self.store.sync()
        return rec

    def _find(self, cid: str) -> dict | None:
        for r in self.store[self.key]:
            if r.get("id") == cid:
                return r
        return None

    def self_contract_fields(self) -> str:
        return (
            "Agree these fields to open a partnership:\n"
            "  • post type(s) allowed (category list)\n"
            "  • volume (posts / day or / week or / month)\n"
            "  • posting schedule (agreed time/s, e.g. Mon & Thu 12:00 UTC)\n"
            "  • delivery preference: forward / direct / pin\n"
            "  • loud or silent post\n"
            "  • duration (e.g. 30 days) + auto-renew yes/no\n"
            "  • terms applied (niche-only, no-ads, no-spam, safety)\n"
            "  • exit: either may close after a notice period if not benefiting"
        )

    def request_close(self, cid: str, by: str, reason: str) -> dict | None:
        """One partner asks to close. Notifies the owner. NOT final yet."""
        r = self._find(cid)
        if not r or r.get("status") == "CLOSED":
            return None
        r["status"] = "CLOSE_REQUESTED"
        r["close_requested"] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "by": by, "reason": reason}
        self.store[self.key] = self.store[self.key]
        self.store.sync()
        return r

    def finalize_close(self, cid: str, by: str, note: str = "") -> dict | None:
        """Owner confirms the close -> record is final. Returns the record."""
        r = self._find(cid)
        if not r:
            return None
        r["status"] = "CLOSED"
        r["closed"] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "by": by, "note": note}
        self.store[self.key] = self.store[self.key]
        self.store.sync()
        return r

    def renew(self, cid: str, note: str = "") -> dict | None:
        r = self._find(cid)
        if not r or r.get("status") == "CLOSED":
            return None
        r["status"] = "RENEWED"
        r["renewed"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "note": note})
        self.store[self.key] = self.store[self.key]
        self.store.sync()
        return r

    def list(self, status: str | None = None) -> list:
        items = self.store[self.key]
        if status:
            return [r for r in items if r.get("status") == status]
        return items

    def between(self, a: str, b: str) -> list:
        return [r for r in self.store[self.key]
                if {r.get("a"), r.get("b")} == {a, b}]

    def get(self, cid: str) -> dict | None:
        return self._find(cid)


# ---------------------------------------------------------------------------
# 5) ROLE REGISTRY — owner / scoped-admin / user, invite-code login
# ---------------------------------------------------------------------------
SCOPES = ["reward", "partnership"]      # admin can control one or both


class RoleRegistry:
    """role entries keyed by telegram user id.

    user:
      { "role": "owner"|"admin"|"user", "scope": [] (admin only),
        "granted_by": ..., "active": bool }

    invites:
      { "<code>": { "scope": [...], "used": bool, "created_by": ... } }
    """

    def __init__(self, store, owner_user_id):
        self.store = store
        self.owner_user_id = str(owner_user_id)
        self.key_users = "roles_users"
        self.key_invites = "roles_invites"
        if self.store.get(self.key_users) is None:
            self.store[self.key_users] = {}
            self.store.sync()
        if self.store.get(self.key_invites) is None:
            self.store[self.key_invites] = {}
            self.store.sync()

    def _u(self, uid) -> dict:
        return self.store[self.key_users].get(str(uid), {})

    def is_owner(self, uid) -> bool:
        return str(uid) == self.owner_user_id

    def role(self, uid) -> str:
        if self.is_owner(uid):
            return "owner"
        return self._u(uid).get("role", "user")

    def is_admin(self, uid, scope: str | None = None) -> bool:
        """True if uid is admin and, if scope given, has that scope."""
        u = self._u(uid)
        if u.get("role") != "admin" or u.get("active") is False:
            return False
        if scope is None:
            return True
        return scope in u.get("scope", [])

    def has_access(self, uid, scope: str) -> bool:
        """owner always passes; admin only within scope."""
        return self.is_owner(uid) or self.is_admin(uid, scope)

    # --- owner-side admin management ---
    def create_invite(self, scope: list[str], created_by=None) -> str:
        code = uuid.uuid4().hex[:10].upper()
        inv = self.store[self.key_invites]
        inv[code] = {"scope": [s for s in scope if s in SCOPES],
                     "used": False, "created_by": created_by}
        self.store[self.key_invites] = inv
        self.store.sync()
        return code

    def redeem_invite(self, uid, code: str) -> tuple[bool, str]:
        """Admin enters a one-time invite code -> becomes admin with that scope.
        (They must CONTACT the owner first; owner issues the code. Not self-serve.)"""
        code = (code or "").upper()
        inv = self.store[self.key_invites]
        rec = inv.get(code)
        if not rec:
            return False, "Invalid invite code."
        if rec.get("used"):
            return False, "That invite code was already used."
        rec["used"] = True
        rec["used_by"] = str(uid)
        u = self.store[self.key_users]
        u[str(uid)] = {"role": "admin", "scope": rec.get("scope", []),
                       "granted_by": inv[code].get("created_by"),
                       "active": True}
        self.store[self.key_users] = u
        self.store[self.key_invites] = inv
        self.store.sync()
        return True, f"Granted ADMIN with scope {rec.get('scope', [])}."

    def revoke_admin(self, uid) -> bool:
        u = self.store[self.key_users]
        if str(uid) in u:
            u[str(uid)]["active"] = False
            self.store[self.key_users] = u
            self.store.sync()
            return True
        return False
