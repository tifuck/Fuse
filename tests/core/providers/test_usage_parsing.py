"""Usage normalization: every usage shape a provider sends becomes the same
token, cache and cost fields, and cumulative stream snapshots merge without
freezing early counts or double-counting reasoning.

These feed billing: a wrong output_tokens or cost is a money error.
"""
import pytest

from app.proxy import merge_usage, usage_from_obj


def _u(usage, **top):
    return usage_from_obj({"usage": usage, **top})


@pytest.mark.parametrize("usage,expected", [
    # OpenAI Chat Completions: reasoning inside completion, cache read in details.
    ({"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
      "prompt_tokens_details": {"cached_tokens": 40}, "completion_tokens_details": {"reasoning_tokens": 10}},
     {"prompt_tokens": 100, "output_tokens": 30, "visible_tokens": 20, "reasoning_tokens": 10,
      "cached_tokens": 40, "total_tokens": 130}),
    # OpenAI Responses names.
    ({"input_tokens": 50, "output_tokens": 8, "input_tokens_details": {"cached_tokens": 5},
      "output_tokens_details": {"reasoning_tokens": 3}},
     {"prompt_tokens": 50, "output_tokens": 8, "visible_tokens": 5, "cached_tokens": 5, "total_tokens": 58}),
    # Anthropic-style cache names; prompt already includes reads and writes.
    ({"prompt_tokens": 100, "completion_tokens": 5, "cache_read_input_tokens": 60, "cache_creation_input_tokens": 30},
     {"cached_tokens": 60, "cache_write_tokens": 30, "total_tokens": 105}),
    # xAI: reasoning reported apart from completion, total says so.
    ({"prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 20, "total_tokens": 35},
     {"output_tokens": 25, "visible_tokens": 5, "reasoning_tokens": 20, "total_tokens": 35}),
    # Kimi / OpenRouter / MiMo: total = prompt + completion, reasoning inside.
    ({"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40,
      "completion_tokens_details": {"reasoning_tokens": 20}},
     {"output_tokens": 30, "visible_tokens": 10, "total_tokens": 40}),
    # No total and reasoning > completion: the counts can only be disjoint.
    ({"prompt_tokens": 10, "completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 9}},
     {"output_tokens": 13, "visible_tokens": 4, "total_tokens": 23}),
    # A total above prompt + output keeps the extra generated tokens.
    ({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 40},
     {"output_tokens": 30, "total_tokens": 40}),
    # Cache counts can never exceed the prompt they are part of.
    ({"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 9}},
     {"cached_tokens": 10, "cache_write_tokens": 0}),
    # Garbage values read as zero, never as a huge or negative count.
    ({"prompt_tokens": True, "completion_tokens": float("nan"), "total_tokens": 10**30},
     {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
])
def test_every_usage_shape_normalizes_to_the_same_fields(usage, expected):
    got = _u(usage)
    assert {k: got[k] for k in expected} == expected
    assert got["usage_status"] == "complete"


@pytest.mark.parametrize("usage,top,micros", [
    ({"cost": 0.0021}, {}, 2100),
    ({"cost": "0.5"}, {}, 500_000),
    ({"cost_in_nano_usd": 1_500_000}, {}, 1500),
    ({"cost_in_usd_ticks": 31_000}, {}, 3),  # 10 billion ticks per USD, rounded
    ({"cost_micros": 77}, {}, 77),
    ({"cost": {"amount": 12, "currency": "cents"}}, {}, 120_000),
    # Half a micro rounds up, never to the even neighbour (float round gives 2).
    ({"cost": 0.0000025}, {}, 3),
    ({"cost": "0.0000025"}, {}, 3),
    ({"cost": {"amount": 0.00025, "currency": "cents"}}, {}, 3),
    ({"cost_in_nano_usd": 2_500}, {}, 3),
    ({"cost": {"amount": 1, "currency": "USD"}}, {}, 1_000_000),
    # Another currency is not dollars: the cost is unknown, not 1 USD.
    ({"cost": {"amount": 1, "currency": "eur"}}, {}, None),
    ({"cost": {"amount": 1, "currency": "eur"}, "cost_usd": 1.08}, {}, 1_080_000),
    ({}, {"cost": 0.01}, 10_000),  # top-level cost beside the usage dict
    ({"cost": -1}, {}, None),
    ({"cost": float("inf")}, {}, None),
    ({}, {}, None),
])
def test_reported_cost_shapes_become_exact_micros(usage, top, micros):
    assert _u({"prompt_tokens": 1, **usage}, **top)["cost_micros"] == micros


def test_nanogpt_cost_is_read_from_its_pricing_block_and_zero_means_subscription():
    paid = _u({"prompt_tokens": 100, "completion_tokens": 5, "cache_read_input_tokens": 40,
               "prompt_tokens_details": {"cached_tokens": 7}},
              x_nanogpt_pricing={"cost": 0.0021, "paymentSource": "USD"})
    # NanoGPT documents its Anthropic-style counts as authoritative.
    assert (paid["cost_micros"], paid["cached_tokens"]) == (2100, 40)

    covered = _u({"prompt_tokens": 100, "completion_tokens": 5}, x_nanogpt_pricing={"cost": 0})
    # Subscription-covered calls report $0: leave them to the price estimate.
    assert covered["cost_micros"] is None


def test_cache_status_tells_a_reported_zero_from_a_missing_field():
    assert _u({"prompt_tokens": 5, "prompt_tokens_details": {"cached_tokens": 0}})["cache_usage_status"] == "known"
    assert _u({"prompt_tokens": 5, "cache_read_input_tokens": 0})["cache_usage_status"] == "known"
    assert _u({"prompt_tokens": 5})["cache_usage_status"] == "unknown"


def test_no_usage_is_an_explicit_unknown_with_zero_tokens():
    empty = usage_from_obj({"choices": []})
    assert empty["usage_status"] == "unknown" and empty["cache_usage_status"] == "unknown"
    assert empty["cost_micros"] is None
    assert all(v == 0 for k, v in empty.items() if k.endswith("_tokens"))


def test_stream_snapshots_merge_as_cumulative_maxima_not_sums():
    early = _u({"prompt_tokens": 100, "completion_tokens": 3})
    final = _u({"prompt_tokens": 100, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 60}},
               cost=0.002)

    merged = merge_usage(early, final)

    assert (merged["prompt_tokens"], merged["completion_tokens"], merged["cached_tokens"]) == (100, 40, 60)
    assert merged["cost_micros"] == 2000 and merged["cache_usage_status"] == "known"
    # A later snapshot without the cache split or cost never downgrades them.
    later = merge_usage(merged, _u({"prompt_tokens": 100, "completion_tokens": 40}))
    assert later["cache_usage_status"] == "known" and later["cost_micros"] == 2000
    # An empty first side takes the other whole; an empty extra changes nothing.
    assert merge_usage({}, final) == final and merge_usage(final, None) == final
