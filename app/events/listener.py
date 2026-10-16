"""Nonblocking dedicated PostgreSQL LISTEN connection; signals are only hints."""
import asyncio
import logging
import psycopg2
from psycopg2 import extensions
from app.database import engine
from app.events.config import CHANNEL, SETTINGS


async def ready(connection):
    loop = asyncio.get_running_loop()
    while True:
        state = connection.poll()
        if state == extensions.POLL_OK:
            return
        future = loop.create_future()
        def resume():
            if not future.done():
                future.set_result(None)
        fd = connection.fileno()
        if state == extensions.POLL_READ:
            loop.add_reader(fd, resume)
        elif state == extensions.POLL_WRITE:
            loop.add_writer(fd, resume)
        else:
            raise psycopg2.OperationalError('Unexpected poll state')
        try:
            await future
        finally:
            loop.remove_reader(fd)
            loop.remove_writer(fd)


async def listen(wake, settings=SETTINGS):
    while True:
        connection = None
        try:
            args, kwargs = engine.dialect.create_connect_args(engine.url)
            connection = psycopg2.connect(*args, **kwargs, async_=True)
            await asyncio.wait_for(ready(connection), settings.lease_seconds)
            with connection.cursor() as cursor:
                cursor.execute('LISTEN ' + CHANNEL)
                await asyncio.wait_for(ready(connection), settings.lease_seconds)
            wake.set()
            loop = asyncio.get_running_loop()
            changed = asyncio.Event()
            fd = connection.fileno()
            loop.add_reader(fd, changed.set)
            try:
                while True:
                    try:
                        await asyncio.wait_for(changed.wait(), settings.scan_seconds)
                    except asyncio.TimeoutError:
                        # Probe the connection during idle periods, without a transaction.
                        loop.remove_reader(fd)
                        try:
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT 1')
                                await asyncio.wait_for(ready(connection), settings.lease_seconds)
                        finally:
                            loop.add_reader(fd, changed.set)
                    changed.clear()
                    connection.poll()
                    if connection.notifies:
                        connection.notifies.clear()
                        wake.set()
            finally:
                loop.remove_reader(fd)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.warning('Database listener reconnecting (%s)', type(error).__name__)
        finally:
            if connection is not None:
                connection.close()
        await asyncio.sleep(settings.reconnect_seconds)
