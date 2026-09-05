#!/usr/bin/env python3
"""Camoufox-based X/Twitter scraper -- one real, dedicated, human-created
account, session-persisted, reading public profile timelines defensively.

Replaces daemons/x_influencer_bridge.py's xAI x_search dependency per the
owner's explicit 2026-08-30 direction: own the whole pipeline end-to-end,
no external API/subscription dependency. This module owns ONLY the
browser-automation piece; ticker/CA extraction and sentiment stay in
social_tracker.py (import _extract_mentions/_classify_sentiment from
there, never duplicated -- see social_tracker.py's own scan_twitter(),
which now calls into this module instead of the old xurl/X-API path that
had no real credentials anyway).

SCOPE, explicit and enforced by this file: session automation on ONE
real, human-created, already-logged-in dedicated account. This file
contains NO account-creation, NO signup automation, NO CAPTCHA-solving
tuned to X's own challenge system. If X challenges the session (a
CAPTCHA, a suspicious-login checkpoint, a rate-limit wall), the correct
behavior is to STOP and flag for a human, not to attempt an automated
bypass -- shumei_solver.py's slider-solving math is geometrically tuned
to a DIFFERENT site's (teamorouter.com's Shumei) CAPTCHA atlas layout and
would not solve X's own challenge type (typically Arkose FunCaptcha)
even if reused; borrowing it here would mean shipping code that pretends
to handle a CAPTCHA it cannot actually solve. Only the general
human-like-interaction PHILOSOPHY is borrowed, via Camoufox's own native
`humanize` option (confirmed real: camoufox/utils.py's launch_options
wires `humanize`/`humanize:maxTime` straight into the injected
fingerprint config -- this drives real randomized mouse-movement timing
for every interaction, no custom drag math needed here).

DESIGN CHOICE -- direct profile visits, not the notification feed:
Considered both (per the task's own framing) and picked profile visits
because:
  1. A profile page (x.com/<handle>) is a single-purpose timeline --
     X's own DOM has used stable `data-testid` attributes (`tweet`,
     `tweetText`, `User-Name`) on this exact page shape for years,
     which is more resistant to selector rot than a heterogeneous,
     mixed-content-type page.
  2. The notification-feed approach requires FOLLOWING and
     BELL-ENABLING every tracked handle up front -- a real, visible
     burst of "follow" actions from one account is itself a distinct
     automation signal, separate from and in addition to whatever risk
     the actual scraping carries. Profile visits need no such setup.
  3. Notifications interleave many unrelated types (likes, replies,
     mentions, new-posts) requiring more filtering logic to isolate
     the one signal we want -- more surface area for both selector rot
     and false-positive extraction.

DESIGN CHOICE -- no oniux/Tor wrapping (evaluated, rejected for this
specific use case): X has documented history of blocking Tor exit-node
traffic outright (historically at signup; general VPN/Tor IPs are
broadly treated as higher-risk at login per current public reporting).
More importantly, this is a PERSISTENT SESSION tied to one account's
saved cookies -- a consistent IP address across visits is a signal
X's own risk model favors; Tor's per-circuit IP rotation would make one
account's login pattern look like "same session, different IP every
run," a classic account-takeover/bot signal, actively working AGAINST
the goal here. oniux stays available (real, installed, confirmed
working) if a future use case genuinely needs source-IP anonymity more
than session stability, but is deliberately NOT used for this scraper.

Rate discipline (real, enforced client-side, not just documented):
  - MAX_CHECKS_PER_HOUR caps total profile visits per rolling hour.
  - Jittered delay between visits (not a fixed interval).
  - Handle order shuffled each cycle (not a deterministic sequence).
  - A real "read pause" after each page load before extracting, roughly
    simulating a human skimming the page rather than firing an
    immediate DOM query.
  - Any real challenge/detection signal (see detect_challenge()) aborts
    the ENTIRE cycle immediately and sets a real, persisted backoff
    (skip N subsequent cycles) rather than retrying the same or a
    different handle right away.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("x_browser_scraper")

PROFILE_DIR = os.environ.get("X_SCRAPER_PROFILE_DIR", "/opt/ares/x_scraper_profile")
STATE_FILE = os.environ.get("X_SCRAPER_STATE_FILE", "/opt/ares/x_scraper_state.json")

MAX_CHECKS_PER_HOUR = int(os.environ.get("X_SCRAPER_MAX_CHECKS_PER_HOUR", "12"))
MIN_DELAY_S = float(os.environ.get("X_SCRAPER_MIN_DELAY_S", "35"))
MAX_DELAY_S = float(os.environ.get("X_SCRAPER_MAX_DELAY_S", "95"))
READ_PAUSE_MIN_S = 2.5
READ_PAUSE_MAX_S = 7.0
MAX_POSTS_PER_PROFILE = 5

# A cycle that hits a real challenge signal skips this many subsequent
# cycles before trying again -- a deliberate, real cooldown, not a
# retry-immediately loop that would repeat whatever triggered it.
BACKOFF_CYCLES_ON_CHALLENGE = int(os.environ.get("X_SCRAPER_BACKOFF_CYCLES", "6"))

# Real, distinct signals that something is wrong with the session or the
# account is being challenged -- checked after every profile visit, not
# just at startup. Deliberately broad (case-insensitive substring match
# against page text) rather than tied to one exact selector, since the
# whole point is catching novel challenge copy too.
CHALLENGE_SIGNALS = (
    "something went wrong", "try again later", "we've detected unusual",
    "confirm your identity", "verify your identity", "suspicious activity",
    "account has been locked", "account is temporarily", "rate limit exceeded",
    "please verify you", "unusual login activity",
)
LOGIN_WALL_SIGNALS = ("log in to x", "sign in to x", "/login", "/i/flow/login")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Backoff state -- real, persisted (survives process restarts, unlike an
# in-memory counter, which matters here: a challenge signal should not be
# forgotten just because the daemon restarted 10 minutes later).
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"backoff_cycles_remaining": 0, "last_challenge_at": None, "last_challenge_reason": None}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def in_backoff() -> bool:
    return _load_state().get("backoff_cycles_remaining", 0) > 0


def _consume_backoff_tick() -> None:
    state = _load_state()
    if state.get("backoff_cycles_remaining", 0) > 0:
        state["backoff_cycles_remaining"] -= 1
        _save_state(state)


def _trigger_backoff(reason: str) -> None:
    state = _load_state()
    state["backoff_cycles_remaining"] = BACKOFF_CYCLES_ON_CHALLENGE
    state["last_challenge_at"] = _now()
    state["last_challenge_reason"] = reason
    _save_state(state)
    logger.warning("x_browser_scraper: CHALLENGE DETECTED (%s) -- backing off %d cycles", reason, BACKOFF_CYCLES_ON_CHALLENGE)


# ---------------------------------------------------------------------------
# Rate limiter -- real, persisted visit timestamps (rolling hour window),
# so the cap holds across process restarts within the same hour, not just
# within one long-running process's memory.
# ---------------------------------------------------------------------------

_VISIT_LOG_FILE = os.environ.get("X_SCRAPER_VISIT_LOG", "/opt/ares/x_scraper_visits.json")


def _recent_visit_timestamps() -> list:
    try:
        with open(_VISIT_LOG_FILE) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = []
    cutoff = time.time() - 3600.0
    return [t for t in raw if isinstance(t, (int, float)) and t > cutoff]


def _record_visit() -> None:
    kept = _recent_visit_timestamps()
    kept.append(time.time())
    with open(_VISIT_LOG_FILE, "w") as f:
        json.dump(kept, f)


def checks_remaining_this_hour() -> int:
    return max(0, MAX_CHECKS_PER_HOUR - len(_recent_visit_timestamps()))


# ---------------------------------------------------------------------------
# Session bootstrap (one-time, real, needs a real human-created account --
# not run automatically by cycle(), invoked separately via login_once()).
# ---------------------------------------------------------------------------

async def login_once(username: str, password: str, headless: bool = False) -> dict:
    """Real, one-time login flow. Opens a persistent-context Camoufox
    profile at PROFILE_DIR, navigates to x.com/login, fills the real
    credentials, and waits for either a successful landing on the home
    timeline OR a challenge (2FA/CAPTCHA/checkpoint) -- which this
    function does NOT attempt to solve; it screenshots, reports exactly
    what it saw, and returns without asserting success. Run this with
    headless=False (a real visible window, e.g. over VNC/X11 forwarding)
    the first time so a human can personally clear any checkpoint X
    shows -- the persisted profile directory means this only needs to
    happen once; every subsequent cycle() run reuses the saved session.

    Returns {"status": "logged_in" | "challenge" | "failed", "detail": str,
    "screenshot": str | None}. Never raises for a failed/challenged
    login -- that's an expected, reportable outcome, not a bug."""
    from camoufox.async_api import AsyncCamoufox

    os.makedirs(PROFILE_DIR, exist_ok=True)
    async with AsyncCamoufox(
        headless=headless, persistent_context=True, user_data_dir=PROFILE_DIR, humanize=True,
    ) as context:
        page = await context.new_page()
        await page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(random.uniform(2.0, 4.0))

        try:
            user_input = page.locator("input[autocomplete='username']").first
            await user_input.click(force=True, timeout=15000)
            await user_input.press_sequentially(username, delay=random.uniform(60, 140))
            await asyncio.sleep(random.uniform(0.8, 1.6))
            await page.locator("text=Next").first.click(force=True, timeout=15000)
            await asyncio.sleep(random.uniform(1.5, 3.0))
        except Exception as e:
            path = "/tmp/x_login_username_step_failed.png"
            await page.screenshot(path=path, full_page=True)
            return {"status": "failed", "detail": f"username step failed: {e}", "screenshot": path}

        try:
            pass_input = page.locator("input[autocomplete='current-password']").first
            await pass_input.click(force=True, timeout=15000)
            await pass_input.press_sequentially(password, delay=random.uniform(60, 140))
            await asyncio.sleep(random.uniform(0.8, 1.6))
            await page.locator("text=Log in").first.click(force=True, timeout=15000)
            await asyncio.sleep(random.uniform(3.0, 5.0))
        except Exception as e:
            path = "/tmp/x_login_password_step_failed.png"
            await page.screenshot(path=path, full_page=True)
            return {"status": "failed", "detail": f"password step failed (may be a 2FA/challenge prompt instead): {e}", "screenshot": path}

        body_text = (await page.locator("body").inner_text()).lower()
        if any(sig in body_text for sig in CHALLENGE_SIGNALS) or "verify" in body_text and "code" in body_text:
            path = "/tmp/x_login_challenge.png"
            await page.screenshot(path=path, full_page=True)
            return {
                "status": "challenge",
                "detail": "a checkpoint/2FA/verification prompt appeared -- needs a human to complete this step live (re-run with headless=False)",
                "screenshot": path,
            }

        current_url = page.url
        if "/home" in current_url or current_url.rstrip("/") == "https://x.com":
            return {"status": "logged_in", "detail": f"landed on {current_url}, session saved to {PROFILE_DIR}", "screenshot": None}

        path = "/tmp/x_login_unknown_state.png"
        await page.screenshot(path=path, full_page=True)
        return {"status": "failed", "detail": f"unrecognized post-login state at {current_url}", "screenshot": path}


async def check_session_valid() -> bool:
    """Real check: open the persisted profile, visit the home timeline,
    confirm we're not bounced to a login wall. Returns False (not a
    raised error) for "no profile yet" / "session expired" -- both are
    real, expected, actionable states, not exceptions."""
    if not os.path.isdir(PROFILE_DIR):
        return False
    from camoufox.async_api import AsyncCamoufox

    try:
        async with AsyncCamoufox(
            headless=True, persistent_context=True, user_data_dir=PROFILE_DIR, humanize=True,
        ) as context:
            page = await context.new_page()
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.0)
            url = page.url.lower()
            return not any(sig in url for sig in LOGIN_WALL_SIGNALS)
    except Exception as e:
        logger.warning("x_browser_scraper: session check failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Real scraping cycle
# ---------------------------------------------------------------------------

async def detect_challenge(page) -> Optional[str]:
    """Real, defensive check run after every profile visit. Returns a
    human-readable reason string if something looks like a challenge/
    lockout, None if the page looks like an ordinary profile timeline."""
    try:
        url = page.url.lower()
        if any(sig in url for sig in LOGIN_WALL_SIGNALS):
            return f"redirected to a login wall ({page.url})"
        body_text = (await page.locator("body").inner_text(timeout=5000)).lower()
    except Exception as e:
        return f"could not read page body to check for a challenge ({e}) -- treating as a possible challenge, not a clean success"
    for sig in CHALLENGE_SIGNALS:
        if sig in body_text:
            return f"page text matched challenge signal: {sig!r}"
    return None


def _extract_posts_from_dom_result(handle: str, raw_posts: list) -> list:
    """Normalize raw {text, href, iso_time} dicts (as returned by the
    real page.evaluate() JS below) into this module's real output shape:
    {handle, post_url, text, posted_at}. Absolute URL-ification and
    basic sanity filtering happen here, not in the JS, so this half is
    unit-testable without a browser."""
    out = []
    for p in raw_posts[:MAX_POSTS_PER_PROFILE]:
        text = str(p.get("text") or "").strip()
        href = str(p.get("href") or "").strip()
        if not text or not href:
            continue
        post_url = href if href.startswith("http") else f"https://x.com{href}"
        out.append({
            "handle": handle,
            "post_url": post_url,
            "text": text[:500],
            "posted_at": p.get("iso_time") or "",
        })
    return out


# Real, defensive DOM extraction -- reads data-testid attributes X's own
# timeline has used consistently for years (see module docstring's design
# rationale). Returns [] rather than throwing on ANY structural mismatch
# so a page-layout change degrades to "no posts found this cycle" instead
# of crashing the whole scrape.
_EXTRACT_JS = """
() => {
  try {
    const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
    return articles.map(a => {
      const textEl = a.querySelector('[data-testid="tweetText"]');
      const linkEl = a.querySelector('a[href*="/status/"]');
      const timeEl = a.querySelector('time');
      return {
        text: textEl ? textEl.innerText : '',
        href: linkEl ? linkEl.getAttribute('href') : '',
        iso_time: timeEl ? timeEl.getAttribute('datetime') : '',
      };
    });
  } catch (e) {
    return [];
  }
}
"""


async def scrape_profile(page, handle: str) -> list:
    """Visit one real profile, wait a real human-scale read pause, extract
    up to MAX_POSTS_PER_PROFILE posts defensively. Returns [] on any
    failure (timeout, no posts, structural mismatch) -- never raises;
    caller's cycle loop treats an empty result as "nothing new," not an
    error requiring a retry."""
    try:
        await page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.info("x_browser_scraper: could not load profile for @%s: %s", handle, e)
        return []

    await asyncio.sleep(random.uniform(READ_PAUSE_MIN_S, READ_PAUSE_MAX_S))

    challenge = await detect_challenge(page)
    if challenge:
        _trigger_backoff(challenge)
        return []

    try:
        raw = await page.evaluate(_EXTRACT_JS)
    except Exception as e:
        logger.info("x_browser_scraper: extraction failed for @%s: %s", handle, e)
        return []

    return _extract_posts_from_dom_result(handle, raw if isinstance(raw, list) else [])


async def run_cycle_async(handles: list) -> list:
    """One real scraping cycle: opens the persisted session ONCE (matching
    a real human's browsing session -- visiting several profiles in one
    sitting, not relaunching a browser per profile), shuffles handle
    order, respects the real rate ceiling, aborts the WHOLE cycle
    immediately on any challenge signal. Returns a flat list of real
    scraped-post dicts (handle/post_url/text/posted_at) -- ticker
    extraction happens downstream in social_tracker.py, not here."""
    if in_backoff():
        logger.info("x_browser_scraper: in backoff (see %s) -- skipping this cycle", STATE_FILE)
        _consume_backoff_tick()
        return []

    if not os.path.isdir(PROFILE_DIR):
        logger.warning("x_browser_scraper: no session profile at %s -- run login_once() first", PROFILE_DIR)
        return []

    shuffled = list(handles)
    random.shuffle(shuffled)

    results = []
    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(
        headless=True, persistent_context=True, user_data_dir=PROFILE_DIR, humanize=True,
    ) as context:
        page = await context.new_page()

        # Real session-health check before spending any real visit budget.
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        if any(sig in page.url.lower() for sig in LOGIN_WALL_SIGNALS):
            _trigger_backoff("session expired -- bounced to login wall on /home")
            return []

        for handle in shuffled:
            if checks_remaining_this_hour() <= 0:
                logger.info("x_browser_scraper: hourly check budget exhausted, stopping this cycle early")
                break
            _record_visit()
            posts = await scrape_profile(page, handle)
            results.extend(posts)
            if in_backoff():  # scrape_profile's own detect_challenge may have just triggered this
                break
            await asyncio.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))

    return results


def run_cycle_sync(handles: list) -> list:
    """Sync wrapper for social_tracker.py's existing synchronous
    scan_all()/one_cycle() loop -- same bridge pattern this codebase
    already uses elsewhere for async-from-sync (see homeassistant_tool.py
    in Hermes, or trade_outcome_learner.py's own asyncio.to_thread use for
    the inverse direction)."""
    return asyncio.run(run_cycle_async(handles))
