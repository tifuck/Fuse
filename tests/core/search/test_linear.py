"""Extraction stays linear on hostile input.

Extraction runs in a worker thread, but CPython's re and the tree walks hold
the GIL, so one quadratic pass over a multi-megabyte page freezes the whole
server. Each case times the same work at n and 8n (CPU time of this thread,
best of three) and asserts the larger input costs about 8x, never the ~64x
of a quadratic pass. Only the ratio is asserted, so a loaded machine cannot
fail it; the cases together cost ~0.5 s.
"""
import time

import pytest

from app.search.extract import extract_page, find_windows, html_too_complex, select_passages
from app.search.providers.bing import parse_bing_html
from app.search.providers.ddg import parse_ddg_html
from app.search.providers.yahoo import parse_yahoo_html

FACTOR = 8
MAX_RATIO = 24  # linear work costs ~8x, a quadratic pass ~64x
FLOOR_S = 0.002  # below this, timer and allocator noise dominate


def cpu_cost(work):
    best = float("inf")
    for _ in range(3):
        started = time.thread_time()
        work()
        best = min(best, time.thread_time() - started)
    return best


def assert_linear(make, n):
    small, large = make(n), make(n * FACTOR)
    small_s, large_s = cpu_cost(small), cpu_cost(large)
    assert large_s < MAX_RATIO * max(small_s, FLOOR_S), f"{FACTOR}x input cost {large_s / max(small_s, 1e-9):.1f}x"


def html(body):
    return f"<html><body>{body}</body></html>"


CASES = {
    "space run without a newline": (
        lambda n: lambda: extract_page("a" + " " * n + "b", content_type="text/plain"), 100_000),
    "unclosed feed items": (
        lambda n: lambda: extract_page("<rss><channel><title>Feed</title>" + "<item" * (n // 5) + "<entry" * (n // 6),
                                       content_type="application/rss+xml"), 50_000),
    "unclosed fields in one feed item": (
        lambda n: lambda: [extract_page("<rss><item>" + opener * (n // len(opener)) + "</item></rss>",
                                        content_type="application/rss+xml")
                           for opener in ("<title", "<link", "<link rel=x>", "<pubDate", "<summary")], 50_000),
    "heading ending in a long mark run": (
        lambda n: lambda: [extract_page(html("<h2>Title" + filler * (n // len(filler)) + "x</h2><p>body</p>"))
                           for filler in ("#", "\u3000", "# ", "¶ ")], 25_000),
    "long class token": (
        lambda n: lambda: extract_page(html('<div class="' + "consent" * (n // 7) + '!">x</div><p>' + "word " * 100 + "</p>")),
        100_000),
    "unclosed metadata tags": (
        lambda n: lambda: [extract_page(raw * (n // len(raw)))
                           for raw in ("<title>", "<meta a", "<script type='application/ld+json'>")], 50_000),
    "complexity pre-scan of hostile markup": (
        lambda n: lambda: [html_too_complex(piece * (n // len(piece)))
                           for piece in ("<div ", "<!--", "<script>", "<div></span>", "<table><div></div>", "<svg><g/>", "<")],
        25_000),
    # Results pages can come from the user's own connector, crafted.
    "duckduckgo page of unclosed result links": (
        lambda n: lambda: [parse_ddg_html(piece * (n // len(piece)))
                           for piece in ('<a href="x" ', '<a rel="nofollow" href="x" ', 'href="x" class="result__a" >',
                                         '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example">',
                                         'class="result__snippet">', "class='result-snippet'>", "<")], 25_000),
    "bing and yahoo blocks of unclosed fields": (
        lambda n: lambda: [parse(marker + piece * (n // len(piece)))
                           for parse, marker in ((parse_bing_html, 'class="b_algo"'), (parse_yahoo_html, 'class="algo-sr"'))
                           for piece in ("<h2>", "<h3>", "<h2><a href", "<cite>", 'class="b_lineclamp2">',
                                         '<p class="fc-dustygray">', "<")], 25_000),
    "query and find over many paragraphs": (
        lambda n: lambda: (select_passages("needle words here.\n\n" * (n // 20), query="needle words"),
                           find_windows("needle words here.\n\n" * (n // 20), "words here")), 25_000),
}


@pytest.mark.parametrize("case", list(CASES))
def test_extraction_scales_linearly(case):
    make, n = CASES[case]
    assert_linear(make, n)


def test_find_and_excerpts_stay_linear_on_whitespace_only_lines():
    # Unicode spaces survive _clean_plain, so a text/plain page of "\u3000\n"
    # lines reaches find=, query= and search-time excerpts unchanged.
    assert_linear(lambda n: lambda: extract_page("x\n" + "\u3000\n" * n, content_type="text/plain", find="zz"), 600)
