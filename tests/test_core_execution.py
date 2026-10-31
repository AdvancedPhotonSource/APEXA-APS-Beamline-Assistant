"""Core command policy and cooperative scheduling; no remote/hardware commands run."""
import asyncio
import json
import subprocess

import pytest

import beamline_core_server as core


@pytest.fixture(autouse=True)
def no_opt_in(monkeypatch):
    monkeypatch.delenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", raising=False)


@pytest.mark.parametrize("command", ["caput AUDIT:m1 2", "/usr/bin/caput AUDIT:m1 2", "pvput AUDIT:m1 2", "bash -c 'caput AUDIT:m1 2'"])
def test_hardware_write_cli_always_denied(monkeypatch, command):
    assert not core.is_command_allowed(command)
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    assert not core.is_command_allowed(command)


@pytest.mark.parametrize("command", ["python3 -c 'print(1)'", "bash -c 'ls'", "ssh elsewhere ls", "ls | wc -l", "ls > output", "env python3 -c 'print(1)'", "./ls", "ls $(pwd)"])
def test_default_inspection_policy_denies_general_execution(command):
    assert not core.is_command_allowed(command)


def test_default_inspection_uses_trusted_system_executable(monkeypatch):
    monkeypatch.setenv("PATH", "/tmp/untrusted")
    argv = core._read_only_argv("ls -l '/tmp/two words'")
    assert argv[0] in ("/bin/ls", "/usr/bin/ls")
    assert argv[1:] == ["-l", "/tmp/two words"]
    assert core.is_command_allowed("ls -l /tmp")


def test_operator_opt_in_allows_exploratory_analysis(monkeypatch):
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    assert core.is_command_allowed("python3 -c 'print(1)'")
    assert core.is_command_allowed("ls | wc -l")
    assert not core.is_command_allowed("not-an-allowed-command x")


def test_local_invocation_is_argv_only_and_yields_to_other_calls(monkeypatch):
    active = peak = 0
    calls = []
    async def runner(command, **kwargs):
        nonlocal active, peak
        calls.append((command, kwargs))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return subprocess.CompletedProcess(command, 0, "ok", "")
    monkeypatch.setattr(core, "run_subprocess", runner)
    async def run():
        return await asyncio.gather(*(core.run_command("pwd") for _ in range(4)))
    results = [json.loads(x) for x in asyncio.run(run())]
    assert peak == 4
    assert all(r["success"] for r in results)
    assert all(isinstance(args, list) and not kwargs["shell"] for args, kwargs in calls)


def test_command_policy_failure_does_not_invoke_executor(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("policy-denied command reached execution")
    monkeypatch.setattr(core, "run_subprocess", forbidden)
    result = json.loads(asyncio.run(core.run_command("python3 -c 'print(1)'")))
    assert result["error_code"] == "command_policy_denied"
    assert "APEXA_ALLOW_UNSANDBOXED_COMMANDS" in result["detail"]


def test_remote_execution_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(core, "is_local_host", lambda _: False)
    result = json.loads(asyncio.run(core.run_remote_command("ls", host="audit-host")))
    assert not result["success"] and result["error_code"] == "command_policy_denied"


def test_remote_timeout_does_not_claim_remote_process_stopped(monkeypatch):
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    monkeypatch.setattr(core, "is_local_host", lambda _: False)
    async def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(core, "run_subprocess", timeout)
    result = json.loads(asyncio.run(core.run_remote_command("ls", host="audit-host")))
    assert result["timed_out"]
    assert result["remote_state"] == "unknown"
    assert result["requires_reconciliation"]


def test_invalid_working_directory_is_not_silently_replaced(tmp_path):
    result = json.loads(asyncio.run(core.run_command("pwd", working_dir=str(tmp_path / "absent"))))
    assert not result["success"]
    assert "Working directory" in result["error"]


@pytest.mark.parametrize("host", ["-oProxyCommand=anything", "host;command", "host with space"])
def test_ssh_option_or_shell_host_injection_is_refused(monkeypatch, host):
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    result = json.loads(asyncio.run(core.run_remote_command("ls", host=host)))
    assert not result["success"] and "Invalid SSH host" in result["error"]
