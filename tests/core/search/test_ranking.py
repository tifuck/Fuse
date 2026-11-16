"""How hits from several engines become one ranked list: canonical-URL
dedup, corroboration, per-site diversity, the hit cap and the topic filter."""
from app.search.engine import MAX_HITS_MERGED, search_bulk
from app.search.providers.base import canonical_url

from ._support import Engines, hit, run


def merged(monkeypatch, query, **script):
    Engines(monkeypatch, **script)
    [res] = run(search_bulk([query], user_id=1))
    return res.hits


def test_tracking_variants_are_one_hit_but_one_engine_repeating_itself_is_not_corroboration(monkeypatch):
    hits = merged(
        monkeypatch, "widgets",
        ddg=[hit("https://one.example/item?utm_source=a", provider="ddg", rank=0),
             hit("https://one.example/item?utm_source=b", provider="ddg", rank=1),
             hit("https://two.example/item", provider="ddg", rank=2)],
        bing=[hit("https://two.example/item?fbclid=x", provider="bing", rank=2)],
    )
    keys = [canonical_url(h.url) for h in hits]
    assert keys == ["https://two.example/item", "https://one.example/item"]
    assert hits[0].providers == ("ddg", "bing")
    assert hits[1].providers == ("ddg",)


def test_canonical_url_drops_only_known_tracking_parameters():
    assert canonical_url("HTTPS://Example.COM:443/path?id=7&utm_campaign=x&gclid=y#part") == "https://example.com/path?id=7"
    assert canonical_url("https://example.com/?ref=essential") == "https://example.com/?ref=essential"
    assert canonical_url("ftp://example.com/x") == ""


def test_one_site_takes_at_most_two_places_unless_nothing_else_fills_the_list(monkeypatch):
    same = [hit(f"https://same.example/{i}", provider="ddg", rank=i) for i in range(10)]
    others = [hit(f"https://site{i}.example/r", provider="yahoo", rank=i) for i in range(8)]
    hits = merged(monkeypatch, "widgets", ddg=same, yahoo=others)
    assert len(hits) == MAX_HITS_MERGED
    assert sum("same.example" in h.url for h in hits) == 2

    few = [hit(f"https://site{i}.example/r", provider="yahoo", rank=i) for i in range(3)]
    hits = merged(monkeypatch, "gadgets", ddg=same, yahoo=few)
    assert len(hits) == MAX_HITS_MERGED, "the list is filled from the deferred same-site hits"
    assert {f"https://site{i}.example/r" for i in range(3)} <= {h.url for h in hits}


def test_off_topic_hits_drop_when_enough_hits_are_on_topic(monkeypatch):
    # Scraped engines answer flagged IPs with degraded rankings (dictionary
    # pages for "won" when asked who won the World Cup).
    degraded = [hit("https://dict.example/won", "WON Definition & Meaning", "The meaning of won", provider="bing"),
                hit("https://wiki.example/won", "South Korean won", "Currency of South Korea", provider="bing", rank=1)]
    good = [hit(f"https://site{i}.example/world-cup-2026", f"2026 FIFA World Cup final {i}",
                "Spain won the 2026 World Cup final", provider="yahoo", rank=i) for i in range(5)]
    hits = merged(monkeypatch, "who won the 2026 world cup", bing=degraded, yahoo=good)
    assert [h.url for h in hits] == [g.url for g in good]

    # Engine operators are not topic words.
    hits = merged(monkeypatch, "site:github.com solaar reprog controls -forum",
                  bing=[hit("https://dict.example/won", "WON Definition", "meaning", provider="bing")],
                  yahoo=[hit(f"https://h{i}.example/solaar", f"Solaar reprog controls {i}", "", provider="yahoo", rank=i)
                         for i in range(5)])
    assert "https://dict.example/won" not in [h.url for h in hits] and len(hits) == 5


def test_with_few_on_topic_hits_off_topic_ones_only_sink(monkeypatch):
    hits = merged(monkeypatch, "rust reqwest timeout example",
                  bing=[hit("https://a.example/x", "Unrelated page", "nothing", provider="bing")],
                  yahoo=[hit("https://b.example/reqwest-timeout", "Rust reqwest timeout example", provider="yahoo", rank=3)])
    assert [h.url for h in hits] == ["https://b.example/reqwest-timeout", "https://a.example/x"]
