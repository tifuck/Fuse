"""web_search backend: parallel fan-out, deadlines, caching, rate limits,
recency and live quotes, through search_bulk.

End-to-end tests answer the real providers' HTTP requests (DuckDuckGo lite,
Bing, Yahoo, HN, Wikipedia, CoinGecko, Brave) from scripted pages; deadline
tests script the providers themselves so time is controlled by events and
never-completing calls, not by sleeps.
"""
import asyncio
import base64
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from app import security
from app.config import settings
from app.search import cache, engine
from app.search.engine import search_bulk
from app.search.pack import MODEL_SEARCH_FAILED, pack_search
from app.search.providers.base import ProviderResponse

from ._support import EMPTY, Engines, Hang, hit, run


def ddg_page(*links):
    rows = "".join(
        f'<a rel="nofollow" href="//duckduckgo.com/l/?uddg={quote(url, safe="")}&amp;rut=x" '
        f"class='result-link'>{title}</a>\n<td class='result-snippet'>{snippet}</td>\n"
        for url, title, snippet in links
    )
    return f"<html><body><table>{rows}</table></body></html>"


def bing_page(*links):
    def u(url):
        return "a1" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

    return "".join(
        f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;p=x&amp;u={u(url)}&amp;ntb=1">{title}</a></h2>'
        f'<p class="b_lineclamp2">{snippet}</p></li>'
        for url, title, snippet in links
    ) or '<html><body><ol id="b_results"></ol></body></html>'


def yahoo_page(*links):
    return "".join(
        f'<div class="algo-sr"><a href="https://r.search.yahoo.com/x/RU={quote(url, safe="")}/RK=2/RS=x">'
        f'<h3 class="title">{title}</h3></a><p class="fc-dustygray">{snippet}</p></div>'
        for url, title, snippet in links
    ) or "<html><body>We did not find results</body></html>"


def json_response(data, status=200):
    return lambda _req: httpx.Response(status, json=data)


def html_response(body, status=200):
    return lambda _req: httpx.Response(status, text=body, headers={"content-type": "text/html"})


def all_engines(web, *, ddg=(), bing=(), yahoo=(), hn=(), wiki=()):
    web.on("https://lite.duckduckgo.com/lite/", html_response(ddg_page(*ddg)))
    web.on("https://html.duckduckgo.com/html/", html_response(ddg_page(*ddg)))
    web.on("https://www.bing.com/search", html_response(bing_page(*bing)))
    web.on("https://search.yahoo.com/search", html_response(yahoo_page(*yahoo)))
    web.on("https://hn.algolia.com/api/v1/search",
           json_response({"hits": [{"title": t, "url": u} for u, t, _ in hn]}))
    web.on("https://en.wikipedia.org/w/api.php",
           json_response({"query": {"search": [{"title": t, "snippet": s} for _, t, s in wiki]}}))


def sent(web, prefix):
    return [r for r in web.requests if web.logical(r).startswith(prefix)]


SHARED = "https://github.com/ulab-uiuc/LLMRouter"


def test_fan_out_merges_dedupes_and_ranks_corroborated_hits_first(web):
    all_engines(
        web,
        ddg=[("https://ddg-only.example/llm-router", "Configure an LLM router (ddg)", "router config"),
             (SHARED + "?utm_source=ddg", "LLMRouter: configure an LLM router", "")],
        bing=[(SHARED, "LLMRouter GitHub: configure LLM router", "An open-source LLM router to configure")],
        yahoo=[(SHARED, "LLMRouter configure LLM router", "x"),
               ("https://yahoo-only.example/router", "LLM router configure guide", "y")],
        hn=[("https://hn-only.example/post", "Show HN: configure your LLM router", "")],
        wiki=[(None, "LLM router configure", "routing")],
    )
    out = run(search_bulk(["how to configure an llm router"], user_id=1))

    [res] = out
    assert res.kind == "ok" and not res.cached
    assert set(res.provider.split("+")) == {"ddg", "bing", "yahoo", "hn", "wikipedia"}
    urls = [h.url.split("?")[0] for h in res.hits]
    assert urls.count(SHARED) == 1, "one URL from three engines is one hit"
    assert urls[0] == SHARED, "the hit three engines agree on ranks first"
    assert res.hits[0].providers == ("ddg", "bing", "yahoo")
    assert res.hits[0].snippet == "An open-source LLM router to configure", "a missing snippet is filled from another engine"
    assert {"https://ddg-only.example/llm-router", "https://yahoo-only.example/router",
            "https://hn-only.example/post", "https://en.wikipedia.org/wiki/LLM_router_configure"} <= set(urls)
    assert all(h.query == "how to configure an llm router" for h in res.hits)

    before = len(web.requests)
    again = run(search_bulk(["How to configure an LLM router?"], user_id=1))
    assert again[0].cached and [h.url for h in again[0].hits] == [h.url for h in res.hits]
    assert len(web.requests) == before, "a cached query sends no request"


def test_unreachable_and_failing_engines_do_not_block_the_one_that_answers(web):
    # DuckDuckGo and Wikipedia are unreachable (no route), Bing errors, HN
    # sends garbage; Yahoo alone answers.
    web.on("https://www.bing.com/search", html_response("oops", status=500))
    web.on("https://hn.algolia.com/api/v1/search", lambda _r: httpx.Response(200, text="{not json"))
    web.on("https://search.yahoo.com/search",
           html_response(yahoo_page(("https://docs.example/timeouts", "Python timeouts guide", "about timeouts"))))

    [res] = run(search_bulk(["how to set python timeouts"], user_id=1))

    assert res.kind == "ok" and res.provider == "yahoo"
    assert [h.url for h in res.hits] == ["https://docs.example/timeouts"]


def test_a_provider_that_raises_is_skipped(monkeypatch):
    engines = Engines(monkeypatch, ddg=RuntimeError("parser crashed"), bing=[hit("https://ok.example/a", provider="bing")])
    [res] = run(search_bulk(["widgets"], user_id=1))
    assert res.kind == "ok" and [h.url for h in res.hits] == ["https://ok.example/a"]
    assert {"ddg", "bing", "yahoo"} <= engines.asked()


def test_every_engine_failing_is_an_error_that_is_not_cached(monkeypatch):
    engines = Engines(monkeypatch, ddg=RuntimeError("boom"),
                      bing=ProviderResponse(hits=[], status=503, error="http"),
                      yahoo=ProviderResponse(hits=[], status=0, error="timeout"))
    [res] = run(search_bulk(["widgets"], user_id=1))
    assert res.kind == "provider_error" and res.error == MODEL_SEARCH_FAILED and not res.hits
    run(search_bulk(["widgets"], user_id=1))
    assert len(engines.calls["bing"]) == 2, "a failed search is retried, not served from cache"


def test_only_genuine_empty_pages_mean_no_results(monkeypatch):
    Engines(monkeypatch)  # every engine parsed a real, empty results page
    [res] = run(search_bulk(["how to decode a nonexistent format"], user_id=1))
    assert (res.kind, res.error) == ("empty", "No results.")

    # A bot challenge or an unparsed page is a failure, not "no results"; so
    # is a live-quote lookup where no coin API answered.
    Engines(monkeypatch, bing=ProviderResponse(hits=[], status=200, error="blocked"))
    [res] = run(search_bulk(["how to decode another format"], user_id=1))
    assert res.kind == "provider_error"
    Engines(monkeypatch, quotes=ProviderResponse(hits=[], status=0, error="empty"))
    [res] = run(search_bulk(["btc price"], user_id=1))
    assert res.kind == "provider_error"


# -- deadlines ----------------------------------------------------------------


def test_soft_deadline_returns_arrived_hits_and_cancels_stragglers(monkeypatch):
    slow = Hang()
    Engines(monkeypatch, ddg=[hit("https://fast.example/widgets", "widgets")], bing=slow, yahoo=slow)
    monkeypatch.setattr(engine, "QUERY_SOFT_DEADLINE", 0.0)
    monkeypatch.setattr(engine, "QUERY_DEADLINE", 600.0)

    # The guard only fails a broken build fast: a correct one returns as soon
    # as ddg's hits arrive, long before the 600 s hard deadline.
    [res] = run(asyncio.wait_for(search_bulk(["widgets"], user_id=1), 10))

    assert res.kind == "ok" and [h.url for h in res.hits] == ["https://fast.example/widgets"]
    assert slow.started == 2 and slow.cancelled == 2


def test_soft_deadline_keeps_waiting_while_no_hits_have_arrived(monkeypatch):
    async def late(_query):
        for _ in range(5):
            await asyncio.sleep(0)
        return ProviderResponse(hits=[hit("https://late.example/widgets", provider="yahoo")], status=200)

    Engines(monkeypatch, ddg=EMPTY, bing=ProviderResponse(hits=[], status=0, error="timeout"), yahoo=late)
    monkeypatch.setattr(engine, "QUERY_SOFT_DEADLINE", 0.0)
    monkeypatch.setattr(engine, "QUERY_DEADLINE", 600.0)

    [res] = run(asyncio.wait_for(search_bulk(["widgets"], user_id=1), 10))

    assert res.kind == "ok" and [h.url for h in res.hits] == ["https://late.example/widgets"]


def test_hard_deadline_with_nothing_arrived_fails_and_cancels_everything(monkeypatch):
    hang = Hang()
    Engines(monkeypatch, ddg=hang, bing=hang, yahoo=hang, hn=EMPTY, wikipedia=hang)
    monkeypatch.setattr(engine, "QUERY_SOFT_DEADLINE", 0.0)
    monkeypatch.setattr(engine, "QUERY_DEADLINE", 0.1)

    [res] = run(search_bulk(["how to test a hard deadline"], user_id=1))

    assert res.kind == "provider_error" and res.error == MODEL_SEARCH_FAILED
    assert hang.started and hang.cancelled == hang.started


def test_a_slow_query_does_not_hold_back_its_siblings_result(monkeypatch):
    async def per_query(query):
        if "slow" in query:
            return await Hang()(query)
        return ProviderResponse(hits=[hit("https://fast.example/" + query.replace(" ", "-"))], status=200)

    Engines(monkeypatch, ddg=per_query)
    monkeypatch.setattr(engine, "QUERY_SOFT_DEADLINE", 0.0)
    monkeypatch.setattr(engine, "QUERY_DEADLINE", 0.1)
    done = []
    out = run(search_bulk(["fast query", "slow query"], user_id=1, on_query_done=done.append))
    assert [r.kind for r in out] == ["ok", "provider_error"]
    assert [r.query for r in done] == ["fast query", "slow query"], "each query reports when it finishes"


def test_an_interrupted_call_runs_no_provider(monkeypatch):
    engines = Engines(monkeypatch, ddg=[hit("https://a.example/")])
    cancel = asyncio.Event()
    cancel.set()
    [res] = run(search_bulk(["widgets"], user_id=1, cancel=cancel))
    assert res.kind == "provider_error" and res.error == "Tool interrupted."
    assert not engines.asked()


# -- bulk ---------------------------------------------------------------------


def test_bulk_collapses_duplicates_reports_short_and_clipped_queries(monkeypatch):
    engines = Engines(monkeypatch, ddg=[hit("https://a.example/")])
    queries = ["Rust async", "rust   ASYNC?", " ", *[f"topic {i}" for i in range(engine.MAX_QUERIES_PER_BULK + 1)]]
    out = run(search_bulk(queries, user_id=1))

    assert len(out) == len(queries), "one result per slot, in order"
    assert engines.calls["ddg"].count("Rust async") == 1 and "rust ASYNC" not in engines.calls["ddg"]
    assert out[0].kind == out[1].kind == "ok"
    assert out[2].error == "Query too short."
    ran = [r for r in out[3:] if r.kind == "ok"]
    clipped = [r for r in out[3:] if "dropped" in (r.error or "")]
    assert len(ran) == engine.MAX_QUERIES_PER_BULK - 1 and len(clipped) == 2
    assert all("failed" not in r.error.lower() for r in clipped), "a clipped query is not a search failure"
    assert len(engines.calls["ddg"]) == engine.MAX_QUERIES_PER_BULK


def test_operators_reach_web_engines_but_not_hn_or_wikipedia(monkeypatch):
    engines = Engines(monkeypatch)
    run(search_bulk(['how to configure "max_form_parts" werkzeug -flask'], user_id=1))
    assert engines.calls["ddg"] == ['how to configure "max_form_parts" werkzeug -flask']
    assert engines.calls["hn"] == ["how to configure max_form_parts werkzeug"]
    assert len(engines.calls["wikipedia"]) == 1

    engines = Engines(monkeypatch)
    run(search_bulk(["how site:github.com solaar reprog controls"], user_id=1))
    assert engines.calls["bing"] == ["how site:github.com solaar reprog controls"]
    assert not engines.calls["hn"] and not engines.calls["wikipedia"], "they cannot honour site:"

    engines = Engines(monkeypatch)
    run(search_bulk(["Ada Lovelace"], user_id=1))
    assert engines.asked() == {"ddg", "bing", "yahoo"}, "a bare entity name skips HN and Wikipedia search"


# -- cache --------------------------------------------------------------------


def test_cache_lifetimes_depend_on_outcome_and_recency(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: clock.now))
    good = [hit("https://a.example/widgets")]
    engines = Engines(monkeypatch, ddg=good)

    def asks(query, recency=None):
        before = len(engines.calls["ddg"])
        run(search_bulk([query], user_id=1, recency_days=recency))
        return len(engines.calls["ddg"]) - before

    assert asks("widgets") == 1
    assert asks("widgets", recency=1) == 1, "another recency is another cache entry"
    clock.now += 60
    assert asks("widgets") == 0, "ordinary results live 10 minutes"
    assert asks("widgets", recency=1) == 1, "past-day results live 45 seconds"
    clock.now += 541
    assert asks("widgets") == 1

    engines = Engines(monkeypatch)  # a genuine empty results page
    assert asks("gadgets") == 1
    assert asks("gadgets") == 0
    clock.now += 21
    assert asks("gadgets") == 1, "no results are re-asked after 20 seconds"


# -- rate limits --------------------------------------------------------------


def test_search_rate_limit_is_per_user_and_cached_queries_are_free(monkeypatch):
    monkeypatch.setattr(engine, "SEARCH_PER_MIN", 2)
    Engines(monkeypatch, ddg=[hit("https://a.example/")])

    assert [r.kind for r in run(search_bulk(["query one", "query two"], user_id=1))] == ["ok", "ok"]
    assert run(search_bulk(["query one"], user_id=1))[0].cached
    [limited] = run(search_bulk(["query three"], user_id=1))
    assert (limited.kind, limited.status) == ("rate_limit", 429)
    assert limited.error.startswith("Search rate limit reached (shared by all your chats and agents).")
    assert "Try again in about" in limited.error and "seconds" in limited.error
    assert run(search_bulk(["query four"], user_id=2))[0].kind == "ok", "another user has their own budget"


def test_hourly_search_limit_holds_across_minutes(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(security, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(engine, "SEARCH_PER_MIN", 2)
    monkeypatch.setattr(engine, "SEARCH_PER_HOUR", 3)
    Engines(monkeypatch, ddg=[hit("https://a.example/")])

    def search(query):
        return run(search_bulk([query], user_id=1))[0].kind

    assert [search("q one"), search("q two")] == ["ok", "ok"]
    clock.now += 61
    assert [search("q three"), search("q four")] == ["ok", "rate_limit"]
    clock.now += 61
    assert search("q five") == "rate_limit", "the hourly budget is spent"
    clock.now += 3600
    assert search("q six") == "ok"


def test_searches_refused_per_minute_do_not_spend_the_hourly_budget(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(security, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(engine, "SEARCH_PER_MIN", 2)
    monkeypatch.setattr(engine, "SEARCH_PER_HOUR", 4)
    Engines(monkeypatch, ddg=[hit("https://a.example/")])

    def search(query):
        return run(search_bulk([query], user_id=1))[0].kind

    assert [search("q one"), search("q two")] == ["ok", "ok"]
    # A model retrying through the per-minute refusal.
    assert [search(f"retry {i}") for i in range(3)] == ["rate_limit"] * 3
    clock.now += 61
    # Two of four hourly searches were served; two remain.
    assert search("q three") == "ok"


# -- recency, brave, quotes and engine circuits -----------------------------------


def test_recency_reaches_every_engine_and_is_inferred_from_the_query(web):
    all_engines(web)
    run(search_bulk(["how to read rust release notes"], user_id=1, recency_days=1))
    assert sent(web, "https://lite.duckduckgo.com/lite/")[0].url.params["df"] == "d"
    assert sent(web, "https://www.bing.com/search")[0].url.params["filters"] == 'ex1:"ez1"'
    assert sent(web, "https://search.yahoo.com/search")[0].url.params["age"] == "1d"
    assert sent(web, "https://hn.algolia.com/")[0].url.params["numericFilters"].startswith("created_at_i>")

    web.requests.clear()
    run(search_bulk(["rust compiler news"], user_id=1))
    assert sent(web, "https://lite.duckduckgo.com/lite/")[0].url.params["df"] == "w", "news means the past week"

    web.requests.clear()
    run(search_bulk(["how to profile rust code"], user_id=1))
    assert "df" not in sent(web, "https://lite.duckduckgo.com/lite/")[0].url.params
    assert "numericFilters" not in sent(web, "https://hn.algolia.com/")[0].url.params


def test_brave_runs_with_a_key_and_stops_after_an_auth_failure(web, monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "brave-key")
    all_engines(web, yahoo=[("https://docs.example/widgets", "Widgets docs", "")])
    web.on("https://api.search.brave.com/res/v1/web/search",
           json_response({"web": {"results": [{"title": "Widgets", "url": "https://brave.example/w", "description": "d"}]}}),
           json_response({"error": "unauthorized"}, status=401))

    [first] = run(search_bulk(["how widgets work"], user_id=1, recency_days=1))
    [brave_req] = sent(web, "https://api.search.brave.com/")
    assert brave_req.headers["x-subscription-token"] == "brave-key"
    assert brave_req.url.params["freshness"] == "pd"
    assert "https://brave.example/w" in [h.url for h in first.hits]

    run(search_bulk(["how gadgets work"], user_id=1))  # 401: the key is rejected
    run(search_bulk(["how sprockets work"], user_id=1))
    assert len(sent(web, "https://api.search.brave.com/")) == 2, "a rejected key is not retried on every search"


def test_live_price_query_leads_with_quotes_and_skips_hn_and_wikipedia(web):
    all_engines(web, ddg=[("https://news.example/btc", "Bitcoin price today", "BTC news")])
    web.on("https://api.coingecko.com/api/v3/simple/price",
           json_response({"bitcoin": {"usd": 79172, "last_updated_at": 0}}))
    web.on("https://api.binance.com/", json_response({}, status=451))

    [res] = run(search_bulk(["BTC USD price"], user_id=1))

    assert res.kind == "ok" and "quotes" in res.provider.split("+")
    [quote_hit] = [h for h in res.hits if h.provider == "quotes"]
    assert "bitcoin = 79172 USD" in quote_hit.snippet
    packed = pack_search([res])
    assert packed.index("LIVE QUOTE") < packed.index("https://news.example/btc"), "the live quote leads the results"
    assert sent(web, "https://lite.duckduckgo.com/lite/")[0].url.params["df"] == "d", "a price is a past-day query"
    assert not sent(web, "https://hn.algolia.com/") and not sent(web, "https://en.wikipedia.org/")

    web.requests.clear()
    run(search_bulk(["bitcoin price history"], user_id=1))
    assert not sent(web, "https://api.coingecko.com/"), "a historical price is not a live quote"


def test_a_duckduckgo_challenge_pauses_it_for_later_searches(web):
    all_engines(web, yahoo=[("https://docs.example/a", "Widgets", "")])
    web.on("https://lite.duckduckgo.com/lite/", html_response("<html>anomaly</html>", status=403))
    run(search_bulk(["how widgets work"], user_id=1))
    assert len(sent(web, "https://lite.duckduckgo.com/")) == 1
    assert not sent(web, "https://html.duckduckgo.com/"), "a challenge is not retried on the second endpoint"

    [res] = run(search_bulk(["how gadgets work"], user_id=1))
    assert res.kind == "ok"
    assert len(sent(web, "https://lite.duckduckgo.com/")) == 1, "the provider is paused"
