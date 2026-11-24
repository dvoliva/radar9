"""Bounded, anonymous reads of the explicitly reviewed publishers in sources.json."""

from __future__ import annotations

import fnmatch
import ipaddress
import json
import math
import os
import re
import threading
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


class SourceError(Exception):
    """Source unavailable; messages are safe to put in the edition log."""


class _Skip(Exception):
    """One non-publishable item; never include remote content in the message."""


class _Known(_Skip):
    pass


class _Missing(_Skip):
    pass


class _Budget(Exception):
    pass


BOT = "RADAR9Bot"
_HTML = {"text/html", "application/xhtml+xml"}
_XML = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
_REDIRECT = {301, 302, 303, 307, 308}
_ROBOTS_MAX, _FEED_MAX, _PAGE_MAX = 256_000, 2_000_000, 1_350_000
_HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_UNSAFE = re.compile(
    r"\b(?:exploit(?:s|ation|ing|able)?|zero[- ]day|0day|proof[- ]of[- ]concept|"
    r"prueba de concepto|payloads?|shellcode|malware|ransomware|bypass(?:ing)?|"
    r"reverse shell|command injection|privilege escalation|penetration test(?:ing)?|"
    r"vulnerabilit(?:y|ies)|vulnerabilidad(?:es)?|credential theft|SQL injection|"
    r"remote code execution|ejecuci[oó]n remota|escalada de privilegios|CVE-\d{4}-\d+)\b", re.I,
)
_RESTRICTED = re.compile(r"\b(?:none|noindex|nofollow|nosnippet|noarchive|noai|noimageai)\b|max-snippet:\s*0\b", re.I)
_BOILER = re.compile(
    r"^(?:forward[- ]looking statements?|safe harbor|about (?:hp|dell|lenovo)\b|"
    r"media contacts?\b|investor contacts?\b|subscribe\b|sign up\b|all rights reserved|"
    r"share this\b|related (?:articles|posts|stories)\b|copyright \d|contacto de prensa)", re.I,
)
_JUNK = (
    "script,style,noscript,template,svg,math,iframe,object,embed,form,button,nav,"
    "footer,aside,pre,code,figure,[hidden],[aria-hidden=true],.share,.social-share,"
    ".newsletter,.related,.related-posts,.post-navigation,.screen-reader-text"
)


def _host_ok(host):
    if not isinstance(host, str) or not _HOST.fullmatch(host) or host.endswith((".local", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def _url(raw, hosts, base=None):
    invalid = _Skip("URL fuera de la política de la fuente.")
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", raw):
        raise invalid
    if re.search(r"%(?![a-fA-F0-9]{2})|%(?:2f|5c|25|3f|23|0[0-9a-f]|1[0-9a-f]|7f)", raw, re.I):
        raise invalid
    try:
        original = urlsplit(raw)
        if any(p in {".", ".."} for p in unquote(original.path).split("/")):
            raise invalid
        if raw.startswith("///") or (original.scheme and not original.netloc):
            raise invalid
        parts = urlsplit(urljoin(base, raw) if base else raw)
        host = parts.hostname
        if (parts.scheme != "https" or host not in hosts or parts.username is not None or
                parts.password is not None or parts.port not in (None, 443) or
                parts.netloc.lower() not in (host, f"{host}:443")):
            raise invalid
        if re.search(r"(?:^|&)(?:api[_-]?key|token|access_token|password|signature|credential)=", unquote(parts.query), re.I):
            raise invalid
        return requests.utils.requote_uri(urlunsplit(("https", host, parts.path or "/", parts.query, "")))
    except (ValueError, UnicodeError, requests.RequestException):
        raise invalid from None


def _display(text):
    text = re.sub(r"<[!/?a-zA-Z][^<>]{0,1024}>", " ", text).replace("<", "‹").replace(">", "›")
    return " ".join("".join(c if unicodedata.category(c) not in {"Cc", "Cf", "Cs"} else " " for c in text).split())


def _text(node):
    return _display(node.get_text(" ", strip=True)) if node else ""


def _date(value):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", value):
            return date.fromisoformat(value)
        if re.match(r"^\d{4}-\d\d-\d\d[T ]\d\d:", value):
            return datetime.fromisoformat(value).date()
        if re.fullmatch(r"(?:[A-Za-z]{3},?\s+)?\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\s+\d\d:\d\d(?::\d\d)?\s+(?:[+-]\d{4}|[A-Z]{1,4})", value):
            return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _rep_text(value):
    # RFC 9309 compares percent-encoded reserved octets as encoded, unreserved as text.
    return requests.utils.requote_uri(re.sub(r"%[0-9a-fA-F]{2}", lambda m: m[0].upper(), value))


class _Robots:
    def __init__(self, data):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            raise SourceError("robots.txt no es UTF-8 válido.") from None
        if "<" in text.lstrip()[:1] or "\0" in text:
            raise SourceError("robots.txt ilegible.")
        groups, agents, rules, global_delay = [], [], [], []
        for raw in text.splitlines():
            line = raw.partition("#")[0].strip()
            if not line:
                continue
            key, colon, value = line.partition(":")
            key, value = key.lower().strip(), value.strip()
            if not colon:
                raise SourceError("Directiva robots.txt ilegible.")
            if key == "user-agent":
                if not value:
                    raise SourceError("Grupo robots.txt ilegible.")
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif key in {"allow", "disallow", "crawl-delay", "request-rate", "visit-time", "content-signal"}:
                if not agents:
                    if key in {"crawl-delay", "request-rate"}:
                        global_delay.append((key, value))
                    else:
                        raise SourceError("Directiva robots.txt sin grupo.")
                else:
                    rules.append((key, value))
        if agents:
            groups.append((agents, rules))
        if not groups:
            raise SourceError("robots.txt sin grupos verificables.")
        relevant = [(max((len(a) for a in names if a != "*" and a in BOT.lower()), default=0)
                     if "*" in names or any(a in BOT.lower() for a in names) else -1, entries)
                    for names, entries in groups]
        best = max(score for score, _ in relevant)
        self.delay = 1.0
        self.patterns = []
        for key, value in global_delay + [r for s, entries in relevant if s == best and s >= 0 for r in entries]:
            if key in {"allow", "disallow"} and value:
                if not value.startswith(("/", "*")) or len(value) > 1200 or value.count("*") > 16:
                    raise SourceError("Patrón robots.txt no compatible.")
                pattern = _rep_text(value.removesuffix("$"))
                # Atomic intermediate segments avoid backtracking on long wildcard rules.
                literal = pattern.replace("[", "[[]").replace("?", "[?]")
                matcher = fnmatch.translate(literal + ("" if value.endswith("$") else "*"))
                self.patterns.append((len(pattern.replace("*", "").encode()), key == "allow", re.compile(matcher)))
            elif key in {"crawl-delay", "request-rate"}:
                if key == "request-rate":
                    rate = re.fullmatch(r"(\d{1,8})/(\d{1,8})", value)
                    if not rate or min(map(int, rate.groups())) < 1:
                        raise SourceError("Request-rate no compatible.")
                    seconds = int(rate[2]) / int(rate[1])
                else:
                    if not re.fullmatch(r"\d{1,8}(?:\.\d{1,5})?", value):
                        raise SourceError("Crawl-delay no compatible.")
                    seconds = float(value)
                self.delay = max(self.delay, seconds)
            elif key in {"visit-time", "content-signal"} and value:
                raise SourceError("Restricción robots.txt requiere revisión.")
        if self.delay > 20:
            raise SourceError("La cadencia de robots.txt excede el presupuesto; no se consulta.")

    def allows(self, url):
        p = urlsplit(url)
        target = _rep_text(p.path + ("?" + p.query if p.query else ""))
        return max(((size, allow) for size, allow, rx in self.patterns if rx.match(target)),
                   default=(-1, True))[1]


class _State:
    """Per-process politeness shared between sources on the same exact origin."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.last, self.blocked, self.robots, self.polls, self.host_locks, self.robot_locks = {}, {}, {}, {}, {}, {}
        self.sec_indices = {}

    def poll(self, key, interval):
        with self.lock:
            now = self.clock()
            if now - self.polls.get(key, -math.inf) < interval:
                return False
            self.polls[key] = now
            return True

    def pace(self, host, delay):
        with self.lock:
            lock = self.host_locks.setdefault(host, threading.Lock())
        with lock:
            now = self.clock()
            if now < self.blocked.get(host, -math.inf):
                raise SourceError("Servidor en pausa; no se reintenta.")
            wait = self.last.get(host, -math.inf) + delay - now
            if wait > 0:
                self.sleep(wait)
            self.last[host] = self.clock()


_STATE = _State()


class _Client:
    def __init__(self, hosts, agent, state, delay, adapter):
        self.hosts, self.agent, self.state, self.delay = hosts, agent, state, delay
        self.adapter, self.budget = adapter, 25

    def _open(self, url, delay):
        if self.budget <= 0:
            raise _Budget()
        self.budget -= 1
        self.state.pace(urlsplit(url).hostname, max(self.delay, delay))
        # Use the adapter directly: even Session.get(allow_redirects=False) prepares
        # a next request, consuming an unchecked redirect body. No cookie jar/netrc.
        req = requests.Request("GET", url, headers={"User-Agent": self.agent, "Accept-Encoding": "identity"}).prepare()
        return self.adapter.send(req, stream=True, timeout=(4, 8), verify=True, proxies={})

    def _read(self, response, types, cap):
        if response.headers.get("Content-Type", "").split(";")[0].strip().lower() not in types:
            raise _Skip("Tipo de contenido inesperado.")
        if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
            raise _Skip("Codificación de transporte no admitida.")
        length = response.headers.get("Content-Length")
        if length is not None and (not re.fullmatch(r"\d{1,12}", length) or int(length) > cap):
            raise _Skip("Documento demasiado grande o longitud inválida.")
        result = bytearray()
        start = self.state.clock()
        for chunk in response.iter_content(8192):
            if len(result) + len(chunk) > cap or self.state.clock() - start > 20:
                raise _Skip("Documento excede el límite de lectura.")
            result.extend(chunk)
        if length is not None and len(result) != int(length):
            raise _Skip("Documento incompleto.")
        return bytes(result)

    def _status(self, r, host):
        if r.status_code in (401, 403, 429, 451, 503):
            seconds = 3600
            retry = r.headers.get("Retry-After", "").strip()
            if re.fullmatch(r"\d{1,12}", retry):
                seconds = max(seconds, int(retry))
            elif retry:
                try:
                    seconds = max(seconds, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
            self.state.blocked[host] = self.state.clock() + seconds
            raise SourceError("Servidor restringido o no disponible; no se reintenta.")
        if r.status_code in (404, 410):
            raise _Missing("Documento no disponible (HTTP).")
        if r.status_code != 200:
            raise _Skip("Documento no disponible (HTTP).")

    def policy(self, url):
        host = urlsplit(url).hostname
        with self.state.lock:
            lock = self.state.robot_locks.setdefault(host, threading.Lock())
        with lock:
            robots = self._robots_for(host)
        if not robots.allows(url):
            raise _Skip("Ruta excluida por robots.txt.")
        return robots.delay

    def _robots_for(self, host):
        cached = self.state.robots.get(host)
        if not cached or self.state.clock() >= cached[0]:
            # Do not follow redirects for robots: absence of this origin's own
            # readable policy (including 404, challenges and malformed files) is a denial.
            try:
                with self._open(f"https://{host}/robots.txt", 1) as r:
                    self._status(r, host)
                    data = self._read(r, {"text/plain"}, _ROBOTS_MAX)
                robots = _Robots(data)
            except _Skip:
                self.state.blocked[host] = self.state.clock() + 3600
                raise SourceError("robots.txt no verificable; fuente cerrada.") from None
            self.state.robots[host] = (self.state.clock() + 3600, robots)
        else:
            robots = cached[1]
        return robots

    def fetch(self, start, types, cap, guard, index_representations=()):
        url, seen = start, set()
        for _ in range(4):
            url = _url(url, self.hosts)
            guard(url)
            if url in seen:
                break
            seen.add(url)
            delay = self.policy(url)
            with self._open(url, delay) as r:
                if r.status_code in _REDIRECT:
                    url = _url(r.headers.get("Location"), self.hosts, url)
                    continue
                self._status(r, urlsplit(url).hostname)
                if _RESTRICTED.search(r.headers.get("X-Robots-Tag", "")):
                    raise _Skip("El editor restringe la indexación o los extractos.")
                link = r.headers.get("Link", "")
                canon = []
                for item in requests.utils.parse_header_links(link):
                    if "canonical" in item.get("rel", "").lower().split():
                        canon.append(_url(item.get("url"), self.hosts, url))
                if "canonical" in link.lower() and not canon:
                    raise _Skip("Cabecera canonical ilegible.")
                if len(set(canon)) > 1:
                    raise _Skip("Canonical ambiguo.")
                if canon and canon[0] != url:
                    if canon[0] in index_representations:
                        # Some publishers identify a *channel's* website as its
                        # canonical. Check policy but never collect or publish it.
                        self.policy(canon[0])
                    else:
                        url = canon[0]
                        continue
                return url, self._read(r, types, cap)
        raise _Skip("Demasiadas redirecciones o ciclo.")


def _page(client, url, guard):
    seen = set()
    for _ in range(3):
        final, data = client.fetch(url, _HTML, _PAGE_MAX, guard)
        if final in seen:
            break
        seen.add(final)
        page = BeautifulSoup(data, "html.parser")
        if page.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)}):
            raise _Skip("Página con redirección no HTTP.")
        for meta in page.find_all("meta"):
            if str(meta.get("name", "")).lower() in {"robots", BOT.lower()} and _RESTRICTED.search(str(meta.get("content", ""))):
                raise _Skip("El editor restringe la indexación o los extractos.")
        base = page.find("base", href=True)
        base_url = _url(base["href"], client.hosts, final) if base else final
        if base and base_url != final:
            raise _Skip("La página cambia la base de sus enlaces.")
        canon = [_url(t.get("href"), client.hosts, base_url) for t in page.find_all("link")
                 if "canonical" in [x.lower() for x in t.get("rel", [])]]
        canon += [_url(t.get("content"), client.hosts, base_url) for t in page.select('meta[property="og:url"]')]
        if len(set(canon)) > 1:
            raise _Skip("Canonical ambiguo.")
        if not canon or canon[0] == final:
            return final, page
        if canon[0] in seen:
            break
        url = canon[0]  # guard, robots, status and MIME are checked before this page is used
    raise _Skip("Canonical circular o demasiado profundo.")


@dataclass
class _Hint:
    url: str
    title: str = ""
    day: date | None = None


def _feed(data):
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ElementTree.ParseError, DefusedXmlException, ValueError, LookupError):
        raise SourceError("Feed XML inválido o no seguro.") from None
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == "rss" and root.find("channel") is not None:
        entries, is_atom = root.findall("channel/item"), False
    elif root.tag == atom + "feed":
        entries, is_atom = root.findall(atom + "entry"), True
    else:
        raise SourceError("El endpoint no es un feed RSS/Atom.")
    for item in entries[:150]:
        if is_atom:
            links = [n.get("href", "") for n in item.findall(atom + "link")
                     if n.get("rel", "alternate") == "alternate" and n.get("type", "text/html") in _HTML]
            href = links[0] if len(set(links)) == 1 else ""
            title, stamp = item.findtext(atom + "title"), item.findtext(atom + "published")
        else:
            href, title = item.findtext("link"), item.findtext("title")
            stamp = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        # Absolute permalink only: no XML base, GUID fallback, updated time or feed body.
        if href and href.startswith("https://"):
            yield _Hint(href.strip(), _display(title or ""), _date(stamp))


def _index_hints(page, source, base):
    for card in page.select(source["card_selector"], limit=100):
        a = card.select_one(source["link_selector"])
        if a and a.get("href") and "nofollow" not in a.get("rel", []):
            stamp = card.select_one(source["index_date_selector"]) if source.get("index_date_selector") else None
            try:
                link = _url(a["href"], source["domains"], base)
            except _Skip:
                continue
            yield _Hint(link, _text(a), _date(stamp.get("datetime")) if stamp else None)


def _json_dates(page):
    for tag in page.select('script[type="application/ld+json"]')[:8]:
        raw = tag.string
        if not raw or len(raw) > 64000:
            continue
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        nodes = value if isinstance(value, list) else [value]
        for obj in nodes[:40]:
            if isinstance(obj, dict):
                graph = obj.get("@graph")
                for node in [obj] + (graph[:40] if isinstance(graph, list) else []):
                    if not isinstance(node, dict):
                        continue
                    types = node.get("@type")
                    types = types if isinstance(types, list) else [types]
                    if any(t in ("NewsArticle", "Article", "BlogPosting", "TechArticle") for t in types):
                        if "datePublished" in node:
                            yield node["datePublished"]


def _original_day(page, selector):
    stamps = [n.get("content") for n in page.select('meta[property="article:published_time"],meta[itemprop="datePublished"]')]
    stamps += [n.get("datetime") or n.get("content") for n in page.select('time[itemprop="datePublished"]')]
    if selector:
        stamps += [n.get("datetime") or n.get("content") for n in page.select(selector)[:2]]
    stamps.extend(_json_dates(page))
    days = {_date(s) for s in stamps}
    if None in days or len(days) > 1:
        raise _Skip("Fecha original inválida o ambigua.")
    return next(iter(days), None)


def _body(root):
    for t in root.select(_JUNK):
        t.decompose()
    parts = []
    for n in root.select("p,li,h2,h3,h4"):
        if n.find_parent(["p", "li"]) is not None:
            continue
        text = _text(n)
        if _BOILER.search(text):
            if parts:
                break
            continue
        if n.name in {"p", "li"} and len(text) >= 30:
            parts.append(text)
    return "\n".join(parts)


def _article(source, page, hint, final):
    root = page.select_one(source["body_selector"])
    marker = page.select_one(source["article_selector"])
    if root is None or marker is None or len(page.select(source["body_selector"], limit=2)) != 1:
        raise _Skip("La página no es un artículo individual.")
    heading = page.select_one("main h1, article h1, h1")
    alternative = page.select_one('meta[property="og:title"][content]')
    title = _text(heading) or (_display(alternative["content"]) if alternative else "")
    if not title or re.fullmatch(r"(?:latest )?(?:news|newsroom|press releases|blog|posts|noticias|comunicados)(?: de prensa)?", title, re.I):
        raise _Skip("No se reconoce el titular de un detalle editorial.")
    day = _original_day(page, source.get("published_selector"))
    if day is None and final == hint.url:
        day = hint.day
    if not day:
        raise _Skip("Sin fecha de publicación original verificable.")
    return title, day, _body(root)


_SEC = "https://www.sec.gov/Archives/edgar/"
_MONTHS = {s: i for i, s in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_ISSUERS = {
    "47217": (r"HP INC\.?", r"HP Inc\.? Reports (?:Fiscal |First |Second |Third |Fourth )",
              r"PALO ALTO,\s*(?:Calif(?:ornia)?\.?|CA)", ("Personal Systems", "Printing")),
    "1571996": (r"Dell Technologies Inc\.?", r"Dell Technologies (?:Reports|Delivers|Announces) ",
                r"ROUND ROCK,\s*(?:Texas|TX)", ("Client Solutions Group", "Infrastructure Solutions Group")),
}


def _exact(url):
    def check(other):
        if other != url:
            raise _Skip("Destino distinto al endpoint autorizado.")
    return check


def _sec_hints(client, now, known, cik):
    """Read a bounded daily index, never enumerate companies, filing folders or exhibits."""
    usable_index = False
    for offset in range(7):
        day = now.date() - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        stamp = day.strftime("%Y%m%d")
        url = f"{_SEC}daily-index/{day.year}/QTR{(day.month-1)//3+1}/master.{stamp}.idx"
        cached = client.state.sec_indices.get(url)
        if cached and client.state.clock() < cached[0]:
            lines = cached[1]
        else:
            try:
                _, data = client.fetch(url, {"application/octet-stream", "text/plain"}, 3_000_000, _exact(url))
            except _Missing:
                continue
            if b"CIK|Company Name|Form Type|Date Filed|File Name" not in data[:1800]:
                raise SourceError("Índice EDGAR no reconocido.")
            lines = [line for line in data.decode("latin-1").splitlines() if line.partition("|")[0] in _ISSUERS]
            if len(client.state.sec_indices) >= 10:
                client.state.sec_indices.pop(next(iter(client.state.sec_indices)))
            client.state.sec_indices[url] = (client.state.clock() + 3600, lines)
        usable_index = True
        for line in lines:
            if not line.startswith(cik + "|"):
                continue
            row = line.split("|")
            if (len(row) != 5 or not re.fullmatch(_ISSUERS[cik][0], row[1], re.I) or
                    row[2] != "8-K" or row[3].replace("-", "") != stamp):
                continue
            accession = re.fullmatch(rf"edgar/data/{cik}/(\d{{10}}-\d{{2}}-\d{{6}})\.txt", row[4])
            if not accession:
                continue
            number = accession[1]
            directory = f"{_SEC}data/{cik}/{number.replace('-', '')}/"
            if any(u.startswith(directory) for u in known):
                continue
            detail = f"{directory}{number}-index.htm"
            _, index = _page(client, detail, _exact(detail))
            for tr in index.select("table.tableFile tr"):
                cells = tr.find_all("td", recursive=False)
                if len(cells) >= 4 and _text(cells[3]).upper() == "EX-99.1":
                    a = cells[2].find("a", href=True)
                    if a:
                        link = _url(a["href"], client.hosts, detail)
                        if link.startswith(directory):
                            yield _Hint(link)
                    break
    if not usable_index:
        raise SourceError("No hay índices EDGAR utilizables en la ventana.")


def _sec_article(page, cik):
    for t in page.select(_JUNK):
        t.decompose()
    root = page.body
    text = _text(root)
    head = text[:12000]
    _, heading, city, segments = _ISSUERS[cik]
    title = re.search(r"\b" + heading + r"[^|]{4,125}?(?:Results|Earnings)\b", head, re.I)
    dateline = re.search(city + r"\s*[,–—-]?\s*"
                         r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(20\d{2})", head, re.I)
    if not title or not dateline or dateline[1][:3].lower() not in _MONTHS:
        raise _Skip("El anexo no es un comunicado de resultados del emisor con fecha original.")
    try:
        day = date(int(dateline[3]), _MONTHS[dateline[1][:3].lower()], int(dateline[2]))
    except ValueError:
        raise _Skip("Fecha de comunicado inválida.") from None
    # EDGAR exhibits often use positioned divs/spans instead of paragraphs. Take
    # the issuer dateline and segment highlights, not a full reproduction/tables.
    body = text[dateline.start():dateline.start()+1200]
    for segment in segments:
        match = re.search(r"\b" + segment + r"\s+(?:net\s+)?revenue\b", text, re.I)
        if match:
            body += "\n" + text[match.start():match.start()+350]
    return title[0], day, body


_EPA = "https://data.energystar.gov/resource/"
_EPA_DATASETS = {"rxdj-2c88", "t2v6-g4nf"}


def _json(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise _Skip("Documento JSON inválido.") from None


def _epa(client, source, endpoint, now, known, limit):
    """One filtered API call and singleton records, NOT a product-launch or sales feed."""
    dataset, brand = source["dataset"], source["brand"]
    date_key = "date_qualified" if dataset == "t2v6-g4nf" else "date_certified"
    prefix = _EPA + dataset + "/"
    _, data = client.fetch(endpoint, {"application/json"}, 120000, _exact(endpoint))
    items = _json(data)
    if not isinstance(items, list) or len(items) > 24:
        raise SourceError("El índice de certificaciones no respeta el formato o límite acordado.")
    out, warnings = [], []
    seen = set(known)
    start, end = now.date() - timedelta(days=6), now.date()
    items = [r for r in items if isinstance(r, dict)]
    for row in sorted(items, key=lambda r: _date(r.get(":created_at")) or date.min, reverse=True):
        ident = row.get("pd_id")
        if not isinstance(ident, str) or not re.fullmatch(r"[1-9]\d{4,9}", ident) or row.get("brand_name") != brand:
            continue
        published, certified = _date(row.get(":created_at")), _date(row.get(date_key))
        if not (published and certified and start <= certified <= published <= end):
            continue
        url = prefix + ident + ".json"
        if url in seen:
            continue
        seen.add(url)
        try:
            _, data = client.fetch(url, {"application/json"}, 48000, _exact(url))
            doc = _json(data)
            if (not isinstance(doc, dict) or doc.get("pd_id") != ident or doc.get("brand_name") != brand or
                    _date(doc.get(date_key)) != certified or not isinstance(doc.get("model_number"), str)):
                raise _Skip("La ficha de certificación no coincide con su registro.")
            def field(name):
                v = doc.get(name)
                if not isinstance(v, str) or len(v) > 300 or re.search(r"[<>]", v):
                    return ""
                return _display(v)
            model = field("model_name") or field("model_number")
            if not model or _UNSAFE.search(model):
                raise _Skip("Ficha sin identidad de producto verificable.")
            category = "equipo de impresión" if dataset == "t2v6-g4nf" else "computador"
            title = f"ENERGY STAR registra el {category} {brand} {model}"
            body = (f"El registro primario de la EPA identifica a {brand} y el modelo {field('model_number')} "
                    f"con el identificador ENERGY STAR {ident}. La fecha de certificación es {certified.isoformat()} "
                    f"y la fecha de alta del registro es {published.isoformat()}.")
            for key, label in (("type", "Tipo"), ("product_type", "Tipo de producto"),
                               ("processor_brand", "Procesador"), ("markets", "Mercados declarados"),
                               ("tec_of_model_kwh", "Consumo TEC (kWh)")):
                if field(key):
                    body += f" {label}: {field(key)}."
            body += " La certificación no equivale a una fecha de lanzamiento ni a cifras de ventas."
            if not _UNSAFE.search(body):
                out.append({"source_id": source["id"], "title": title, "url": url,
                            "published_at": published.isoformat(), "body": body})
        except _Skip as exc:
            if str(exc) not in warnings:
                warnings.append(str(exc))
        except (SourceError, requests.RequestException, OSError):
            if not out:
                raise
            warnings.append("Consulta interrumpida; se conservan solo los registros ya verificados.")
            break
        if len(out) >= limit or len(seen) - len(known) >= min(8, limit * 2):
            break
    return out, warnings


def _valid_config(s, now):
    if s.get("enabled") is not True:
        raise SourceError("Fuente desactivada.")
    hosts = s["domains"]
    if not isinstance(hosts, list) or not hosts or not all(_host_ok(h) for h in hosts):
        raise SourceError("Lista de dominios no válida.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s["id"]):
        raise SourceError("Identificador de fuente no válido.")
    endpoint = _url(s["endpoint"], hosts)
    kind, pol = s["kind"], s["policy"]
    if kind not in {"rss", "atom", "html", "sec-8k", "epa-records"} or pol.get("status") != "reviewed" or pol.get("scope") != kind:
        raise SourceError("Permiso de recolección ausente o incompatible.")
    checked, expires = date.fromisoformat(pol["reviewed_at"]), date.fromisoformat(pol["review_until"])
    if not checked <= now.date() <= expires or expires - checked > timedelta(days=183):
        raise SourceError("Revisión de condiciones vencida o no vigente.")
    terms = urlsplit(pol["terms_url"])
    if not _host_ok(terms.hostname) or not _url(pol["terms_url"], [terms.hostname]) or not pol.get("basis"):
        raise SourceError("Condiciones de uso no identificadas.")
    if kind == "sec-8k" and (hosts != ["www.sec.gov"] or endpoint != _SEC + "daily-index/" or s.get("cik") not in _ISSUERS):
        raise SourceError("Ámbito EDGAR incorrecto.")
    if kind == "epa-records":
        if (hosts != ["data.energystar.gov"] or s.get("dataset") not in _EPA_DATASETS or
                s.get("brand") not in {"HP", "Lenovo", "DELL"} or
                endpoint.partition("?")[0] != _EPA + s["dataset"] + ".json"):
            raise SourceError("Ámbito EPA incorrecto.")
        stamp = "date_qualified" if s["dataset"] == "t2v6-g4nf" else "date_certified"
        params = parse_qsl(urlsplit(endpoint).query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        if len(params) != 4 or dict(params) != {
                "brand_name": s["brand"], "$select": f"pd_id,brand_name,{stamp},:created_at",
                "$order": stamp + " DESC", "$limit": "12"}:
            raise SourceError("Consulta EPA no autorizada.")
    if kind in {"html", "rss", "atom"} and not (s.get("article_selector") and s.get("body_selector")):
        raise SourceError("Falta la plantilla de detalle.")
    if kind == "html" and not (s.get("card_selector") and s.get("link_selector")):
        raise SourceError("Falta la plantilla de listado.")
    return endpoint


def _discover(source, now, known_urls, limit, state, adapter):
    endpoint = _valid_config(source, now)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise SourceError("Límite no válido.")
    if limit == 0:
        return [], []
    limit = min(limit, 8)
    kind, hosts = source["kind"], source["domains"]
    agent = BOT + "/1.0"
    if kind == "sec-8k":
        contact = os.environ.get("RADAR9_SEC_CONTACT", "")
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,60}@[A-Za-z0-9.-]{1,150}\.[A-Za-z]{2,20}", contact):
            raise SourceError("EDGAR requiere un contacto real en RADAR9_SEC_CONTACT.")
        agent += " (" + contact + ")"
    interval = max(1800, int(source.get("poll_interval_seconds", 21600)))
    delay = float(source.get("min_delay_seconds", 2))
    if not 1 <= delay <= 20:
        raise SourceError("Cadencia no válida.")
    if not state.poll((source["id"], endpoint), interval):
        return [], ["Consulta aplazada por el intervalo de la fuente."]
    known = set()
    for raw in known_urls:
        try:
            known.add(_url(raw, hosts))
        except _Skip:
            pass
    client = _Client(hosts, agent, state, delay, adapter)
    if kind == "epa-records":
        return _epa(client, source, endpoint, now, known, limit)
    pattern = re.compile(source["article_path"], re.ASCII | re.I)

    def guard(url):
        if url in known:
            raise _Known()
        parts = urlsplit(url)
        if (url == endpoint or parts.query or not pattern.fullmatch(parts.path) or
                re.search(r"/(?:feed|rss|atom|tag|tags|category|topics?|author|search|page)(?:/|$)|\.(?:xml|rss|atom|pdf)$", parts.path, re.I) or
                _UNSAFE.search(unquote(parts.path).replace("-", " "))):
            raise _Skip("El enlace no es un detalle autorizado.")

    alerts, results = [], []

    def warn(exc):
        if str(exc) and str(exc) not in alerts and len(alerts) < 8:
            alerts.append(str(exc))

    if kind in {"rss", "atom"}:
        representations = {_url(u, hosts) for u in source.get("channel_canonicals", [])}
        _, data = client.fetch(endpoint, _XML, _FEED_MAX, _exact(endpoint), representations)
        hints = list(_feed(data))
    elif kind == "html":
        base, listing = _page(client, endpoint, _exact(endpoint))
        if not listing.select_one(source["card_selector"]):
            raise SourceError("No se reconoce el listado editorial autorizado.")
        hints = list(_index_hints(listing, source, base))
    else:
        hints = _sec_hints(client, now, known, source["cik"])
    if isinstance(hints, list):
        hints.sort(key=lambda h: h.day or date.min, reverse=True)
    minimum, maximum = now.date() - timedelta(days=6), now.date()
    required = re.compile(source["headline_filter"], re.I) if source.get("headline_filter") else None
    tried = set()
    attempts = 0
    try:
        for hint in hints:
            if len(results) >= limit or attempts >= min(12, max(limit * 2, 4)):
                break
            if (hint.day and not minimum <= hint.day <= maximum) or _UNSAFE.search(hint.title):
                continue
            if required and hint.title and not required.search(hint.title):
                continue
            try:
                hint.url = _url(hint.url, hosts)
                guard(hint.url)
                if hint.url in tried:
                    continue
                tried.add(hint.url)
                attempts += 1
                final, page = _page(client, hint.url, _exact(hint.url) if kind == "sec-8k" else guard)
                title, day, body = _sec_article(page, source["cik"]) if kind == "sec-8k" else _article(source, page, hint, final)
                if not minimum <= day <= maximum:
                    continue
                if (not 5 <= len(title) <= 400 or len(body) < 80 or _UNSAFE.search(title + "\n" + body) or
                        (required and not required.search(title))):
                    raise _Skip("Artículo sin texto editorial utilizable o fuera del ámbito defensivo.")
                results.append({"source_id": source["id"], "title": title, "url": final,
                                "published_at": day.isoformat(), "body": body[:2400].rstrip()})
                known.update({hint.url, final})
            except _Skip as exc:
                warn(exc)
            if len(results) >= limit or attempts >= min(12, max(limit * 2, 4)):
                break
    except _Budget:
        pass  # remaining items can wait for the next scheduled run
    except (SourceError, requests.RequestException, OSError):
        if not results:
            raise
        warn(_Skip("Consulta interrumpida; se conservan solo los artículos ya verificados."))
    return sorted(results, key=lambda r: r["published_at"], reverse=True), alerts


def discover(source: dict, now: datetime, known_urls: set[str], limit: int = 4) -> tuple[list[dict], list[str]]:
    """Return dated, validated detail pages for today and the preceding six dates.

    Offline tests inject transport and clock via _discover. Never log server text,
    raw exception messages, query strings, credentials or operator contact details.
    """
    try:
        with closing(requests.adapters.HTTPAdapter(max_retries=0)) as adapter:
            return _discover(source, now, known_urls, limit, _STATE, adapter)
    except SourceError:
        raise
    except _Skip as exc:
        raise SourceError(str(exc)) from None
    except (requests.RequestException, OSError):
        raise SourceError("No se pudo consultar la fuente por HTTPS.") from None
    except (_Budget, KeyError, TypeError, ValueError, AttributeError, SyntaxError, NotImplementedError, re.error, RecursionError):
        raise SourceError("Configuración o documento de fuente no válido.") from None
