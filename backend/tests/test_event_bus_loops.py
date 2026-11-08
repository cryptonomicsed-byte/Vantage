"""The event bus must survive the app running on more than one event loop.

Regression: a module-level asyncio.Queue bound to the first loop made every
get() on a second loop raise, and the dispatch loop retried without yielding --
a hot spin that starved the loop, so a second TestClient(app) never finished
startup and CI was cancelled at 12 minutes.
"""
import asyncio

from backend import event_bus as eb


def _roundtrip_on_a_fresh_loop() -> bool:
    async def main():
        q = eb._queue()
        await q.put("ping")
        return await asyncio.wait_for(q.get(), timeout=2) == "ping"

    return asyncio.run(main())


def test_queue_works_on_a_second_loop():
    assert _roundtrip_on_a_fresh_loop()
    assert _roundtrip_on_a_fresh_loop()  # a new loop, the same module


def test_dispatch_errors_back_off_instead_of_spinning(monkeypatch):
    calls = {"n": 0}

    class Broken:
        async def get(self):
            calls["n"] += 1
            raise RuntimeError("boom")

    async def main():
        async def _no_table():
            return None

        monkeypatch.setattr(eb, "_ensure_event_log_table", _no_table)
        monkeypatch.setattr(eb, "_queue", lambda: Broken())
        task = asyncio.create_task(eb._dispatch_loop())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    # Without backoff this is in the hundreds of thousands within 0.3s.
    assert 1 <= calls["n"] <= 10, calls
