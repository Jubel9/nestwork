"""Background analyses (appearance, clothes) that keep running when you close Settings or the page.

Their state goes into every payload ("analyzing", "analysis_error"), so the UI can show "Analysing…"
again when you come back and pick up the result when it's done.
"""

import asyncio

_tasks: dict[str, asyncio.Task] = {}
errors: dict[str, str] = {}      # the last failure per job, until it's run again


def running(key: str) -> bool:
    task = _tasks.get(key)
    return task is not None and not task.done()


def start(key: str, coro) -> bool:
    """Run `coro` in the background unless the same job is already running."""
    if running(key):
        coro.close()
        return False
    errors.pop(key, None)

    async def wrapper():
        from . import agent
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            errors[key] = "The analysis took too long. Try again."
        except Exception as e:   # shown to the user next time they look
            errors[key] = str(e) or e.__class__.__name__
        finally:
            _tasks.pop(key, None)
            agent.after_change()   # open pages notice and pick up the result

    _tasks[key] = asyncio.create_task(wrapper())
    return True


def cancel(key: str) -> None:
    task = _tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

