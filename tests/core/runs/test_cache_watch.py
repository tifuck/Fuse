"""Cache-miss notices: a round whose prompt the provider should have served
from cache (an earlier round of the chat was) raises one cache event, a
cache_watch bug never fails the turn, and miss() names why the cache was lost."""
from unittest.mock import patch

from app.chat_generation import cache_watch
from app.chat_output import Output
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames

from ._support import fake_tools, tool_thread


def _usage(prompt, cached):
    return {"prompt_tokens": prompt, "completion_tokens": 5, "total_tokens": prompt + 5,
            "prompt_tokens_details": {"cached_tokens": cached}}


def _tool_round(usage):
    delta = {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "web_search", "arguments": '{"query": "x"}'}}
    return [
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [delta]}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        frame({"choices": [], "usage": usage}),
        b"data: [DONE]\n\n",
    ]


def _cache_events():
    seen = []
    real = Output.apply

    def apply(self, obj):
        fuse = obj.get("fuse") if isinstance(obj, dict) else None
        if isinstance(fuse, dict) and fuse.get("event") == "cache":
            seen.append(fuse)
        return real(self, obj)

    return seen, patch.object(Output, "apply", apply)


def test_a_cold_round_after_a_cached_one_publishes_a_cache_miss(fresh_db):
    uid = quick_user()
    seen, watching = _cache_events()
    with watching, fake_tools(), fake_provider(
        _tool_round(_usage(100_000, 90_000)), text_frames("done", usage=_usage(120_000, 0)),
    ):
        _, run = run_async(run_turn(uid, "go", thread=tool_thread(uid)))

    assert run["status"] == "completed"
    assert [(e["state"], e["reason"], e["prompt"], e["cached"]) for e in seen] == [("miss", "provider", 120_000, 0)]
    assert seen[0]["run_id"] == run["id"]


def test_a_cache_watch_bug_never_fails_the_turn(fresh_db):
    uid = quick_user()
    with patch.object(cache_watch, "miss", side_effect=RuntimeError("bug")), fake_tools(), fake_provider(
        _tool_round(_usage(100_000, 90_000)), text_frames("done", usage=_usage(120_000, 0)),
    ):
        _, run = run_async(run_turn(uid, "go", thread=tool_thread(uid)))

    assert run["status"] == "completed"


def _probe(prompt, cached, *, at, prior=None, source="openai", cred=None, messages=("sys", "q"), effort=None):
    request = cache_watch.fingerprint([{"role": "user", "content": m} for m in messages], effort=effort)
    return cache_watch.probe({"cache_usage_status": "known", "prompt_tokens": prompt, "cached_tokens": cached},
                             model="m", source=source, cred=cred, prior=prior, now=at, run="r", request=request)


def _reason(prior, current, **kwargs):
    event = cache_watch.miss(prior, current, **kwargs)
    return event and event["reason"]


def test_miss_names_why_the_cache_was_lost():
    t = cache_watch.BOOT + 10
    hot = _probe(100_000, 90_000, at=t, messages=("sys", "a", "b"))
    assert _reason(hot, _probe(120_000, 0, at=t + 5, prior=hot, messages=("sys", "a", "b", "c"))) == "provider"
    assert _reason(hot, _probe(120_000, 0, at=t + 5, prior=hot, messages=("other sys", "a", "b"))) == "settings"
    assert _reason(hot, _probe(120_000, 0, at=t + 5, prior=hot), settings_changed=True) == "settings"
    long = _probe(100_000, 90_000, at=t, messages=("sys", "a", "b", "c", "d", "e"))
    # An early edit: the provider served the part before it, the edit cost the rest.
    edited = _probe(120_000, 30_000, at=t + 5, prior=long, messages=("sys", "a", "edited", "c", "d", "e"))
    assert _reason(long, edited) == "history"
    swapped = _probe(100_000, 90_000, at=t, cred=1, messages=("sys", "a"))
    assert _reason(swapped, _probe(120_000, 0, at=t + 5, prior=swapped, cred=2, messages=("sys", "a", "b"))) == "account"
    efforted = _probe(100_000, 90_000, at=t, effort="low")
    event = cache_watch.miss(efforted, _probe(120_000, 0, at=t + 5, prior=efforted, effort="high"))
    assert (event["reason"], event["effort_from"], event["effort_to"]) == ("effort", "low", "high")


def test_miss_stays_quiet_when_nothing_notable_was_lost():
    t = cache_watch.BOOT + 10
    hot = _probe(100_000, 90_000, at=t)
    grown = ("sys", "q", "more")
    # Past the provider's cache lifetime: ordinary expiry.
    assert cache_watch.miss(hot, _probe(120_000, 0, at=t + cache_watch.DEFAULT_TTL_S + 1, prior=hot, messages=grown)) is None
    # Mostly served from cache.
    assert cache_watch.miss(hot, _probe(120_000, 80_000, at=t + 5, prior=hot, messages=grown)) is None
    # A chat that was never served from cache does not warn.
    cold = _probe(100_000, 0, at=t)
    assert cache_watch.miss(cold, _probe(120_000, 0, at=t + 5, prior=cold, messages=grown)) is None
    # A small implicit-cache prompt routed to a cold server.
    small = _probe(20_000, 18_000, at=t)
    assert cache_watch.miss(small, _probe(24_000, 0, at=t + 5, prior=small, messages=grown)) is None


def test_a_settings_miss_across_a_restart_is_not_reported():
    before = _probe(100_000, 90_000, at=cache_watch.BOOT - 5)
    after = _probe(120_000, 0, at=cache_watch.BOOT + 5, prior=before, messages=("new sys", "q"))
    assert cache_watch.miss(before, after) is None
    # The same change within one process is reported.
    hot = _probe(100_000, 90_000, at=cache_watch.BOOT + 5)
    assert _reason(hot, _probe(120_000, 0, at=cache_watch.BOOT + 10, prior=hot, messages=("new sys", "q"))) == "settings"
