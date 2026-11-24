import copy
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from requests.structures import CaseInsensitiveDict

from radar import feeds


NOW = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)
ROOT = "https://press.example.test"
INDEX = ROOT + "/news.xml"
ROBOTS = ROOT + "/robots.txt"
NEWS = ROOT + "/news/"
COPY = "La empresa actualizó su programa de servicios para equipos profesionales. El informe describe los cambios para clientes y equipos de operaciones."
CATALOG = json.loads((Path(__file__).resolve().parents[1] / "config" / "sources.json").read_text(encoding="utf-8"))
SRC = {
    "id": "empresa", "domains": ["press.example.test"], "endpoint": INDEX,
    "org": "Empresa", "name": "Noticias", "fields": [3], "enabled": True, "kind": "rss",
    "article_path": "/news/[a-z0-9-]+", "body_selector": "article.story", "article_selector": "article.story",
    "policy": {"status": "reviewed", "scope": "rss", "terms_url": ROOT + "/terms",
               "reviewed_at": "2026-10-01", "review_until": "2027-01-01", "basis": "Autorización editorial de prueba."},
}


class Reply:
    def __init__(self, content=b"", mime="text/html", status=200, headers=None):
        self.content = content.encode() if isinstance(content, str) else content
        self.status_code = status
        self.headers = CaseInsensitiveDict({"Content-Type": mime, **(headers or {})})
        self.reads = 0
        self.closed = False

    def iter_content(self, step):
        self.reads += 1
        for pos in range(0, len(self.content), step):
            yield self.content[pos:pos + step]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


class Adapter:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def send(self, req, *, stream, timeout, verify, proxies):
        if not stream or not verify or proxies or req.method != "GET" or any(
                key in req.headers for key in ("Authorization", "Cookie", "Proxy-Authorization")):
            raise AssertionError("Unexpected transport options")
        self.calls.append(req.url)
        if req.url not in self.routes:
            raise AssertionError("Unexpected network request: " + req.url)
        response = self.routes[req.url]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class Clock:
    def __init__(self):
        self.now, self.waits = 0.0, []

    def sleep(self, duration):
        self.waits.append(duration)
        self.now += duration


def policy(text="User-agent: *\nAllow: /\n"):
    return Reply(text, "text/plain")


def rss(*entries):
    items = "".join(f"<item><link>{escape(link)}</link><title>{escape(title)}</title>"
                    f"<pubDate>{stamp}</pubDate></item>" for link, stamp, title in entries)
    return Reply("<rss><channel>" + items + "</channel></rss>", "application/rss+xml")


def article(head="", text=COPY, heading="Nota institucional", suffix=""):
    return Reply(f"<html><head><meta charset='utf-8'>{head}</head><body><h1>{heading}</h1>"
                 f"<article class='story'><p>{text}</p>{suffix}</article></body></html>")


def as_json(data):
    return Reply(json.dumps(data), "application/json")


def source(sid):
    return copy.deepcopy(next(s for s in CATALOG if s["id"] == sid))


class FeedsTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = feeds._State(lambda: self.clock.now, self.clock.sleep)

    def run_feed(self, routes, src=None, known=(), limit=4):
        self.adapter = Adapter(routes)
        with patch.object(feeds, "_STATE", self.state), patch.object(
                feeds.requests.adapters, "HTTPAdapter", return_value=self.adapter):
            return feeds.discover(SRC if src is None else src, NOW, set(known), limit)

    def test_seven_calendar_dates_sort_known_and_stop_at_limit(self):
        url = lambda s: NEWS + s
        feed = rss((url("old"), "2026-11-17", "Muy antigua"), (url("edge"), "2026-11-18", "Primera fecha"),
                   (url("known"), "2026-11-24", "Ya publicada"), (url("next"), "2026-11-25", "Mañana"),
                   (url("today"), "Tue, 24 Nov 2026 12:00:00 -0300", "Nota nueva"),
                   (url("unused"), "2026-11-18", "No visitada"))
        rows, warn = self.run_feed({ROBOTS: policy(), INDEX: feed, url("edge"): article(), url("today"): article()},
                                   known=[url("known") + "#old"], limit=2)
        self.assertEqual(warn, [])
        self.assertEqual([(r["url"], r["published_at"]) for r in rows],
                         [(url("today"), "2026-11-24"), (url("edge"), "2026-11-18")])
        self.assertEqual(set(rows[0]), {"source_id", "title", "body", "url", "published_at"})
        self.assertEqual(self.adapter.calls, [ROBOTS, INDEX, url("today"), url("edge")])

    def test_atom_published_not_updated_and_no_code_boilerplate_or_embedded_requests(self):
        atom = (f'<feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-11-24</updated>'
                f'<entry><title>Nueva nota</title><published>2026-11-23T10:00:00Z</published>'
                f'<link href="{NEWS}one"/><link rel="enclosure" href="https://outside.test/file"/></entry>'
                f'<entry><title>Vieja edición</title><updated>2026-11-24T10:00:00Z</updated>'
                f'<link href="{NEWS}two"/></entry></feed>')
        s = copy.deepcopy(SRC)
        s["kind"] = s["policy"]["scope"] = "atom"
        rows, warnings = self.run_feed({
            ROBOTS: policy(), INDEX: Reply(atom, "application/atom+xml"),
            NEWS+"one": article(suffix="<script src='https://outside.test/a'>neverScript</script>"
                                "<nav><p>neverNav</p></nav><code>neverCode</code><h2>About HP</h2>"
                                "<p>Standard boilerplate not part of the original editorial document.</p>"),
            NEWS+"two": article(head='<meta property="article:modified_time" content="2026-11-24">')
        }, s)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["published_at"], rows[0]["body"]), ("2026-11-23", COPY))
        self.assertTrue(warnings)

    def test_original_article_date_overrides_new_feed_stamp_and_malformed_date_is_not_invented(self):
        def meta(field, value):
            return f'<meta property="article:{field}" content="{value}">'
        rows, warnings = self.run_feed({
            ROBOTS: policy(), INDEX: rss(*[(NEWS+k, "2026-11-24", "Anuncio original") for k in ("a", "b", "c")]),
            NEWS+"a": article(head=meta("published_time", "2026-10-15") + meta("modified_time", "2026-11-24")),
            NEWS+"b": article(head=meta("published_time", "unknown")),
            NEWS+"c": article(head='<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle",'
                                   '"datePublished":"2026-11-20","dateModified":"2026-11-24"}]}</script>')})
        self.assertEqual([(r["url"], r["published_at"]) for r in rows], [(NEWS+"c", "2026-11-20")])
        self.assertTrue(warnings)

    def test_missing_restricted_expired_policy_or_disabled_never_opens_network(self):
        for change in ("absent", "scope", "status", "review_until", "enabled"):
            with self.subTest(change=change):
                s = copy.deepcopy(SRC)
                if change == "absent":
                    del s["policy"]
                elif change == "enabled":
                    s["enabled"] = False
                else:
                    s["policy"][change] = {"scope": "html", "status": "pending", "review_until": "2026-11-23"}[change]
                with self.assertRaises(feeds.SourceError):
                    self.run_feed({}, s)
                self.assertFalse(self.adapter.calls)

    def test_unavailable_or_unparseable_robots_closes_before_endpoint(self):
        for robots in (Reply(status=404), Reply(status=403), Reply(status=302, headers={"Location": ROOT+"/terms"}),
                       Reply("<html>Challenge</html>", "text/plain"), Reply("oops", "text/plain"),
                       policy("User-agent: *\nCrawl-delay: 90"), policy("User-agent: *\nRequest-rate: unknown"),
                       policy("User-agent: *\nDisallow: /news.xml")):
            with self.subTest(status=robots.status_code):
                self.setUp()
                with self.assertRaises(feeds.SourceError):
                    self.run_feed({ROBOTS: robots})
                self.assertEqual(self.adapter.calls, [ROBOTS])
                self.assertTrue(robots.closed)

    def test_robots_most_specific_agent_merged_groups_literal_query_and_longest_path(self):
        rules = ("User-agent: *\nDisallow: /\nUser-agent: Radar\nDisallow: /\n"
                 "User-agent: Radar9Bot\nDisallow: /news/\nCrawl-delay: 7\n"
                 "User-agent: RADAR9BOT\nAllow: /news/good$\nDisallow: /news/good$\n"
                 "Disallow: /literal?secret=*\nDisallow: /a*b$\n")
        robot = feeds._Robots(rules.encode())
        self.assertTrue(robot.allows(ROOT+"/literalXsecret=1"))
        self.assertFalse(robot.allows(ROOT+"/literal?secret=1"))
        self.assertFalse(robot.allows(ROOT+"/awordb"))
        self.assertTrue(robot.allows(ROOT+"/awordbother"))
        rows, warn = self.run_feed({ROBOTS: policy(rules), INDEX: rss(
            (NEWS+"blocked", "2026-11-24", "Restringida"), (NEWS+"good", "2026-11-24", "Permitida")),
            NEWS+"good": article()})
        self.assertEqual([r["url"] for r in rows], [NEWS+"good"])
        self.assertTrue(warn)
        self.assertEqual(self.clock.waits, [7, 7])

    def test_rate_limit_no_retry_or_read_cooldown_shared_by_host(self):
        blocked = Reply("private diagnostic", status=429, headers={"Retry-After": "8000"})
        with self.assertRaises(feeds.SourceError) as exc:
            self.run_feed({ROBOTS: policy(), INDEX: blocked})
        self.assertNotIn("private", str(exc.exception))
        self.assertEqual(blocked.reads, 0)
        s = {**SRC, "id": "otra"}
        with self.assertRaises(feeds.SourceError):
            self.run_feed({}, s)
        self.assertFalse(self.adapter.calls)

    def test_cadence_and_zero_limit_do_not_revisit(self):
        self.assertEqual(self.run_feed({}, limit=0), ([], []))
        self.assertEqual(self.run_feed({ROBOTS: policy(), INDEX: rss()}), ([], []))
        rows, warn = self.run_feed({})
        self.assertEqual(rows, [])
        self.assertTrue(warn)
        self.assertFalse(self.adapter.calls)

    def test_exact_https_before_requests_even_for_redirects_canonicals_and_known_aliases(self):
        targets = ["http://press.example.test/news/a", "https://name:pw@press.example.test/news/a",
                   ROOT+":444/news/a", "https://evil.press.example.test/news/a", "//other.test/news/a",
                   "https://press.example.test.evil.test/news/a", ROOT+"/news/%2e%2e/dir",
                   ROOT+"/news/a?token=private", ROOT+"/news/a%252foutside", "/news/../only"]
        for target in targets:
            with self.subTest(target=target):
                self.setUp()
                redirect = Reply("never read", status=302, headers={"Location": target})
                rows, warnings = self.run_feed({
                    ROBOTS: policy(), INDEX: rss((NEWS+"go", "2026-11-24", "Permalink")),
                    NEWS+"go": redirect})
                self.assertFalse(rows)
                self.assertTrue(warnings)
                self.assertEqual(redirect.reads, 0)
                self.assertEqual(self.adapter.calls, [ROBOTS, INDEX, NEWS+"go"])
        self.setUp()
        a = article(head=f'<link rel="canonical" href="{NEWS}known">')
        rows, _ = self.run_feed({ROBOTS: policy(), INDEX: rss((NEWS+"go", "2026-11-24", "Permalink")),
                                 NEWS+"go": a}, known=[NEWS+"known"])
        self.assertEqual(rows, [])
        self.assertNotIn(NEWS+"known", self.adapter.calls)

    def test_cross_origin_allowed_only_after_each_origins_own_robots(self):
        second = "https://other.example.test"
        s = {**SRC, "domains": SRC["domains"] + ["other.example.test"]}
        redirect = Reply(status=307, headers={"Location": second+"/news/next"})
        rows, warnings = self.run_feed({
            ROBOTS: policy(), INDEX: rss((NEWS+"a", "2026-11-24", "Actualidad")),
            NEWS+"a": redirect, second+"/robots.txt": policy("User-agent: RADAR9Bot\nDisallow: /news/")
        }, s)
        self.assertFalse(rows)
        self.assertTrue(warnings)
        self.assertEqual(self.adapter.calls[-1], second+"/robots.txt")

    def test_canonical_target_must_be_read_and_validated_before_publication(self):
        first = article(head=f'<link rel="canonical" href="{NEWS}b">', text="never publish the old body "*10)
        final = article(head='<meta property="article:published_time" content="2026-11-22">')
        rows, warns = self.run_feed({ROBOTS: policy(), INDEX: rss((NEWS+"a", "2026-11-24", "Actualidad")),
                                     NEWS+"a": first, NEWS+"b": final})
        self.assertFalse(warns)
        self.assertEqual([(r["url"], r["body"], r["published_at"]) for r in rows],
                         [(NEWS+"b", COPY, "2026-11-22")])

    def test_invalid_http_canonical_rejected_before_read_and_external_meta_never_fetched(self):
        link = article()
        link.headers["Link"] = '<https://other.test/leak>; rel="canonical"'
        for bad in (link, article(head='<meta property="og:url" content="https://other.test/leak">'),
                    article(head=f'<base href="{ROOT}/unrelated/">'),
                    article(head='<meta http-equiv="refresh" content="0; url=https://other.test/">'),
                    article(head='<meta name="robots" content="nosnippet">')):
            self.setUp()
            rows, warn = self.run_feed({ROBOTS: policy(), INDEX: rss((NEWS+"a", "2026-11-24", "Actualidad")),
                                        NEWS+"a": bad})
            self.assertFalse(rows)
            self.assertTrue(warn)
            self.assertEqual(len(self.adapter.calls), 3)
        self.assertEqual(link.reads, 0)

    def test_feed_channel_canonical_must_be_explicit_and_never_becomes_an_article(self):
        s = {**SRC, "channel_canonicals": [ROOT+"/"]}
        f = rss((NEWS+"new", "2026-11-24", "Una publicación"))
        f.headers["Link"] = f'<{ROOT}/>; rel="canonical"'
        rows, _ = self.run_feed({ROBOTS: policy(), INDEX: f, NEWS+"new": article()}, s)
        self.assertEqual([r["url"] for r in rows], [NEWS+"new"])
        self.assertNotIn(ROOT+"/", self.adapter.calls)
        self.setUp()
        with self.assertRaises(feeds.SourceError):
            self.run_feed({ROBOTS: policy(), INDEX: f})

    def test_size_mime_xml_status_and_deadlines_fail_closed(self):
        cases = (Reply("fake feed", "text/html"), Reply("<rss/>", "text/xml", status=206),
                 Reply("<rss/>", "text/xml", headers={"Content-Length": str(feeds._FEED_MAX+1)}),
                 Reply("<rss/>", "text/xml", headers={"Content-Encoding": "gzip"}),
                 Reply("<rss/>", "application/octet-stream"), Reply("<rss/>", "text/xml", headers={"Content-Length": "100"}))
        for i, r in enumerate(cases):
            with self.subTest(i=i):
                self.setUp()
                with self.assertRaises(feeds.SourceError):
                    self.run_feed({ROBOTS: policy(), INDEX: r})
                if i < 5:
                    self.assertEqual(r.reads, 0)
        for xml in ("<!DOCTYPE rss [<!ENTITY local 'x'>]><rss><channel/></rss>",
                    "<!DOCTYPE rss SYSTEM 'https://outside.test/entity'><rss/>", "<html>not a feed</html>", "<feed"):
            self.setUp()
            with self.assertRaises(feeds.SourceError):
                self.run_feed({ROBOTS: policy(), INDEX: Reply(xml, "application/xml")})
            self.assertEqual(self.adapter.calls, [ROBOTS, INDEX])

    def test_chunked_budget_without_content_length(self):
        with patch.object(feeds, "_FEED_MAX", 48), self.assertRaises(feeds.SourceError):
            self.run_feed({ROBOTS: policy(), INDEX: Reply(b"x"*64, "text/xml")})

    def test_landings_lists_feeds_and_offensive_items_are_not_published(self):
        f = rss((INDEX, "2026-11-24", "Canal"), (NEWS+"tag/list", "2026-11-24", "Tema"),
                (NEWS+"bad", "2026-11-24", "Vulnerability details"),
                (NEWS+"list", "2026-11-24", "Listado"), (NEWS+"unsafe", "2026-11-24", "Actualidad"))
        rows, warn = self.run_feed({
            ROBOTS: policy(), INDEX: f, NEWS+"list": Reply("<main><h1>Noticias recientes</h1><p>"+COPY+"</p></main>"),
            NEWS+"unsafe": article(text=COPY+" Exploitation details withheld.")})
        self.assertFalse(rows)
        self.assertTrue(warn)
        self.assertNotIn(NEWS+"bad", self.adapter.calls)

    def test_html_index_scoped_cards_and_permitted_permalinks_only(self):
        s = copy.deepcopy(SRC)
        s.update(kind="html", endpoint=ROOT+"/listing", card_selector=".news-card", link_selector="a.title",
                 index_date_selector="time[datetime]")
        s["policy"]["scope"] = "html"
        index = Reply("<a href='/news/unrelated'>Comunicación</a><div class='news-card'>"
                      "<a class='title' href='/news/one'>Actualidad empresarial</a><time datetime='2026-11-23'></time></div>"
                      "<div class='news-card'><a class='title' href='https://third.test/'>External</a></div>")
        rows, warn = self.run_feed({ROBOTS: policy(), s["endpoint"]: index, NEWS+"one": article()}, s)
        self.assertFalse(warn)
        self.assertEqual([(r["url"], r["published_at"]) for r in rows], [(NEWS+"one", "2026-11-23")])
        self.setUp()
        with self.assertRaises(feeds.SourceError):
            self.run_feed({ROBOTS: policy(), s["endpoint"]: Reply("<h1>Challenge</h1>")}, s)

    def test_edgar_contacts_and_issuer_exhibit_not_hpe_or_filing_date(self):
        s = source("hp-inc-edgar")
        with patch.dict(os.environ, {"RADAR9_SEC_CONTACT": ""}), self.assertRaises(feeds.SourceError):
            self.run_feed({}, s)
        sec = "https://www.sec.gov"
        no = "0000047217-26-000012"
        folder = sec+"/Archives/edgar/data/47217/"+no.replace("-", "")+"/"
        idx = sec+"/Archives/edgar/daily-index/2026/QTR4/master.20261124.idx"
        rows = ("CIK|Company Name|Form Type|Date Filed|File Name\n"
                "1645590|HEWLETT PACKARD ENTERPRISE CO|8-K|20261124|edgar/data/1645590/no.txt\n"
                f"47217|HP INC|8-K|20261124|edgar/data/47217/{no}.txt\n")
        filing = Reply(f"<table class='tableFile'><tr><td>1</td><td>Release</td><td><a href='{folder}result.htm'>"
                       "result</a></td><td>EX-99.1</td></tr></table>")
        press = Reply("<html><body>HP Inc. Reports Fiscal 2026 Fourth Quarter Results. "
                      "PALO ALTO, Calif. – Nov. 23, 2026. "+
                      COPY+" Personal Systems net revenue and Printing net revenue are discussed in this report.</body></html>")
        with patch.dict(os.environ, {"RADAR9_SEC_CONTACT": "unit@example.test"}):
            result, warning = self.run_feed({sec+"/robots.txt": policy(), idx: Reply(rows, "application/octet-stream"),
                                           folder+no+"-index.htm": filing, folder+"result.htm": press}, s, limit=1)
        self.assertFalse(warning)
        self.assertEqual([(r["url"], r["published_at"]) for r in result], [(folder+"result.htm", "2026-11-23")])
        self.assertIn("Personal Systems", result[0]["body"])
        self.assertIn("Printing", result[0]["body"])

    def test_epa_singleton_matches_index_publication_not_launch_or_metadata_update(self):
        s = source("lenovo-certified-pcs")
        base = "https://data.energystar.gov"
        rec = {"pd_id": "4714374", "brand_name": "Lenovo", "date_certified": "2026-11-21T00:00:00",
               ":created_at": "2026-11-22T13:00:00Z"}
        detail = base+"/resource/rxdj-2c88/4714374.json"
        foreign = {**rec, "pd_id": "1234567", "brand_name": "Other"}
        old = {**rec, "pd_id": "2222222", "date_certified": "2025-01-01", ":updated_at": "2026-11-24"}
        data = {base+"/robots.txt": policy(), s["endpoint"]: as_json([foreign, old, rec]),
                detail: as_json({**rec, "model_number": "PC-24", "model_name": "Estación", "markets": "Canada"})}
        rows, warn = self.run_feed(data, s)
        self.assertFalse(warn)
        self.assertEqual([(r["url"], r["published_at"]) for r in rows], [(detail, "2026-11-22")])
        self.assertIn("2026-11-21", rows[0]["body"])
        self.setUp()
        data[detail] = as_json([{**rec, "model_number": "PC-24"}])
        rows, warn = self.run_feed(data, s)
        self.assertFalse(rows, "Una lista no es un registro de producto individual.")
        self.assertTrue(warn)
        self.setUp()
        rows, _ = self.run_feed(data, s, known=[detail])
        self.assertFalse(rows)
        self.assertNotIn(detail, self.adapter.calls)

    def test_source_failures_do_not_leak_remote_diagnostics(self):
        with self.assertRaises(feeds.SourceError) as exc:
            self.run_feed({ROBOTS: feeds.requests.ConnectionError("sensitive query and response text")})
        self.assertNotIn("sensitive", str(exc.exception))

    def test_hp_printing_uses_the_legacy_qualification_date_at_both_ends(self):
        s = source("hp-certified-printing")
        self.assertIn("date_qualified", s["endpoint"])
        self.assertNotIn("date_certified", s["endpoint"])
        base = "https://data.energystar.gov"
        detail = base+"/resource/t2v6-g4nf/4514259.json"
        rec = {"pd_id": "4514259", "brand_name": "HP", "date_qualified": "2026-11-23T00:00:00",
               ":created_at": "2026-11-24T14:00:00Z"}
        product = {"pd_id": rec["pd_id"], "brand_name": "HP", "date_qualified": rec["date_qualified"],
                   "model_name": "Color LaserJet Enterprise", "model_number": "X778dn"}
        rows, warnings = self.run_feed({base+"/robots.txt": policy(), s["endpoint"]: as_json([rec]),
                                       detail: as_json(product)}, s)
        self.assertFalse(warnings)
        self.assertEqual([r["published_at"] for r in rows], ["2026-11-24"])
        self.assertIn("2026-11-23", rows[0]["body"])
        self.assertEqual(rows[0]["url"], detail)

    def test_preserves_verified_item_on_later_restriction_without_retry(self):
        blocked = Reply("Private server explanation", status=403)
        rows, warnings = self.run_feed({
            ROBOTS: policy(), INDEX: rss((NEWS+"ok", "2026-11-24", "Publicación uno"),
                                         (NEWS+"denied", "2026-11-23", "Publicación dos")),
            NEWS+"ok": article(), NEWS+"denied": blocked})
        self.assertEqual([r["url"] for r in rows], [NEWS+"ok"])
        self.assertTrue(warnings)
        self.assertNotIn("Private", str(warnings))
        self.assertEqual(blocked.reads, 0)

    def test_active_catalog_all_nine_fields_reviewed_exact_hosts_and_alternatives(self):
        active = [s for s in CATALOG if s["enabled"]]
        self.assertEqual({i for s in active for i in s["fields"]}, set(range(1, 10)))
        self.assertEqual(len({s["id"] for s in CATALOG}), len(CATALOG))
        for s in active:
            with self.subTest(source=s["id"]):
                self.assertTrue(feeds._valid_config(s, NOW))
                self.assertNotIn("hpe.com", s["domains"])
        for sid in ("lenovo", "hp-investor", "dell-investor"):
            self.assertTrue(any(s["id"] in source(sid)["alternative_ids"] for s in active))


if __name__ == "__main__":
    unittest.main()
