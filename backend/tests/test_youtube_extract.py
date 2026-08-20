"""youtube_extract.py — video-ID parsing and GitHub-URL extraction.

Only the pure, deterministic logic is covered here (no network, no yt-dlp
subprocess) -- video ID parsing and description->repo-URL regex extraction
are exactly the parts that must be exactly right regardless of which
fetch method (yt-dlp vs. HTTP fallback) produced the description, and the
parts that are actually reusable unchanged by a future API-key channel
watcher per the module's own design.
"""
import pytest

from backend.youtube_extract import extract_video_id, extract_github_urls


# ── extract_video_id ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("  dQw4w9WgXcQ  ", "dQw4w9WgXcQ"),
])
def test_extract_video_id_accepts_real_formats(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize("bad", [
    "", "not a url", "https://vimeo.com/12345678",
    "https://www.youtube.com/", "https://www.youtube.com/@somechannel",
    "short", "toolongtobeanid12345",
])
def test_extract_video_id_rejects_non_video_input(bad):
    assert extract_video_id(bad) is None


# ── extract_github_urls ──────────────────────────────────────────────────

def test_extracts_single_github_url():
    text = "Check out the code: https://github.com/pytorch/pytorch"
    assert extract_github_urls(text) == ["https://github.com/pytorch/pytorch"]


def test_extracts_multiple_distinct_urls_in_order():
    text = (
        "Repo 1: https://github.com/facebook/react\n"
        "Repo 2: https://github.com/vuejs/vue\n"
    )
    assert extract_github_urls(text) == [
        "https://github.com/facebook/react",
        "https://github.com/vuejs/vue",
    ]


def test_dedupes_repeated_urls():
    text = (
        "https://github.com/torvalds/linux is great.\n"
        "Again: https://github.com/torvalds/linux\n"
    )
    assert extract_github_urls(text) == ["https://github.com/torvalds/linux"]


def test_strips_trailing_sentence_punctuation():
    text = "Source code (https://github.com/owner/repo)."
    assert extract_github_urls(text) == ["https://github.com/owner/repo"]


def test_strips_trailing_comma_and_period():
    text = "See https://github.com/owner/repo, https://github.com/owner/repo2."
    assert extract_github_urls(text) == [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo2",
    ]


def test_preserves_literal_dot_git_suffix():
    text = "Clone it: https://github.com/owner/repo.git"
    assert extract_github_urls(text) == ["https://github.com/owner/repo.git"]


def test_no_github_links_returns_empty_list():
    text = "This video has no source code links, just a music video."
    assert extract_github_urls(text) == []


def test_ignores_non_github_urls():
    text = "Follow me: https://twitter.com/someone and https://gitlab.com/owner/repo"
    assert extract_github_urls(text) == []


def test_http_scheme_input_is_matched_but_normalized_to_https():
    """The regex matches an http:// link too (real descriptions have old
    links), but the output is always normalized to https:// -- documented
    module behavior, not scheme-preserving."""
    text = "Old link: http://github.com/owner/repo"
    assert extract_github_urls(text) == ["https://github.com/owner/repo"]


def test_handles_real_video_description_shape():
    """Regression fixture: a real description shape captured from the
    live dQw4w9WgXcQ end-to-end test (2026-08-19) -- confirms the regex
    doesn't choke on a realistic multi-line description with no repo
    links, matching what a genuine non-code video looks like."""
    text = (
        "Official music video for “Never Gonna Give You Up” by Rick Astley\n\n"
        "“Never Gonna Give You Up” was a global smash on its release in "
        "July 1987...\n\nFollow Rick Astley:\n"
        "Facebook: https://www.facebook.com/rickastley\n"
        "Twitter: https://twitter.com/rickastley\n"
    )
    assert extract_github_urls(text) == []
