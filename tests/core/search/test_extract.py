"""Page extraction: what the model reads from HTML, markdown, plain text,
JSON and feeds; find/query passages and paging over the full text."""
import json

from app.search.extract import (FETCH_CHARS, PASSAGE_CHARS, TABLE_MAX_ROWS, best_excerpt, extract_page, find_windows,
                                select_passages)

PAD = "<p>" + "Ordinary article body text. " * 12 + "</p>"


def test_html_keeps_the_article_and_drops_site_chrome_scripts_and_hidden_text():
    html = (
        "<html><head><title>GLM-5.3 released</title><style>body{color:red}</style>"
        "<script>var secretBundle='xyzzy';</script></head><body>"
        "<header>Global navigation</header>"
        "<nav><ul><li><a href='/'>Home</a></li><li><a href='/de'>Deutsch</a></li></ul></nav>"
        "<div class='cookie-consent'>We use cookies</div>"
        "<aside class='sidebar'>Related ads</aside>"
        "<article><header><h1>GLM-5.3 released</h1><p>By A. Reporter</p></header>"
        "<p>GLM-5.3 is out today with faster decode.<sup class='reference'><a href='#c1'>[3]</a></sup></p>"
        "<p hidden>secret hidden text</p><span class='sr-only'>Skip to content</span>"
        "<div style='display: none'>invisible</div>" + PAD + "</article>"
        "<footer><ul><li>Privacy policy</li><li>Cookie settings</li></ul></footer></body></html>"
    )
    page = extract_page(html, url="https://news.example/glm")

    assert page.kind == "html" and page.title == "GLM-5.3 released" and not page.truncated
    assert page.text.startswith("# GLM-5.3 released"), "the article, not the chrome, opens the text"
    assert "By A. Reporter" in page.text and "GLM-5.3 is out today with faster decode." in page.text
    for junk in ("xyzzy", "color:red", "Global navigation", "Deutsch", "We use cookies", "Related ads", "[3]",
                 "secret hidden text", "Skip to content", "invisible", "Privacy policy", "Cookie settings"):
        assert junk not in page.text, junk


def test_malformed_and_cut_off_html_still_yields_its_text():
    cut = "<html><head><title>Doc</title></head><body><main><article><p>" + "Meaningful article body. " * 30
    assert "Meaningful article body" in extract_page(cut).text, "a download cut at the byte cap"
    crossed = "<html><body><article><div><p>Malformed but useful</div><p>Still present</article>"
    page = extract_page(crossed)
    assert "Malformed but useful" in page.text and "Still present" in page.text
    unclosed_li = ("<body><nav><ul><li><a href=/>Home<li><a href=/docs>Docs</ul></nav><main><article><h1>Releases</h1><p>"
                   + "Electron ships every eight weeks. " * 8 + "<ul><li>alpha<li>beta<li>stable</ul></article></main></body>")
    page = extract_page(unclosed_li)
    assert "- alpha\n- beta\n- stable" in page.text and "Docs" not in page.text


def test_links_are_resolved_deduplicated_and_placed_in_the_text():
    html = (
        "<article><p>Read <a href='/guide#start'>the guide</a> and <a href='https://other.test/report'>external report</a>"
        " and <a href='/guide'>the guide again</a>.</p>"
        "<a href='#local'>local section</a><a href='javascript:void(0)'>bad</a><a href='mailto:a@b.c'>mail</a>"
        "<a href='https://[your-project].supabase.co/rest'>placeholder</a>" + PAD + "</article>"
    )
    page = extract_page(html, url="https://example.test/news/item")
    assert [(link["url"], link["text"]) for link in page.links] == [
        ("https://example.test/guide", "the guide"), ("https://other.test/report", "external report")]
    for link in page.links:
        assert page.full_text[link["at"]:link["at"] + len(link["text"])] == link["text"]


def test_structure_survives_as_markdown():
    html = (
        "<article>" + PAD +
        "<div class='highlight-python3'><div class='highlight'><pre><span class='linenos'>1</span>async def main():\n"
        "    if ready:\n        await run()\n</pre></div></div>"
        "<p>Call <code>asyncio.run(main())</code> once.</p>"
        "<table><thead><tr><th>Model</th><th>Input</th></tr></thead>"
        "<tbody><tr><td>grok-4.7</td><td>$2 | cached $0.5</td></tr><tr><td>glm-5</td><td>$1</td></tr></tbody></table>"
        "<table role='presentation'><tr><td>Revenue USD</td><td>12345</td></tr></table>"
        "<ol start=3><li>three<ul><li>inner</li></ul></li><li>four</li></ol>"
        "<dl><dt><code>callbackFn</code></dt><dd><p>A function to run.</p></dd></dl></article>"
    )
    text = extract_page(html).text
    assert "```python3\nasync def main():\n    if ready:\n        await run()\n```" in text
    assert "Call `asyncio.run(main())` once." in text
    assert "| Model | Input |\n| --- | --- |\n| grok-4.7 | $2 \\| cached $0.5 |\n| glm-5 | $1 |" in text
    assert "Revenue USD" in text and "12345" in text, "a layout table keeps its values"
    assert "3. three\n   - inner\n4. four" in text
    assert "`callbackFn`\n: A function to run." in text


def test_long_tables_are_capped_per_table_and_say_so():
    rows = "".join(f"<tr><td>row-{i}</td><td>value-{i}</td></tr>" for i in range(TABLE_MAX_ROWS + 5))
    text = extract_page(f"<article><table>{rows}</table><table><tr><td>second-table-row</td></tr></table></article>").text
    assert f"row-{TABLE_MAX_ROWS - 1}" in text and f"row-{TABLE_MAX_ROWS}" not in text
    assert "(… 5 more table rows not shown)" in text
    assert "second-table-row" in text


def test_structured_data_fills_in_for_lost_markup_and_dates_are_read():
    body = "<h2>Details</h2><p>Useful &amp; safe.</p><style>.secret{}</style>" + " More of the story." * 30
    ld = json.dumps({"@type": "NewsArticle", "headline": "Structured", "articleBody": body, "datePublished": "2026-10-01T08:00"})
    page = extract_page(f"<html><head><script type='application/ld+json'>{ld}</script></head><body><p>chrome</p></body></html>")
    assert page.text.startswith("# Structured") and "## Details" in page.text and "Useful & safe." in page.text
    assert "secret" not in page.text and page.published == "2026-10-01"

    meta = "<head><meta property='article:published_time' content='2026-09-09T10:00:00Z'></head>"
    assert extract_page(f"<html>{meta}<body><article>{PAD}</article></body></html>").published == "2026-09-09"


def test_a_script_shell_is_reported_as_unreadable_not_invented():
    page = extract_page("<html><head><title>App</title><meta property='og:description' content='A chart app'></head>"
                        "<body><div id='root'></div><script>var bundle=1</script></body></html>")
    assert page.kind == "spa" and not page.links
    assert page.text.startswith("App\nA chart app\n") and "do not invent" in page.text and "bundle" not in page.text


def test_plain_markdown_json_and_feeds_keep_their_shape():
    plain = extract_page("line one\r\n    indented code\x07\n\n\n\n\nlast line   \n", content_type="text/plain")
    assert (plain.kind, plain.text) == ("text", "line one\n    indented code\n\n\nlast line")

    md = extract_page("# Title\n\nintro\n\n```python\n# not a heading\n```\n\n## Install\n\nsteps", content_type="text/markdown")
    assert [(lvl, title) for _, lvl, title in md.outline] == [(1, "Title"), (2, "Install")]
    assert md.full_text[md.outline[1][0]:].startswith("## Install")

    data = extract_page('{"ok": true, "n": [1, 2]}', content_type="application/json")
    assert data.kind == "json" and json.loads(data.text) == {"ok": True, "n": [1, 2]}
    assert extract_page("{not json", content_type="application/json").kind == "text"

    rss = extract_page(
        "<?xml version='1.0'?><rss><channel><title>Prices &amp; News</title>"
        "<item><title>Profit &amp; loss</title><link>https://example.com/?a=1&amp;b=2</link>"
        "<pubDate>Mon, 12 Oct 2026 09:00:00 GMT</pubDate><description>A &lt; B</description></item>"
        "<item><title><![CDATA[Second]]></title><description><![CDATA[<p>Big &amp; fast</p>]]></description></item>"
        "</channel></rss>", content_type="application/rss+xml")
    assert rss.text == ("# Prices & News\n- Profit & loss (Mon, 12 Oct 2026 09:00:00 GMT)\n  https://example.com/?a=1&b=2\n"
                        "  A < B\n- Second\n  Big & fast")
    atom = extract_page("<feed><title>Atom</title><entry><title>One</title><link rel='alternate' href='https://a.example/1'/>"
                        "<updated>2026-01-02</updated><summary>s</summary></entry></feed>", content_type="application/atom+xml")
    assert atom.text == "# Atom\n- One (2026-01-02)\n  https://a.example/1\n  s"


def test_paging_walks_the_whole_text_without_gaps():
    text = "".join(f"paragraph {i} " + "word " * 40 + "\n\n" for i in range(400))
    first = extract_page(text, content_type="text/plain")
    assert first.truncated and first.next_offset == FETCH_CHARS and first.total_chars == len(first.full_text)
    pieces, page = [first.text], first
    while page.next_offset is not None:
        page = extract_page(text, content_type="text/plain", offset=page.next_offset)
        pieces.append(page.text)
    assert "".join(pieces) == first.full_text
    past = extract_page(text, content_type="text/plain", offset=first.total_chars + 10)
    assert past.text == "" and past.next_offset is None and past.kind == "text"


def test_find_is_literal_and_reaches_past_the_first_window():
    text = "word " * 6000 + "NEEDLE-AT-END"
    found = extract_page(text, content_type="text/plain", find="NEEDLE-AT-END")
    assert found.used_find and "NEEDLE-AT-END" in found.text
    assert "No literal match for '(a+)+'" in extract_page("aaaaaaa", content_type="text/plain", find="(a+)+").text
    once = find_windows("# Topic\n\nneedle appears once and needle appears twice.", "needle")
    assert once.count("needle appears once") == 1 and once.count("# Topic") == 1


def test_find_returns_a_phrase_cut_by_the_passage_split_whole():
    phrase = "restore the replica"
    text = "a" * (PASSAGE_CHARS - len(phrase) // 2) + phrase + " and then more words. " + "tail " * 100
    assert phrase in find_windows(text, phrase)
    html = f"<html><body><article><p>{text}</p></article></body></html>"
    assert phrase in extract_page(html, url="https://example.test/a", find=phrase).text
    body = "a" * (PASSAGE_CHARS - 8) + " restore" + " the replica now"
    assert phrase in find_windows("# Heading\n\n" + body + "\n", phrase), "the space at the cut is kept"


def test_query_selects_the_relevant_section_with_an_offset_to_read_on():
    text = ("# Gardening\n\nTomatoes need sunlight and water.\n\n# Setup\n\n" + "Install the package. " * 40
            + "\n\n# Database recovery\n\nRestore the replica from a verified backup after corruption.")
    out = select_passages(text, query="how to restore database backup")
    assert "# Database recovery" in out and "verified backup" in out and "Tomatoes" not in out
    offset = int(out.split("[offset ", 1)[1].split("]", 1)[0])
    assert text[offset:].startswith("Restore the replica")

    pricing = "# Intro\n\nGeneral words about the product.\n\n# Pricing\n\nThe pro plan costs $20 per month."
    assert "$20 per month" in best_excerpt(pricing, "pro plan price per month", 500)
    assert best_excerpt(pricing, "zzz unrelated", 40).startswith("# Intro"), "no match falls back to the opening"
