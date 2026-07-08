#!/usr/bin/env python3
"""Crawl4AI — Convert Vantage UI pages to clean markdown for Strix analysis."""
import asyncio, sys
from crawl4ai import AsyncWebCrawler

PAGES = [
    ("home", "http://localhost:8001/"),
    ("trading", "http://localhost:8001/trading"),
    ("code", "http://localhost:8001/code"),
]

async def crawl(url, name):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url)
        if result.success:
            path = f"/tmp/vantage_{name}.md"
            with open(path, "w") as f:
                f.write(f"# Vantage {name.title()} Page\n\n{result.markdown[:10000]}")
            print(f"✅ {name}: {len(result.markdown)} chars → {path}")
            return len(result.markdown)
        else:
            print(f"❌ {name}: crawl failed")
            return 0

async def main():
    total = 0
    for name, url in PAGES:
        total += await crawl(url, name)
    print(f"\nTotal: {total} chars of clean markdown saved")

asyncio.run(main())
