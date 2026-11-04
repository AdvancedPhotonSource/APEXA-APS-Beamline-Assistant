"""Learned streaming in OpenAICompatProvider.

Argo's Claude path refuses a NON-streaming call whose max_tokens implies more
than 10 minutes -- the Anthropic SDK's own guard, cutoff 21,333 tokens, measured
to the token against the live native endpoint on 26 Oct 2026. The provider
switches a session to streaming on the first such refusal and folds the stream
back into a normal completion. These pin that without the network.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apexa_provider_openai as M

P = M.OpenAICompatProvider


def _stream(*chunks):
    async def gen():
        for c in chunks:
            yield c
    return gen()


def _chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def _frag(index=None, id=None, name=None, arguments=None):
    return NS(index=index, id=id,
              function=NS(name=name, arguments=arguments) if (name or arguments) else None)


def _run(coro):
    return asyncio.run(coro)


# ── detection ────────────────────────────────────────────────────────────────
def test_the_argo_refusal_is_recognised():
    e = RuntimeError("Error code: 500 - {'error': {'message': 'Streaming is required for "
                     "operations that may take longer than 10 minutes.'}}")
    assert P._needs_streaming(e)


@pytest.mark.parametrize("msg", ["Connection reset", "rate limited", "Error code: 500 - overloaded"])
def test_other_failures_do_not_trigger_streaming(msg):
    assert not P._needs_streaming(RuntimeError(msg))


# ── reassembly ───────────────────────────────────────────────────────────────
def test_text_only_stream_is_joined():
    c = _run(P._reassemble(_stream(_chunk("The radius "), _chunk("is 612.4"), _chunk())))
    assert c.choices[0].message.content == "The radius is 612.4"
    assert c.choices[0].message.tool_calls is None


def test_a_tool_call_split_across_fragments_is_rebuilt():
    c = _run(P._reassemble(_stream(
        _chunk(tool_calls=[_frag(0, "call_a", "ring_radius_px", '{"ri')]),
        _chunk(tool_calls=[_frag(0, None, None, 'ng": 3}')]),
    )))
    tc = c.choices[0].message.tool_calls
    assert len(tc) == 1 and tc[0].id == "call_a"
    assert tc[0].function.name == "ring_radius_px"
    assert tc[0].function.arguments == '{"ring": 3}'


def test_parallel_tool_calls_keep_their_own_arguments():
    """The live check returned two parallel calls; fragments interleave by index."""
    c = _run(P._reassemble(_stream(
        _chunk(tool_calls=[_frag(0, "a", "ring_radius_px", '{"ring"')]),
        _chunk(tool_calls=[_frag(1, "b", "ring_radius_px", '{"ring"')]),
        _chunk(tool_calls=[_frag(0, None, None, ': 2}')]),
        _chunk(tool_calls=[_frag(1, None, None, ': 3}')]),
    )))
    tc = c.choices[0].message.tool_calls
    assert [(t.id, t.function.arguments) for t in tc] == [("a", '{"ring": 2}'), ("b", '{"ring": 3}')]


def test_fragments_without_an_index_attach_to_the_call_they_continue():
    """Some gateways translating another vendor's stream omit `index`."""
    c = _run(P._reassemble(_stream(
        _chunk(tool_calls=[_frag(None, "a", "f", '{"x"')]),
        _chunk(tool_calls=[_frag(None, None, None, ': 1}')]),
        _chunk(tool_calls=[_frag(None, "b", "g", '{}')]),
    )))
    tc = c.choices[0].message.tool_calls
    assert [(t.id, t.function.name, t.function.arguments) for t in tc] == \
        [("a", "f", '{"x": 1}'), ("b", "g", "{}")]


@pytest.mark.parametrize("pieces", [["ring_radius_px"] * 3, ["ring_", "radius_", "px"]])
def test_a_name_resent_or_split_is_never_doubled(pieces):
    frags = [_chunk(tool_calls=[_frag(0, "a" if i == 0 else None, p)]) for i, p in enumerate(pieces)]
    c = _run(P._reassemble(_stream(*frags)))
    assert c.choices[0].message.tool_calls[0].function.name == "ring_radius_px"


def test_empty_arguments_become_an_empty_object():
    c = _run(P._reassemble(_stream(_chunk(tool_calls=[_frag(0, "a", "list_motors")]))))
    assert c.choices[0].message.tool_calls[0].function.arguments == "{}"


def test_a_reassembled_completion_parses_like_a_normal_one():
    """_to_agent_response must stay the single parser for both paths."""
    c = _run(P._reassemble(_stream(
        _chunk("ok "), _chunk(tool_calls=[_frag(0, "a", "ring_radius_px", '{"ring": 3}')]))))
    r = P._to_agent_response(c)
    assert r.content == "ok " and r.tool_calls[0].arguments == {"ring": 3}
    assert r.stop_reason == "tool_use"


# ── learn once, then stream for the rest of the session ──────────────────────
def test_the_first_refusal_switches_the_session_to_streaming(monkeypatch):
    calls = []

    class Completions:
        async def create(self, **kw):
            calls.append(bool(kw.get("stream")))
            if not kw.get("stream"):
                raise RuntimeError("Error code: 500 - Streaming is required for operations "
                                   "that may take longer than 10 minutes")
            return _stream(_chunk("612.4"))

    client = NS(chat=NS(completions=Completions()))
    p = P.__new__(P)
    p.username, p.model, p.url = "u", "claudeopus55", "https://x/v1"
    p._dropped, p._resolved_model, p._stream = set(), "Claude Opus 5.5", False
    p._client_for_request = lambda: client
    p._endpoint_name = "argo"
    monkeypatch.setattr(M, "log_llm_call", lambda *a, **k: None)

    r1 = _run(p.chat([{"role": "user", "content": "hi"}]))
    r2 = _run(p.chat([{"role": "user", "content": "again"}]))
    assert r1.content == r2.content == "612.4"
    # one refused attempt, then streamed -- and the second turn never retries non-streaming
    assert calls == [False, True, True]
    assert p._stream is True


# Native Anthropic Messages: errors and real tool-result history, not just text.
def _sse(*events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          text="".join("data: " + json.dumps(event) + "\n\n" for event in events))


@asynccontextmanager
async def _native_provider(monkeypatch, handler, model="claude-opus-5-5"):
    import apexa_agents as A
    from apexa_llm_endpoints import AnthropicEndpoint
    monkeypatch.setattr(A, "log_llm_call", lambda row: None)
    monkeypatch.setattr(A, "record_llm_call", lambda **kwargs: None)
    monkeypatch.setenv("APEXA_LLM_API_KEY", "fixture-private-key")
    provider = A.StreamingAnthropicProvider("", model, endpoint=AnthropicEndpoint())
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        yield provider
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_native_anthropic_billing_error_reaches_chat_without_echoing_secrets(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message":
            "Your credit balance is too low to access the Anthropic API. fixture-private-key private-prompt"}})
    async with _native_provider(monkeypatch, handler) as provider:
        with pytest.raises(httpx.HTTPStatusError, match="API account has insufficient credits") as error:
            await provider.chat([{"role": "user", "content": "synthetic"}])
    assert "fixture-private-key" not in str(error.value)
    assert "private-prompt" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
async def test_native_tool_round_trip_keeps_signed_blocks_and_parallel_results(monkeypatch, model):
    from apexa_agents import AgentRunner
    calls = []
    thinking = {"type": "thinking", "thinking": "fixture reasoning", "signature": "opaque-signature"}
    opaque = {"type": "redacted_thinking", "data": "opaque-data"}
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert "temperature" not in body and "tool_choice" not in body
        assert all(m["role"] in {"user", "assistant"} for m in body["messages"])
        assert body["tools"][0]["input_schema"]["type"] == "object"
        if len(calls) == 1:
            return _sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "fixture reasoning"}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque-signature"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "content_block_start", "index": 1, "content_block": opaque},
                {"type": "content_block_stop", "index": 1},
                {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "a", "name": "probe", "input": {}}},
                {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"x":'}},
                {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '1}'}},
                {"type": "content_block_stop", "index": 2},
                {"type": "content_block_start", "index": 3, "content_block": {"type": "tool_use", "id": "b", "name": "probe", "input": {}}},
                {"type": "content_block_stop", "index": 3},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}},
                {"type": "message_stop"},
            )
        assert body["messages"][-2]["content"][:2] == [thinking, opaque]
        results = body["messages"][-1]["content"]
        assert [(c["type"], c["tool_use_id"], c["content"]) for c in results] == [
            ("tool_result", "a", "receipt-a"), ("tool_result", "b", "receipt-b")]
        return _sse(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "receipt-a receipt-b"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        )
    tools = [{"type": "function", "function": {"name": "probe", "parameters": {"type": "object"}}}]
    messages = [{"role": "system", "content": "Synthetic test"}, {"role": "user", "content": "Call the probes"}]
    runner = AgentRunner(None)
    async with _native_provider(monkeypatch, handler, model) as provider:
        first = await provider.chat(messages, tools=tools)
        assert first.content == "" and first.stop_reason == "tool_use"
        assert [t.arguments for t in first.tool_calls] == [{"x": 1}, {}]
        messages.append(runner._assistant_message(first, model, structured=True))
        messages.extend(runner._tool_result_message(t, "receipt-" + t.id, model, structured=True)
                        for t in first.tool_calls)
        second = await provider.chat(messages, tools=tools)
    assert second.content == "receipt-a receipt-b" and len(calls) == 2


def test_native_adapter_translates_shared_openai_tool_history_without_mutating_it():
    from apexa_agents import StreamingAnthropicProvider
    history = [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": "Probe"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "function": {"name": "probe", "arguments": '{"x":1}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "receipt"},
    ]
    before = json.dumps(history)
    system, messages = StreamingAnthropicProvider._split_system(history)
    assert system == "Instructions" and json.dumps(history) == before
    assert messages[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "a", "name": "probe", "input": {"x": 1}}]}
    assert messages[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "receipt"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,match", [
    ("invalid-json", "incomplete tool arguments"),
    ("disconnect", "ended before completion"),
    ("output-limit", "output limit"),
    ("error-event", "temporarily unavailable"),
    ("unfinished-block", "incomplete content blocks"),
    ("invalid-event", "invalid stream event"),
])
async def test_native_stream_failures_never_return_partial_tool_calls(monkeypatch, failure, match):
    def handler(request):
        events = [{"type": "content_block_start", "index": 0,
                   "content_block": {"type": "tool_use", "id": "a", "name": "probe", "input": {}}}]
        if failure == "invalid-json":
            events += [{"type": "content_block_delta", "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": '{"x":'}},
                       {"type": "content_block_stop", "index": 0}]
        elif failure == "invalid-event":
            return httpx.Response(200, text='data: {"type":\n\ndata: {"type":"message_stop"}\n\n')
        elif failure == "unfinished-block":
            events += [{"type": "message_stop"}]
        elif failure == "error-event":
            events += [{"type": "error", "error": {"type": "overloaded_error", "message": "private"}}]
        elif failure == "output-limit":
            events += [{"type": "content_block_stop", "index": 0},
                       {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
                       {"type": "message_stop"}]
        return _sse(*events)
    async with _native_provider(monkeypatch, handler) as provider:
        with pytest.raises((ValueError, RuntimeError), match=match):
            await provider.chat([{"role": "user", "content": "Probe"}])


@pytest.mark.parametrize("body", [{"error": "private-secret"}, {"error": {"message": "private-secret"}}, "<html>private-secret</html>"])
def test_unknown_provider_errors_do_not_echo_body(body):
    from apexa_llm_endpoints import provider_error_message
    assert "private-secret" not in provider_error_message(400, body)
