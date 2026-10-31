"""Telegram-cache-only service; no polling, OpenAI client or workspace write access."""
import asyncio
import signal
from app.cleanup.worker import CleanupWorker


async def main():
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    async with CleanupWorker('telegram'):
        await stopped.wait()


if __name__ == '__main__':
    asyncio.run(main())
