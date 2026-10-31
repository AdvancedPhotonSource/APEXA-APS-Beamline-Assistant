import asyncio
import json
import os

import pytest

from _idempotency import idempotent


def make_tool(tmp_path, *, engine_identity=None, gate_passed=True):
    source = tmp_path / "input.txt"
    source.write_text("first")
    output = tmp_path / "out"
    calls = []

    @idempotent("calibrate", anchor=lambda kw: kw["output_dir"],
                salient=lambda kw: {"image_file": kw["image_file"]}, engine_identity=engine_identity)
    async def run(image_file, output_dir):
        calls.append(1)
        await asyncio.sleep(0.02)
        nested = output / "nested"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "result.txt").write_text(source.read_text())
        return json.dumps({"status": "success", "gate_passed": gate_passed, "value": source.read_text()})
    return source, output, calls, run


def test_changed_bytes_at_same_path_do_not_replay(tmp_path):
    source, output, calls, run = make_tool(tmp_path)
    async def scenario():
        await run(str(source), str(output))
        assert json.loads(await run(str(source), str(output)))["cached"]
        stamp = source.stat()
        source.write_text("other")  # same byte length; restore mtime as well
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        result = json.loads(await run(str(source), str(output)))
        assert result["value"] == "other" and not result.get("cached")
    asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ["delete", "modify"])
def test_nested_output_damage_invalidates_cache(tmp_path, damage):
    source, output, calls, run = make_tool(tmp_path)
    async def scenario():
        await run(str(source), str(output))
        artifact = output / "nested/result.txt"
        if damage == "delete":
            artifact.unlink()
        else:
            artifact.write_text("wrong")
        await run(str(source), str(output))
    asyncio.run(scenario())
    assert len(calls) == 2


def test_concurrent_duplicate_requests_execute_once(tmp_path):
    source, output, calls, run = make_tool(tmp_path)
    async def scenario():
        return await asyncio.gather(*(run(str(source), str(output)) for _ in range(3)))
    results = [json.loads(r) for r in asyncio.run(scenario())]
    assert len(calls) == 1
    assert sum(bool(r.get("cached")) for r in results) == 2


def test_unqualified_engine_never_reuses_output(tmp_path):
    source, output, calls, run = make_tool(tmp_path, engine_identity=lambda kw: None)
    async def scenario():
        await run(str(source), str(output))
        await run(str(source), str(output))
    asyncio.run(scenario())
    assert len(calls) == 2


def test_engine_build_change_invalidates_reuse(tmp_path):
    build = {"version": "1"}
    source, output, calls, run = make_tool(tmp_path, engine_identity=lambda kw: dict(build))
    async def scenario():
        await run(str(source), str(output))
        build["version"] = "2"
        await run(str(source), str(output))
    asyncio.run(scenario())
    assert len(calls) == 2


def test_failed_scientific_gate_is_not_cached(tmp_path):
    source, output, calls, run = make_tool(tmp_path, gate_passed=False)
    async def scenario():
        await run(str(source), str(output))
        await run(str(source), str(output))
    asyncio.run(scenario())
    assert len(calls) == 2


def test_tool_exception_is_not_retried_by_cache_wrapper(tmp_path):
    calls = []
    @idempotent("fail", anchor=lambda kw: str(tmp_path / "out"), salient=lambda kw: {})
    async def fail():
        calls.append(1)
        raise ValueError("actual engine failure")
    with pytest.raises(ValueError, match="actual engine failure"):
        asyncio.run(fail())
    assert len(calls) == 1


def test_unknown_engine_still_serializes_shared_output(tmp_path):
    active = 0
    @idempotent("unsafe-output", anchor=lambda kw: str(tmp_path / "out"),
                salient=lambda kw: {}, engine_identity=lambda kw: None)
    async def run():
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0.02)
        active -= 1
        return '{"status":"success"}'
    async def scenario():
        await asyncio.gather(run(), run())
    asyncio.run(scenario())


def test_forced_calls_still_serialize_shared_output(tmp_path):
    active = 0
    @idempotent("forced", anchor=lambda kw: str(tmp_path / "out"), salient=lambda kw: {})
    async def run():
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0.02)
        active -= 1
        return '{"status":"success"}'
    async def scenario():
        await asyncio.gather(run(force=True), run(force=True))
    asyncio.run(scenario())


def test_missing_anchor_does_not_retry_tool_exception():
    calls = []
    @idempotent("fail", anchor=lambda kw: "", salient=lambda kw: {})
    async def run():
        calls.append(1)
        raise ValueError("engine error")
    with pytest.raises(ValueError, match="engine error"):
        asyncio.run(run())
    assert len(calls) == 1


def test_registry_routed_remote_output_never_creates_local_lock(tmp_path, monkeypatch):
    import apexa_remote_exec
    monkeypatch.setattr(apexa_remote_exec, "resolve_host_for_path", lambda path: "remote-analysis")
    monkeypatch.setattr(apexa_remote_exec, "is_local_host", lambda host: False)
    output = tmp_path / "remote-only"
    @idempotent("remote", anchor=lambda kw: str(output), salient=lambda kw: {}, engine_identity=lambda kw: None)
    async def run():
        return '{"status":"success"}'
    assert asyncio.run(run()) == '{"status":"success"}'
    assert not output.exists()
