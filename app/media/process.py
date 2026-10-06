"""Bounded external process execution with cancellation cleanup."""
import asyncio

class MediaError(Exception):
    """An actionable, safe-to-display media failure."""

async def run_process(*args, timeout):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        # Provider stderr can contain signed URLs or credentials; don't expose it.
        raise MediaError(f'{args[0]} could not process this media. Check source availability and format.')
    return out.decode('utf-8')
