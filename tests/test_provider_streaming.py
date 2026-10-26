"""Learned streaming in OpenAICompatProvider.

Argo's Claude path refuses a NON-streaming call whose max_tokens implies more
than 10 minutes -- the Anthropic SDK's own guard, cutoff 21,333 tokens, measured
to the token against the live native endpoint on 26 Oct 2026. The provider
switches a session to streaming on the first such refusal and folds the stream
back into a normal completion. These pin that without the network.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

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
