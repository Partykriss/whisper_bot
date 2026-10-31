"""Lifecycle of database listeners and independent queue workers (ADR-0003)."""
import asyncio
import logging
from contextlib import suppress
from app.events.config import SETTINGS
from app.events.listener import listen
from app.events.leases import with_lease
from app.events import store, delivery
from app.services import task_queue
from app.cleanup.worker import CleanupWorker


class Runtime:
    def __init__(self, bot, jobs, settings=SETTINGS):
        self.bot, self.jobs, self.settings = bot, jobs, settings
        self.wake = asyncio.Event()
        self.workers = []
        self.cleanup = CleanupWorker('workspace', jobs.settings)

    async def __aenter__(self):
        await self.cleanup.__aenter__()
        self.workers = [asyncio.create_task(listen(self.wake, self.settings)),
                        asyncio.create_task(self.loop(self.fanout))]
        self.workers += [asyncio.create_task(self.loop(self.media))
                         for _ in range(self.jobs.settings.concurrent_jobs)]
        self.workers += [asyncio.create_task(self.loop(self.notify))
                         for _ in range(self.settings.delivery_workers)]
        return self

    async def __aexit__(self, *args):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await self.cleanup.__aexit__(*args)

    async def loop(self, operation):
        while True:
            try:
                if await operation():
                    # Yield even when fanout has a backlog of synchronous DB work.
                    await asyncio.sleep(0)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logging.error('Queue worker %s failed (%s)', operation.__name__, type(error).__name__)
                await asyncio.sleep(self.settings.retry_seconds)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=self.settings.scan_seconds)
            self.wake.clear()

    async def fanout(self):
        processed = store.fanout_one()
        if processed:
            self.wake.set()
        return processed

    async def media(self):
        task_queue.recover(self.settings)
        item = task_queue.claim(self.settings)
        if item is None:
            return False
        user_id, chat_id, task_id, source, token = item
        await with_lease(self.jobs.process(user_id, chat_id, task_id, source, token),
                         lambda: task_queue.renew(task_id, token, self.settings), self.settings)
        return True

    async def notify(self):
        item = store.claim(self.settings)
        if item is None:
            return False
        await with_lease(delivery.send(self.bot, item, self.jobs.settings),
                         lambda: store.renew(item[0], item[1], self.settings), self.settings)
        return True
