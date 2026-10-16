"""Worker lifecycle: heartbeat renewal and cancellation on lost ownership."""
import asyncio
import unittest
from unittest.mock import Mock
from app.events.leases import with_lease
from app.events.config import QueueSettings
from app.services.media_store import LeaseLost


class LeaseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_loss_cancels_work(self):
        cancelled = asyncio.Event()
        async def operation():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        renew = Mock(side_effect=LeaseLost())
        with self.assertRaises(LeaseLost):
            await with_lease(operation(), renew, QueueSettings(heartbeat_seconds=1, lease_seconds=2))
        self.assertTrue(cancelled.is_set())
        renew.assert_called_once()

    async def test_heartbeat_keeps_running_until_work_finishes(self):
        renewed = asyncio.Event()
        def renew():
            renewed.set()
        async def operation():
            await renewed.wait()
            return 'done'
        self.assertEqual(await with_lease(operation(), renew,
                                         QueueSettings(heartbeat_seconds=1, lease_seconds=2)), 'done')
