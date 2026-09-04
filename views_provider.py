"""Optional MTProto "observer" — the only way to read real channel post views.

The Bot API cannot read channel post views. Views require the MTProto layer
(Telethon / a user session). This observer follows partner channels and reads
their post view/forward counts via `GetMessagesViewsRequest`, using
`increment=False` so it never inflates the count.

This is OPTIONAL. If you don't wire it up, the PerformanceEngine degrades to the
reliability proxy (which is still useful). Enable it when you're ready.

Setup:
  1. Create API creds at https://my.telegram.org (api_id / api_hash).
  2. Decide the observer account: a dedicated Telegram account that will FOLLOW
     the partner channels so it can read their posts. (It must be a member of /
     able to see each channel you want to measure.)
  3. Keep volume low and measurement read-only. This reads public counters; it
     is NOT a spam/scrape-invade tool.

Usage (return shape that PerformanceEngine.views_provider expects):
    provider = make_observer_provider(api_id, api_hash, session_name)
    perf.views_provider = provider
    provider("@SomeChannel")  # -> {"views": int, "forwards": int, "subs": int}

CAVEAT: only channels the observer can VIEW give real data. Channels you can't
see return None -> the engine falls back to the proxy for that channel.
"""
from __future__ import annotations
import asyncio

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetMessagesViewsRequest
    _HAS_TELE = True
except ImportError:
    _HAS_TELE = False


def make_observer_provider(api_id: int, api_hash: str, session_name: str = "observer"):
    """Return a callable provider for PerformanceEngine. If telethon is missing,
    raise with a clear message."""
    if not _HAS_TELE:
        raise RuntimeError("Telethon not installed. `pip install telethon` to enable live views.")

    client = TelegramClient(session_name, api_id, api_hash)

    def _run(coro):
        # telethon needs a running loop; use a fresh event loop per call (simple + safe)
        return asyncio.run(_run_coro(coro))

    async def _run_coro(coro):
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Observer session not authorized. Log in once first.")
        return await coro

    async def _fetch(username: str) -> dict:
        # Resolve the channel; skip channels the observer can't resolve/view.
        try:
            entity = await client.get_entity(username)
        except Exception:
            return {"views": None}
        subs = getattr(entity, "subscribers_count", 0) or 0
        # Read recent posts' views (non-increasing).
        msgs = await client.get_messages(entity, limit=10)
        ids = [m.id for m in msgs if m.id]
        if not ids:
            return {"views": 0, "forwards": 0, "subs": subs}
        res = await client(GetMessagesViewsRequest(peer=entity, id=ids, increment=False))
        # avg views over the last messages as the post-level performance signal
        views = sum(getattr(v, "views", 0) for v in res.views) / len(res.views)
        forwards = sum(getattr(v, "forwards", 0) for v in res.views)
        return {"views": round(views), "forwards": forwards, "subs": subs}

    def provider(username: str) -> dict:
        # executor-safe synchronous wrapper the engine can call
        return _run(_fetch(username))

    # Start the client and log in once (interactive) if needed
    async def _boot():
        await client.start()
    try:
        asyncio.run(_boot())
    except Exception:
        pass
    return provider
