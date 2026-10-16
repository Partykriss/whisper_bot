"""Shared lease ownership contract and asynchronous heartbeat lifecycle."""
import asyncio
from app.events.config import SETTINGS


class LeaseLost(Exception):
    """A worker no longer owns its database record."""


async def with_lease(operation, renew, settings=SETTINGS):
    async def heartbeat():
        while True:
            await asyncio.sleep(settings.heartbeat_seconds)
            renew()
    work = asyncio.create_task(operation)
    beat = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait((work, beat), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return await work
        await beat  # Raise lease loss and cancel stale work in finally.
    finally:
        for task in (work, beat):
            task.cancel()
        await asyncio.gather(work, beat, return_exceptions=True)

