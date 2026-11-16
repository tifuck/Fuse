"""Search-time excerpts: the top hits' pages are read while the search call is
open and the relevant passage rides along with each hit; pages that miss the
deadline keep loading into the page cache for a later read_url."""
import asyncio

import httpx

from app.search import engine
from app.search.engine import fetch_url, is_fetch_cached
from app.search.excerpts import attach_excerpts
from app.search.pack import flatten_hits, pack_search
from tests.core.helpers import wait_until_async

from ._support import hit, result, run


def markdown(name):
    return (f"# Page {name}\n\nIntro words about the product.\n\n## Pricing\n\nThe alpha pricing is $5 per seat.\n\n"
            "## Limits\n\nThe beta limits are 10 rps per key.")


def serve(web, dns, *names):
    for i, name in enumerate(names):
        dns.add(f"{name}.example", f"93.184.216.{10 + i}")
        web.page(f"https://{name}.example/p", markdown(name), ctype="text/markdown")


def results():
    return [result("alpha pricing", [hit(f"https://a{r}.example/p", provider="yahoo", rank=r) for r in range(3)]),
            result("beta limits", [hit(f"https://b{r}.example/p", provider="yahoo", rank=r) for r in range(3)])]


def test_each_querys_best_hits_are_read_first_and_carry_their_relevant_passage(web, dns, monkeypatch):
    monkeypatch.setattr(engine, "FETCH_PER_MIN", 0)  # excerpts never spend the model's read budget
    serve(web, dns, "a0", "a1", "a2", "b0", "b1", "b2")

    out = run(attach_excerpts(results(), user_id=1, max_pages=3))

    assert {r.headers["host"] for r in web.requests} == {"a0.example", "b0.example", "a1.example"}
    alpha, beta = out
    assert "alpha pricing is $5" in alpha.hits[0].excerpt and "beta limits" not in alpha.hits[0].excerpt
    assert "beta limits are 10 rps" in beta.hits[0].excerpt
    assert alpha.hits[1].excerpt and not alpha.hits[2].excerpt and not beta.hits[1].excerpt
    assert [h["status"] for h in flatten_hits(out)].count("read") == 3
    assert "[page excerpt, read from the page]" in pack_search(out)
    assert all(is_fetch_cached(f"https://{h}.example/p") for h in ("a0", "a1", "b0")), "a follow-up read_url is instant"


def test_quotes_pdfs_and_hosts_behind_a_bot_wall_are_not_read(web, dns):
    dns.add("walled.example", "93.184.216.20")
    web.page("https://walled.example/", "denied", status=403)
    run(fetch_url("https://walled.example/", user_id=1, allowed=set()))
    web.requests.clear()
    hits = [hit("https://www.coingecko.com/en/coins/bitcoin", provider="quotes"),
            hit("https://walled.example/x", provider="yahoo"),
            hit("https://files.example/paper.pdf", provider="yahoo")]

    [out] = run(attach_excerpts([result("q", hits)], user_id=1))

    assert web.requests == [] and not any(h.excerpt for h in out.hits)


def test_pages_missing_the_deadline_finish_into_the_page_cache(web, dns):
    serve(web, dns, "a0")
    dns.add("b0.example", "93.184.216.30")
    release = asyncio.Event()

    async def slow(_request):
        await release.wait()
        return httpx.Response(200, text=markdown("b0"), headers={"content-type": "text/markdown"})

    web.on("https://b0.example/p", slow)
    items = [result("alpha pricing", [hit("https://a0.example/p", provider="yahoo")]),
             result("beta limits", [hit("https://b0.example/p", provider="yahoo")])]

    async def scenario():
        await fetch_url("https://a0.example/p", user_id=1, allowed=set())  # cached: its read completes at once
        # deadline=0: the excerpt wait ends on the loop's next turn, after every
        # read has started but before any network read could have finished.
        out = await attach_excerpts(items, user_id=1, deadline=0)
        assert "alpha pricing is $5" in out[0].hits[0].excerpt
        assert out[1].hits[0].excerpt == ""
        assert not is_fetch_cached("https://b0.example/p")
        release.set()
        await wait_until_async(lambda: is_fetch_cached("https://b0.example/p"))

    run(scenario())
