#!/usr/bin/env python3
"""One-time real login bootstrap for daemons/x_browser_scraper.py.

Run this ONCE, manually, with real credentials for the dedicated X
account, headless=False so a human can personally clear any 2FA/CAPTCHA/
checkpoint X shows live (headless=True cannot show you a challenge to
solve). On this VPS, headless=False needs a real display -- either an
X11/VNC forward, or run this from a machine that DOES have a display and
copy the resulting profile directory (X_SCRAPER_PROFILE_DIR, default
/opt/ares/x_scraper_profile) to this VPS afterward. Either way, this is a
one-time step: once login_once() reports "logged_in", the persisted
profile is reused by every future social_tracker.py cycle with no further
login needed, until the session naturally expires.

Usage:
  X_LOGIN_USERNAME=... X_LOGIN_PASSWORD=... python3 x_scraper_login.py

Credentials are read from env only -- never hardcoded, never logged.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x_browser_scraper  # noqa: E402


def main():
    username = os.environ.get("X_LOGIN_USERNAME", "").strip()
    password = os.environ.get("X_LOGIN_PASSWORD", "").strip()
    if not username or not password:
        print("Set X_LOGIN_USERNAME and X_LOGIN_PASSWORD in the environment before running this.")
        sys.exit(1)

    print(f"Logging in as @{username} (headless=False -- needs a real display)...")
    result = asyncio.run(x_browser_scraper.login_once(username, password, headless=False))
    print(f"\nstatus: {result['status']}")
    print(f"detail: {result['detail']}")
    if result.get("screenshot"):
        print(f"screenshot saved: {result['screenshot']}")

    if result["status"] == "logged_in":
        print(f"\nSession saved to {x_browser_scraper.PROFILE_DIR}. social_tracker.py will use it automatically from the next cycle.")
    elif result["status"] == "challenge":
        print("\nX showed a checkpoint this script does not attempt to solve. Re-run this with a real display attached, "
              "complete the checkpoint by hand in the visible browser window, then check the screenshot above to confirm.")
    else:
        print("\nLogin did not complete. Check the screenshot and X_LOGIN_USERNAME/X_LOGIN_PASSWORD, then retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
