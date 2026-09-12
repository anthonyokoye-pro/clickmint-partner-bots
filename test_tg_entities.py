"""Broadcast formatting = Telegram entities only (decision 2026-09-09 #3)."""
from tg_entities import sanitize_entities, to_html, utf16_len


def test_entities_are_validated_against_utf16_text():
    text = "Hi 👋 bold link"
    assert utf16_len(text) == 15                       # emoji is two UTF-16 units
    ents = sanitize_entities(text, [{"type": "bold", "offset": 6, "length": 4},
                                    {"type": "text_link", "offset": 11, "length": 4, "url": "https://x.y"},
                                    {"type": "custom_emoji", "offset": 3, "length": 2, "custom_emoji_id": "1"}])
    assert [e["type"] for e in ents] == ["bold", "text_link"], "unsupported types are dropped, not stored"
    for bad in ([{"type": "bold", "offset": 14, "length": 3}],                 # runs past the end
                [{"type": "text_link", "offset": 0, "length": 2, "url": "javascript:alert(1)"}],
                [{"type": "bold", "offset": "x", "length": 1}]):
        try:
            sanitize_entities(text, bad); raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    print("PASS entities are range-checked in UTF-16 units, unsafe links refused")


def test_preview_html_escapes_and_nests():
    text = "<b>not markup</b> 👋 bold"
    assert utf16_len(text) == 25
    html = to_html(text, [{"type": "bold", "offset": 21, "length": 4},
                          {"type": "italic", "offset": 21, "length": 2}])
    assert "&lt;b&gt;not markup&lt;/b&gt;" in html, html          # typed tags are NOT formatting
    assert html.endswith("<b><i>bo</i>ld</b>"), html
    assert "👋" in html
    print("PASS preview escapes typed markup and renders entities as nested tags")


TESTS = [test_entities_are_validated_against_utf16_text, test_preview_html_escapes_and_nests]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print(f"\nALL TG ENTITY TESTS PASSED ({len(TESTS)})")
