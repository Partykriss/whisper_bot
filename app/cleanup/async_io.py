"""Keep file-owning threads alive through cancellation until they close files."""
import asyncio


async def file_thread(function, *args):
    work = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # Repeated cancellation must not release filesystem guards early either.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled():
            work.exception()  # Retrieve a possible thread failure without leaking it.
        raise
