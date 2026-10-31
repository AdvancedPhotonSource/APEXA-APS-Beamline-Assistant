import asyncio

import pytest

from apexa_ffhedm_graph import FFHEDMWorkflow


def test_requested_durable_workflow_does_not_silently_fall_back(tmp_path, monkeypatch):
    monkeypatch.delenv("APEXA_ALLOW_VOLATILE_WORKFLOWS", raising=False)
    invalid = tmp_path / "not_a_directory"
    invalid.write_text("file")
    called = []
    async def execute(*args):
        called.append(args)
    workflow = FFHEDMWorkflow(execute, db_path=str(invalid / "checkpoints.sqlite"))
    with pytest.raises(RuntimeError, match="Durable FF-HEDM checkpoints"):
        asyncio.run(workflow._ensure_app())
    assert workflow.durability == "uninitialized"
    assert workflow._checkpoint_error
    assert not called


def test_explicit_volatile_development_mode_is_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("APEXA_ALLOW_VOLATILE_WORKFLOWS", "1")
    invalid = tmp_path / "not_a_directory"
    invalid.write_text("file")
    async def execute(*args):
        raise AssertionError("no tools should run")
    workflow = FFHEDMWorkflow(execute, db_path=str(invalid / "checkpoints.sqlite"))
    asyncio.run(workflow._ensure_app())
    assert workflow.durability == "memory"
    assert workflow._checkpoint_error
