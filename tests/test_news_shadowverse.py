import news_scraper as ns


def test_extract_article_strips_script_and_style():
    html = (
        "<html><head><style>@font-face{font-family:x}</style>"
        "<script>!function(){const t={adt_ei:1}}()</script></head><body>"
        "<h1>Godzilla Collab Dungeon</h1>"
        "<script>window.adthrive=1</script><style>:root{--a:oklch(1 1 1)}</style>"
        "<p>By Ann</p><p>November 5, 2026</p><p>Real body text.</p></body></html>"
    )
    a = ns.shadowverse_extract_article(html, "html", "https://shadowverse.gg/x/")
    assert a["title"] == "Godzilla Collab Dungeon"
    assert "adthrive" not in a["summary"]
    assert "font-face" not in a["summary"]
    assert "oklch" not in a["summary"]
    assert "Real body text." in a["summary"]
    assert a["published_ts"]


def test_extract_article_uses_post_metadata_not_related_cards():
    html = (
        '<html><head><meta property="article:published_time" content="2026-10-31T09:00:00+00:00">'
        '<meta name="author" content="Shadowverse.gg"></head><body>'
        "<h1>Victory Rewards</h1><time>October 31, 2026</time>"
        '<article class="post"><div class="entry-content">'
        '<div class="dotgg-toc-wrap">Contents Hide Show Notes</div>'
        '<p class="news-source-top">Source: Cygames</p>'
        "<p>Win matches to open <b>chests</b>.</p><h3>Notes</h3><ul><li>Resets daily.</li></ul>"
        '<p class="dbs-source">Read the full article on the official site</p></div>'
        "<h3>More Articles</h3><article>Godzilla Mini-Collab November 6, 2026</article>"
        "</article></body></html>"
    )
    a = ns.shadowverse_extract_article(html, "html", "https://shadowverse.gg/victory-rewards/")
    assert a["published"] == "2026-10-31T09:00:00+00:00"
    assert a["author"] == "Shadowverse.gg"
    assert a["summary"] == "Win matches to open chests.\nNotes\n• Resets daily."


def test_extract_article_mirror_text_is_trimmed():
    md = (
        "Title: x\nPublished Time: 2026-10-31T09:00:00+00:00\n\n"
        "[![ad](http://a/b.png)](http://ad)\n[Menu](http://m)\n\n"
        "# Victory Rewards\n\nContents Hide Show\n\nGet [free](http://f) gems.\n\n"
        "[Read the full article here](http://x)\n## More Articles\nJunk\n"
    )
    a = ns.shadowverse_extract_article(md, "text", "https://shadowverse.gg/x/")
    assert a["title"] == "Victory Rewards"
    assert a["summary"] == "Get free gems."
    assert a["published"].startswith("2026-10-31T09:00")
