"""POSIX process-group probe races; all signal calls here are injected fakes."""
import errno
import os
import signal

import pytest

import apexa_subprocess as child_runtime


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX group semantics")


class ExitedChild:
    pid = 123456789
    waited = False

    async def wait(self):
        self.waited = True
        return -signal.SIGTERM


@pytest.mark.asyncio
async def test_transient_permission_probe_then_absence_does_not_send_kill(monkeypatch):
    calls = []
    probes = 0
    def killpg(pid, sig):
        nonlocal probes
        assert pid == ExitedChild.pid
        calls.append(sig)
        if sig == 0:
            probes += 1
            if probes == 1:
                raise PermissionError(errno.EPERM, "transient group teardown")
            raise ProcessLookupError(errno.ESRCH, "group disappeared")
        assert sig == signal.SIGTERM, "a disappeared group must never receive a later KILL"
    monkeypatch.setattr(child_runtime.os, "killpg", killpg)
    child = ExitedChild()
    await child_runtime._stop(child, grace=0.1)
    assert child.waited
    assert calls == [signal.SIGTERM, 0, 0]


@pytest.mark.asyncio
async def test_persistent_permission_probe_remains_visible(monkeypatch):
    calls = []
    def killpg(pid, sig):
        assert pid == ExitedChild.pid
        calls.append(sig)
        if sig == 0:
            raise PermissionError(errno.EPERM, "cannot inspect owned group")
    monkeypatch.setattr(child_runtime.os, "killpg", killpg)
    child = ExitedChild()
    with pytest.raises(PermissionError, match="cannot inspect"):
        await child_runtime._stop(child, grace=0.03)
    assert calls.count(0) >= 2
    assert signal.SIGKILL not in calls
    assert not child.waited


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
async def test_real_signal_permission_denial_is_not_suppressed(monkeypatch, denied_signal):
    def killpg(pid, sig):
        assert pid == ExitedChild.pid
        if sig == denied_signal:
            raise PermissionError(errno.EPERM, "termination denied")
    monkeypatch.setattr(child_runtime.os, "killpg", killpg)
    with pytest.raises(PermissionError, match="termination denied"):
        await child_runtime._stop(ExitedChild(), grace=0)


@pytest.mark.asyncio
async def test_already_absent_group_is_not_signalled_again(monkeypatch):
    calls = []
    def killpg(pid, sig):
        assert pid == ExitedChild.pid
        calls.append(sig)
        raise ProcessLookupError(errno.ESRCH, "already gone")
    monkeypatch.setattr(child_runtime.os, "killpg", killpg)
    child = ExitedChild()
    await child_runtime._stop(child)
    assert child.waited
    assert calls == [signal.SIGTERM]
