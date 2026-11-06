from datetime import UTC, datetime, timedelta

from app.collection.dates import parse_absolute, parse_provider_date
from app.collection.text import content_hash, excerpt_around, find_terms, normalize_for_match
from app.collection.urls import canonicalize, domain_of, is_http_url


def test_canonicalize_and_domain():
    assert canonicalize("HTTP://WWW.ElWatan.dz:443/actu/article/?utm_source=x&b=2&a=1#top") == \
        "https://elwatan.dz/actu/article?a=1&b=2"
    assert canonicalize("https://tsa-algerie.com/") == "https://tsa-algerie.com/"
    assert canonicalize("https://x.dz/p?fbclid=abc") == canonicalize("http://www.x.dz/p")
    assert canonicalize("https://x.dz:8443/p") == "https://x.dz:8443/p"
    assert domain_of("https://www.Echoroukonline.com/x") == "echoroukonline.com"
    assert is_http_url("https://a.dz") and not is_http_url("javascript:alert(1)")
    assert not is_http_url("ftp://a.dz") and not is_http_url("https://")


def test_normalization_for_matching():
    assert normalize_for_match("  DJEZZY   Algérie ") == "djezzy algerie"
    assert normalize_for_match("جَازِي") == "جازي"            # diacritics removed
    assert normalize_for_match("إتصالات") == normalize_for_match("اتصالات")   # alef variants
    assert normalize_for_match("موبيليـــس") == "موبيليس"       # tatweel


def test_find_terms_whole_words_multiscript():
    text = "Le réseau de Djezzy est en panne depuis ce matin, شبكة جَازي ما تخدمش"
    assert find_terms(text, ["djezzy", "جازي", "Ooredoo"]) == ["djezzy", "جازي"]
    assert find_terms("Djezzyfan account", ["djezzy"]) == []          # not inside a word
    assert find_terms("Mobilis 4G  rapide", ["mobilis 4g"]) == ["mobilis 4g"]
    assert find_terms("الجازي", ["جازي"]) == []                       # attached article: no match
    assert find_terms("anything", ["", "  "]) == []


def test_excerpt_keeps_original_text():
    long = "x " * 400 + "Le service client de Djezzy est catastrophique" + " y" * 400
    ex = excerpt_around(long, ["DJEZZY"], width=80)
    assert "Djezzy" in ex and ex.startswith("…") and ex.endswith("…") and len(ex) < 120
    ar = "خدمة جَازي ممتازة"
    assert "جَازي" in excerpt_around(ar, ["جازي"])                  # original diacritics kept


def test_content_hash_ignores_case_and_spacing():
    assert content_hash("Titre", "Djezzy  lance") == content_hash("TITRE ", "djezzy lance")
    assert content_hash("a", "b") != content_hash("a", "c")


def test_dates():
    assert parse_absolute("Thu, 05 Nov 2026 10:00:00 +0100") == \
        datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert parse_absolute("2026-11-05T10:00:00Z") == datetime(2026, 11, 5, 10, tzinfo=UTC)
    assert parse_absolute("Nov 5, 2026") == datetime(2026, 11, 5, tzinfo=UTC)
    assert parse_absolute("not a date") is None
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    for raw, hours in [("3 hours ago", 3), ("il y a 3 heures", 3), ("قبل ٣ ساعات", 3),
                       ("2 days ago", 48), ("il y a 1 jour", 24)]:
        d, how = parse_provider_date(raw, now)
        assert how == "provider_relative" and now - d == timedelta(hours=hours), raw
    assert parse_provider_date("2026-11-01", now) == (datetime(2026, 11, 1, tzinfo=UTC), "provider")
    assert parse_provider_date("hier", now) == (None, None)


ARTICLE = """<!doctype html><html><head><title> Panne Djezzy | TSA </title>
<meta property="article:published_time" content="2026-11-05T08:30:00+01:00">
<link rel="alternate" type="application/rss+xml" href="/feed/">
<script>var x = "Djezzy hidden";</script><style>.a{}</style></head>
<body><nav>Menu</nav><article><h1>Panne géante</h1><p>Les abonnés de <b>Djezzy</b>
se plaignent.</p><p>Second paragraphe.</p></article></body></html>"""


def test_page_extraction():
    from app.collection.html import Page
    p = Page(ARTICLE, "https://www.tsa-algerie.com/panne")
    assert p.title == "Panne Djezzy | TSA"
    assert "Les abonnés de Djezzy se plaignent." in " ".join(p.text.split())
    assert "hidden" not in p.text and ".a{}" not in p.text
    assert p.feed_links == ["https://www.tsa-algerie.com/feed/"]
    assert p.published_at() == datetime(2026, 11, 5, 7, 30, tzinfo=UTC)


def test_page_jsonld_date_and_garbage():
    from app.collection.html import Page
    html = ('<script type="application/ld+json">{"@type":"NewsArticle",'
            '"datePublished":"2026-10-01T12:00:00Z"}</script><p>x</p>')
    assert Page(html, "https://a.dz").published_at() == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert Page("<<<>>> <p unclosed", "https://a.dz").published_at() is None


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>APS</title>
<item><title>Djezzy lance une offre</title><link>https://www.aps.dz/eco/1</link>
<description>&lt;p&gt;L'op&#233;rateur &lt;b&gt;Djezzy&lt;/b&gt; annonce&lt;/p&gt;</description>
<pubDate>Thu, 05 Nov 2026 10:00:00 +0100</pubDate><dc:creator>APS</dc:creator></item>
<item><title>Sans date</title><link>https://www.aps.dz/eco/2</link></item>
</channel></rss>"""

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>موبيليس تطلق عرضا</title><link rel="alternate" href="https://b.dz/1"/>
<published>2026-11-04T09:00:00Z</published><summary>عرض جديد من موبيليس</summary>
<author><name>Amine</name></author></entry></feed>""".encode()


def test_parse_rss_and_atom():
    import pytest

    from app.collection.feeds import FeedError, parse_feed
    title, items = parse_feed(RSS)
    assert title == "APS" and len(items) == 2
    assert items[0].summary == "L'opérateur Djezzy annonce"
    assert items[0].published_at == datetime(2026, 11, 5, 9, tzinfo=UTC)
    assert items[0].author == "APS" and items[1].published_at is None
    title, items = parse_feed(ATOM_FEED)
    assert items[0].title == "موبيليس تطلق عرضا" and items[0].link == "https://b.dz/1"
    with pytest.raises(FeedError):
        parse_feed(b"<html>not a feed</html>")
    with pytest.raises(FeedError):
        parse_feed(b"garbage")
