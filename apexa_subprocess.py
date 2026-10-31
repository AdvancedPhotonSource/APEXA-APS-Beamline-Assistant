"""Bounded asynchronous child processes with owned cancellation.

The caller owns the command and its resource environment. Cancelling an await
does not return until the owned process group has been terminated and reaped.
This provides local process lifetime management, not durable job scheduling or
proof that cancelling an SSH client stopped a remote job.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import weakref
from typing import Mapping, Sequence
from apexa_observability import traced_process, emit


_slots: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class OutputLimitExceeded(RuntimeError):
    def __init__(self, limit, stdout, stderr):
        super().__init__(f"Child output exceeded {limit} bytes; its process group was stopped. Write large results/logs to artifact files.")
        self.stdout, self.stderr = stdout, stderr


async def _communicate_bounded(proc, payload, limit):
    total = 0
    exceeded = False
    stop_task = None

    async def read(stream):
        nonlocal total, exceeded, stop_task
        if stream is None:
            return None
        chunks = []
        while data := await stream.read(65536):
            keep = data[:max(0, limit - total)]
            if keep:
                chunks.append(keep)
                total += len(keep)
            if len(keep) < len(data) and not exceeded:
                exceeded = True
                # Keep draining while termination/reaping proceeds. Awaiting
                # proc.wait() from inside a paused pipe reader can deadlock.
                stop_task = asyncio.create_task(_stop(proc))
        return b''.join(chunks)

    async def write():
        if proc.stdin is not None:
            try:
                if payload is not None:
                    proc.stdin.write(payload)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

    stdout, stderr, _, _ = await asyncio.gather(read(proc.stdout), read(proc.stderr), write(), proc.wait())
    if stop_task is not None:
        await stop_task
    return stdout, stderr, exceeded


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _slots:
        try:
            limit = max(1, int(os.environ.get("APEXA_MAX_CHILDREN", "32")))
        except ValueError:
            limit = 32
        _slots[loop] = asyncio.Semaphore(limit)
    return _slots[loop]


async def _stop(proc: asyncio.subprocess.Process, grace: float = 1.0) -> None:
    """Terminate the entire group, including descendants holding output pipes."""
    if os.name == "posix":
        group_present = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            group_present = False
        # Even if the direct child exits promptly, descendants may ignore TERM.
        # Wait for the group to disappear rather than only checking returncode.
        # macOS can transiently return EPERM for signal-zero while a just-killed
        # group disappears. It is not proof of either liveness or successful
        # cleanup: retry within the grace period, then propagate persistent
        # permission errors. TERM/KILL permission errors are never swallowed.
        deadline = asyncio.get_running_loop().time() + grace
        while group_present and asyncio.get_running_loop().time() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                group_present = False
                break
            except PermissionError:
                pass  # bounded retry; the final probe below must establish state
            await asyncio.sleep(0.025)
        if group_present:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                group_present = False
            # In particular, do not suppress PermissionError here and report
            # that a group we cannot inspect was stopped.
        if group_present:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Once ESRCH established disappearance, never signal the PGID again:
        # it could be recycled by the OS before a redundant SIGKILL arrives.
    else:
        # psutil is an APEXA dependency; snapshot descendants before terminating
        # the parent, since they may be reparented afterwards.
        import psutil
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
            parent = None
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        if parent is not None:
            try:
                parent.terminate()
            except psutil.NoSuchProcess:
                pass
        await asyncio.sleep(grace)
        for child in children + ([parent] if parent is not None else []):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    await proc.wait()


async def _drain_cleanup(task: asyncio.Task) -> None:
    """Repeated caller cancellation must not abandon process cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


async def _stop_and_drain(proc, communication):
    try:
        await _stop(proc)
    except BaseException:
        # An explicit stop failure must not turn into an unbounded pipe wait.
        # The caller receives that failure; resource ownership remains uncertain.
        communication.cancel()
        await asyncio.gather(communication, return_exceptions=True)
        raise
    return await communication


@traced_process
async def run(
    args: str | Sequence[str], *, shell: bool = False,
    cwd: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None, timeout: float | None = None,
    input: str | bytes | None = None, text: bool = True,
    capture_output: bool = True, check: bool = False, max_output_bytes: int | None = None,
) -> subprocess.CompletedProcess:
    """Run a child without blocking the event loop; timeout/cancel stops its group.

    This intentionally supports the small subprocess.run subset used by APEXA.
    Output is drained concurrently to avoid full-pipe deadlocks. Callers should
    send large scientific outputs to artifacts, keeping stdout to a small result
    manifest. Thread/device settings must be passed in ``env`` per invocation.
    """
    if shell and not isinstance(args, str):
        raise TypeError("shell=True requires a command string")
    if not shell and isinstance(args, str):
        raise TypeError("shell=False requires an argument sequence")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    output_limit = max_output_bytes if max_output_bytes is not None else int(
        os.environ.get("APEXA_MAX_CHILD_OUTPUT_BYTES", str(32 * 1024 * 1024)))
    if type(output_limit) is not int or output_limit <= 0:
        raise ValueError("max_output_bytes must be a positive integer")
    payload = input.encode() if isinstance(input, str) else input
    options = {
        "cwd": cwd, "env": dict(env) if env is not None else None,
        "stdin": asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE if capture_output else None,
        "stderr": asyncio.subprocess.PIPE if capture_output else None,
    }
    if os.name == "posix":
        options["start_new_session"] = True
    else:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    async with _semaphore():
        spawn = asyncio.create_task(
            asyncio.create_subprocess_shell(args, **options) if shell else
            asyncio.create_subprocess_exec(*args, **options))
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Spawn may already have crossed the OS boundary. Obtain its handle
            # and stop it before propagating cancellation.
            async def stop_after_spawn():
                spawned = await spawn
                draining = asyncio.create_task(_communicate_bounded(spawned, None, output_limit))
                from apexa_compute import attach_current_child
                try:
                    await asyncio.to_thread(attach_current_child, spawned.pid,
                        process_group=spawned.pid if os.name == "posix" else None)
                finally:
                    await _stop_and_drain(spawned, draining)
            await _drain_cleanup(asyncio.create_task(stop_after_spawn()))
            raise
        communicate = asyncio.create_task(_communicate_bounded(proc, payload, output_limit))
        emit("subprocess.spawned", pid=proc.pid)
        from apexa_compute import attach_current_child
        attachment = asyncio.create_task(asyncio.to_thread(attach_current_child,
            proc.pid, process_group=proc.pid if os.name == "posix" else None))
        try:
            await asyncio.shield(attachment)
        except BaseException:
            async def cleanup_attachment():
                await asyncio.gather(attachment, return_exceptions=True)
                await _stop_and_drain(proc, communicate)
            await _drain_cleanup(asyncio.create_task(cleanup_attachment()))
            raise
        try:
            stdout, stderr, exceeded = await asyncio.wait_for(asyncio.shield(communicate), timeout)
        except (asyncio.CancelledError, TimeoutError) as error:
            async def cleanup():
                return await _stop_and_drain(proc, communicate)
            cleanup_task = asyncio.create_task(cleanup())
            await _drain_cleanup(cleanup_task)
            stdout, stderr, _ = cleanup_task.result()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr) from error
        if exceeded:
            raise OutputLimitExceeded(output_limit, stdout, stderr)
        if text:
            stdout = stdout.decode(errors="replace") if stdout is not None else None
            stderr = stderr.decode(errors="replace") if stderr is not None else None
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
