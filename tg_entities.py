"""Telegram MessageEntity helpers for broadcast drafts (decision 2026-09-09 #3).

Formatting is never hand-typed. An admin composes the message *in Telegram*
and sends it to the admin bot; the bot captures ``text`` + ``entities`` exactly
as Telegram parsed them. The draft stores that pair and delivery calls
``send_message(text=..., entities=...)`` with no parse_mode, so what the admin
saw is what every recipient gets. Ads are excluded by decision.

Offsets/lengths are in UTF-16 code units (Telegram's rule), which is why the
preview renderer below slices a UTF-16 buffer instead of the Python string.
"""
from __future__ import annotations

from html import escape

# Entity types the platform will store and replay. Anything else (custom emoji,
# text_mention with user payloads, pre with language, …) is dropped at capture
# so a draft never carries data we do not render or understand.
ALLOWED_TYPES = frozenset({
    "bold", "italic", "underline", "strikethrough", "spoiler", "code", "pre",
    "text_link", "url", "mention", "hashtag", "cashtag", "bot_command", "email",
    "phone_number", "blockquote", "expandable_blockquote",
})
_MAX_ENTITIES = 100


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def sanitize_entities(text: str, entities) -> list[dict]:
    """Return only well-formed, in-range, allowed entities as plain dicts.
    Accepts aiogram ``MessageEntity`` objects or dicts. Raises ValueError when
    the input is not a list or is oversized; silently drops unsupported types."""
    if entities is None:
        return []
    if not isinstance(entities, (list, tuple)):
        raise ValueError("entities must be a list")
    if len(entities) > _MAX_ENTITIES:
        raise ValueError(f"at most {_MAX_ENTITIES} entities are allowed")
    limit = utf16_len(text)
    out: list[dict] = []
    for ent in entities:
        d = ent.model_dump(exclude_none=True) if hasattr(ent, "model_dump") else dict(ent)
        etype = d.get("type")
        if etype not in ALLOWED_TYPES:
            continue
        try:
            offset, length = int(d["offset"]), int(d["length"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("every entity needs integer offset and length")
        if offset < 0 or length <= 0 or offset + length > limit:
            raise ValueError("entity range is outside the text")
        clean = {"type": etype, "offset": offset, "length": length}
        if etype == "text_link":
            url = str(d.get("url") or "")
            if not url.lower().startswith(("http://", "https://", "tg://")):
                raise ValueError("text_link must carry an http(s) or tg:// url")
            clean["url"] = url
        if etype == "pre" and d.get("language"):
            clean["language"] = str(d["language"])[:32]
        out.append(clean)
    return out


_TAGS = {
    "bold": ("<b>", "</b>"), "italic": ("<i>", "</i>"), "underline": ("<u>", "</u>"),
    "strikethrough": ("<s>", "</s>"), "spoiler": ("<span class=\"tg-spoiler\">", "</span>"),
    "code": ("<code>", "</code>"), "pre": ("<pre>", "</pre>"),
    "blockquote": ("<blockquote>", "</blockquote>"), "expandable_blockquote": ("<blockquote>", "</blockquote>"),
}


def to_html(text: str, entities: list[dict] | None) -> str:
    """Escaped HTML preview of text + entities (for the Mini App only — never
    sent to Telegram). Handles nesting by opening/closing tags at boundaries."""
    ents = sanitize_entities(text, entities or [])
    buf = text.encode("utf-16-le")
    n = len(buf) // 2
    opens: dict[int, list[dict]] = {}
    closes: dict[int, list[dict]] = {}
    for e in ents:
        opens.setdefault(e["offset"], []).append(e)
        closes.setdefault(e["offset"] + e["length"], []).append(e)
    # Longer entities open first / close last so nesting is well-formed.
    for lst in opens.values():
        lst.sort(key=lambda e: -e["length"])
    for lst in closes.values():
        lst.sort(key=lambda e: e["length"])

    def open_tag(e):
        if e["type"] == "text_link":
            return f'<a href="{escape(e["url"], quote=True)}">'
        return _TAGS.get(e["type"], ("", ""))[0]

    def close_tag(e):
        if e["type"] == "text_link":
            return "</a>"
        return _TAGS.get(e["type"], ("", ""))[1]

    parts: list[str] = []
    i = 0
    while i <= n:
        for e in closes.get(i, []):
            parts.append(close_tag(e))
        for e in opens.get(i, []):
            parts.append(open_tag(e))
        if i < n:
            unit = buf[2 * i:2 * i + 2]
            # Surrogate pairs: emit both halves together, advance two units.
            cu = int.from_bytes(unit, "little")
            if 0xD800 <= cu <= 0xDBFF and i + 1 < n:
                parts.append(escape(buf[2 * i:2 * i + 4].decode("utf-16-le")))
                i += 2
                continue
            parts.append(escape(unit.decode("utf-16-le", errors="replace")))
        i += 1
    return "".join(parts).replace("\n", "<br>")
