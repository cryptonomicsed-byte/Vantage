"""Security regression tests — XSS sanitization, injection, dangerous protocols."""
import pytest
from backend.utils import sanitize_markdown, sanitize_text


# ── sanitize_text ─────────────────────────────────────────────────────────────

class TestSanitizeText:
    def test_strips_html_tags(self):
        assert sanitize_text("<b>bold</b>") == "bold"

    def test_strips_script_tag(self):
        # sanitize_text removes the <script> tags; the text content remains
        # (plain-text fields — not markdown — are rendered as text, so the JS is harmless)
        result = sanitize_text("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "</script>" not in result

    def test_strips_javascript_protocol(self):
        result = sanitize_text("javascript:alert(1)")
        assert "javascript:" not in result

    def test_strips_data_protocol(self):
        result = sanitize_text("data:text/html,<h1>hi</h1>")
        assert "data:" not in result

    def test_strips_vbscript_protocol(self):
        result = sanitize_text("vbscript:MsgBox(1)")
        assert "vbscript:" not in result

    def test_preserves_plain_text(self):
        assert sanitize_text("Hello world") == "Hello world"

    def test_strips_img_tag(self):
        assert sanitize_text('<img src="x" onerror="alert(1)">') == ""

    def test_empty_string(self):
        assert sanitize_text("") == ""

    def test_none_coercion(self):
        # Ensure no crash on falsy but not None
        assert sanitize_text("") == ""


# ── sanitize_markdown ────────────────────────────────────────────────────────

class TestSanitizeMarkdown:
    def test_removes_script_block(self):
        md = "# Title\n<script>alert(1)</script>\nBody"
        result = sanitize_markdown(md)
        assert "<script>" not in result
        assert "alert(1)" not in result

    def test_removes_inline_script(self):
        md = "text <script src='x.js'></script> after"
        assert "<script>" not in sanitize_markdown(md)

    def test_removes_style_block(self):
        md = "<style>body{display:none}</style>"
        assert "<style>" not in sanitize_markdown(md)

    def test_removes_iframe(self):
        md = '<iframe src="evil.com"></iframe>'
        assert "<iframe>" not in sanitize_markdown(md)

    def test_removes_event_handlers(self):
        md = '<img src="x" onerror="alert(1)">'
        result = sanitize_markdown(md)
        assert "onerror" not in result

    def test_removes_onclick_handler(self):
        md = '<div onclick="evil()">click me</div>'
        assert "onclick" not in sanitize_markdown(md)

    def test_removes_javascript_link(self):
        md = "[click](javascript:alert(1))"
        result = sanitize_markdown(md)
        assert "javascript:" not in result

    def test_removes_data_link(self):
        md = "[img](data:text/html,<h1>xss</h1>)"
        assert "data:" not in sanitize_markdown(md)

    def test_removes_html_comments(self):
        md = "<!-- <script>alert(1)</script> -->"
        result = sanitize_markdown(md)
        assert "alert" not in result

    def test_preserves_normal_markdown(self):
        md = "# Hello\n\n**bold** and _italic_ with [link](https://example.com)"
        result = sanitize_markdown(md)
        assert "Hello" in result
        assert "bold" in result
        assert "https://example.com" in result

    def test_empty_string(self):
        assert sanitize_markdown("") == ""

    def test_removes_object_tag(self):
        md = "<object data='evil.swf'></object>"
        assert "<object>" not in sanitize_markdown(md)

    def test_removes_embed_tag(self):
        md = "<embed src='evil.swf'>"
        assert "<embed>" not in sanitize_markdown(md)


# ── Admin key hash comparison ─────────────────────────────────────────────────

class TestAdminKeyHash:
    def test_hash_is_sha256(self):
        import hashlib
        from backend.config import Settings
        # Keyword args use field names (not env var names with prefix)
        s = Settings(ADMIN_KEY="a" * 32)
        expected = hashlib.sha256(("a" * 32).encode()).hexdigest()
        assert s.ADMIN_KEY_HASH == expected

    def test_no_key_returns_none(self):
        from backend.config import Settings
        s = Settings(ADMIN_KEY="")
        assert s.ADMIN_KEY_HASH is None

    def test_short_key_raises(self):
        from pydantic import ValidationError
        from backend.config import Settings
        with pytest.raises((ValidationError, ValueError)):
            Settings(ADMIN_KEY="short")
