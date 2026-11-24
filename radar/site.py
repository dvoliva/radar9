"""Publicación estática de RADAR 9. Ejecutar desde la raíz del repositorio."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE = "/radar9/"
WEB = Path(__file__).resolve().parent.parent / "web"
ASSETS = ("radar.css", "preferencias.js", "radar.js", "marca.svg", "barrido.svg")
MARKER = ".radar9-publicacion"
CSP = ("default-src 'none'; base-uri 'none'; object-src 'none'; script-src 'self'; "
       "style-src 'self'; img-src 'self'; connect-src 'self'; form-action 'self'")
MONTHS = ("", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
NAV = (("Inicio", ""), ("Hoy", "hoy/"), ("Categorías", "categorias/"),
       ("Explorar", "explorar/"), ("Favoritos", "favoritos/"), ("Fuentes", "fuentes/"),
       ("Configuración", "configuracion/"))
SECTIONS = (("event", "hecho", "Qué ocurrió"), ("context", "contexto", "Contexto"),
            ("analysis", "analisis", "Análisis"),
            ("implications", "implicaciones", "Implicaciones profesionales"),
            ("watch", "seguimiento", "Qué vigilar"))
LABELS = {"nueva": "Nueva", "reciente": "Reciente", "seguimiento": "Seguimiento"}
DEPTHS = {"breve": "Breve", "estándar": "Estándar", "profundo": "Profundo"}
METHODS = {"curaduría": "Curaduría editorial", "ia": "Análisis asistido por IA"}
SOURCE_TYPES = {"documental": "Publicación documental", "rss": "Canal RSS", "atom": "Canal Atom",
                "html": "Página web", "sec-8k": "Informes 8-K de la SEC",
                "epa-records": "Registros de certificación de la EPA", "json": "Datos JSON", "api": "API pública"}
FEED_PARAMS = {"feed": r"rss2?|atom|rdf", "format": r"rss2?|atom|xml|json",
               "alt": r"rss|atom|json", "lang": r"[a-z]{2}(?:-[a-z]{2})?", "id": r"[0-9]{1,16}"}
EPA_PARAMS = {"brand_name": r"[\w .-]{1,64}", "$select": r"[:\w]+(?:,[:\w]+){0,24}",
              "$order": r"[:\w]+(?: (?:ASC|DESC))?", "$limit": r"[1-9][0-9]{0,5}"}
CITATION = re.compile(r"\[(\d{1,4})\]")
HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                  r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,119}")
RESERVED = re.compile(r"con|prn|aux|nul|com[0-9]|lpt[0-9]")


class SiteError(ValueError):
    """Diagnóstico de consola que no reproduce el contenido de las entradas."""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(s for item in value if (s := text(item))))


def mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def rows(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def identifier(value: object) -> str:
    return str(value) if type(value) in (int, str) and str(value).strip() else ""


def slug(value: str) -> str:
    # Los títulos nunca intervienen en la URL. Un segundo espacio de nombres
    # evita colisiones de mayúsculas, rutas relativas y nombres DOS reservados.
    if SLUG.fullmatch(value) and not RESERVED.fullmatch(value):
        return value
    return "~" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def field_id(value: object) -> int | None:
    return value if type(value) is int and 1 <= value <= 9 else None


def field_ids(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(i for v in value if (i := field_id(v)) is not None))


def iso_day(value: object) -> str:
    s = text(value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            pass
    return ""


@cache
def santiago() -> ZoneInfo:
    try:
        return ZoneInfo("America/Santiago")
    except ZoneInfoNotFoundError:
        # CPython en Windows no incluye una base IANA. Esta única zona es
        # información pública, no un paquete requerido en tiempo de ejecución.
        raw = base64.b64decode((WEB / "santiago.tzif.b64").read_bytes())
        return ZoneInfo.from_file(io.BytesIO(raw), key="America/Santiago")


def instant(value: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text(value))
        return dt.astimezone(santiago()) if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def date_name(value: str, weekday: bool = False) -> str:
    if not value:
        return "Sin fecha"
    d = date.fromisoformat(value)
    return (f"{DAYS[d.weekday()]}, " if weekday else "") + f"{d.day} de {MONTHS[d.month]} de {d.year}"


def day_tag(value: str, weekday: bool = False) -> str:
    return f'<time datetime="{value}">{date_name(value, weekday)}</time>' if value else "Sin fecha"


def clock(dt: datetime | None) -> str:
    if dt is None:
        return "Sin registro"
    return (f'<time datetime="{dt.isoformat()}">{date_name(dt.date().isoformat())}, '
            f'{dt:%H:%M}</time>')


def count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def domain(value: object) -> str:
    host = text(value).lower().rstrip(".")
    if HOST.fullmatch(host) and not host.endswith((".localhost", ".local", ".internal")):
        return host
    return ""


def public_url(value: object, domains: tuple[str, ...], *, endpoint_kind: str | None = None) -> str:
    """Solo enlaces editoriales HTTP(S) a hosts catalogados; nunca credenciales."""
    raw = text(value)
    if not raw or "\\" in raw or re.search(r"[\x00-\x20\x7f]", raw):
        return ""
    try:
        u = urlsplit(raw)
        host = domain(u.hostname)
        if (u.scheme not in ("https", "http") or u.username is not None or u.password is not None
                or u.port not in (None, 80, 443) or not host
                or not any(host == d or host.endswith("." + d) for d in domains)):
            return ""
        if endpoint_kind is not None:
            # Un enlace a un canal debe conservar su consulta funcional íntegra.
            # Si parece privada o no la reconocemos, no publicamos una URL mutilada.
            allowed = EPA_PARAMS if endpoint_kind == "epa-records" else FEED_PARAMS
            params = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True, max_num_fields=24)
            if u.scheme != "https" or any(k not in allowed or not re.fullmatch(allowed[k], v, re.I)
                                          for k, v in params):
                return ""
            return urlunsplit((u.scheme, host, u.path or "/", u.query, ""))
        # Conservamos selectores públicos de documentos, no firmas, sesiones ni
        # parámetros de seguimiento de las URL del colector.
        query = [(k, v) for k, v in parse_qsl(u.query) if
                 k.lower() in {"id", "p", "doc", "docid", "documentid", "articleid", "page", "lang", "v"}
                 and re.fullmatch(r"[\w.-]{1,96}", v)]
        return urlunsplit((u.scheme, host, u.path or "/", urlencode(query), ""))
    except ValueError:
        return ""


def read_json(path: Path, default: object = None) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SiteError("No se pudo leer una entrada JSON de la publicación.") from exc


@dataclass(frozen=True)
class Source:
    key: str
    org: str
    name: str
    domains: tuple[str, ...]
    kind: str
    endpoint: str
    fields: tuple[int, ...]
    enabled: bool
    status: str
    success: datetime | None
    checked: datetime | None


def public_status(value: object) -> str:
    key = text(value).lower().replace("-", "_").replace(" ", "_")
    if key == "verificada_manualmente":
        return "Verificada manualmente"
    if key in {"deshabilitada", "desactivada"}:
        return "Deshabilitada"
    if key == "requiere_contacto":
        return "Pendiente de contacto para EDGAR"
    if key in {"ok", "success", "healthy", "disponible", "operativa", "activa", "sin_novedades", "actualizada"}:
        return "Operativa"
    if key in {"error", "failed", "failure", "no_disponible", "unavailable", "timeout", "degradada", "fallo"}:
        return "Sin respuesta"
    if key in {"partial", "parcial"}:
        return "Comprobación parcial"
    return "Pendiente de comprobación"


@dataclass(frozen=True)
class Reference:
    source: Source | None
    title: str
    url: str
    published: str
    verified: datetime | None


@dataclass(frozen=True)
class Article:
    key: str
    title: str
    summary: str
    sections: tuple[tuple[str, str, str], ...]
    concepts: tuple[tuple[str, str], ...]
    refs: tuple[Reference, ...]
    primary: int
    fields: tuple[int, ...]
    companies: tuple[str, ...]
    industries: tuple[str, ...]
    relevance: int
    depth: str
    method: str
    published: str
    detected: datetime | None
    added: datetime | None
    minutes: int

    @property
    def path(self) -> str:
        return BASE + "articulos/" + self.key + "/"

    @property
    def body(self) -> str:
        return "\n".join([self.summary, *(v for _, _, v in self.sections),
                          *(f"{t}: {d}" for t, d in self.concepts)])


@dataclass(frozen=True)
class Edition:
    day: str
    published: datetime | None
    briefing: str
    items: tuple[tuple[Article, str], ...]

    @property
    def path(self) -> str:
        return BASE + "ediciones/" + self.day + "/"


def parse_article(row: dict, sources: dict[str, Source]) -> Article:
    raw_key = identifier(row.get("id"))
    title = text(row.get("title"))
    primary = field_id(row.get("primary"))
    if not (raw_key and title and primary):
        raise SiteError("Hay un artículo sin identificador, título o campo válido.")
    sections = mapping(row.get("sections"))
    parts = tuple((anchor, name, t) for k, anchor, name in SECTIONS if (t := text(sections.get(k))))
    concepts = tuple((t, d) for c in rows(row.get("concepts"))
                     if (t := text(c.get("term"))) and (d := text(c.get("definition"))))
    refs = []
    for ref in rows(row.get("references")):
        source = sources.get(identifier(ref.get("source_id")))
        refs.append(Reference(source, text(ref.get("title")) or "Documento de referencia",
                              public_url(ref.get("url"), source.domains if source else ()),
                              iso_day(ref.get("published_at")), instant(ref.get("verified_at"))))
    summary = text(row.get("summary"))
    corpus = " ".join([title, summary, *(v for _, _, v in parts), *(t + " " + d for t, d in concepts)])
    relevance = row.get("relevance")
    return Article(slug(raw_key), title, summary, parts, concepts, tuple(refs), primary,
                   tuple(dict.fromkeys([primary, *field_ids(row.get("secondary"))])),
                   strings(row.get("companies")), strings(row.get("industries")),
                   max(0, min(100, relevance)) if type(relevance) is int else 0,
                   DEPTHS.get(text(row.get("depth")), "Sin clasificar"),
                   METHODS.get(text(row.get("method")), "No indicado"),
                   iso_day(row.get("published_at")), instant(row.get("detected_at")),
                   instant(row.get("incorporated_at")), max(1, math.ceil(len(corpus.split()) / 210)))


class Catalog:
    def __init__(self, root: Path):
        self.fields = {i: f"Campo {i:02d}" for i in range(1, 10)}
        self.priorities = {}
        for f in rows(read_json(root / "config" / "fields.json", [])):
            i = field_id(f.get("id"))
            if i and (name := text(f.get("name"))):
                self.fields[i] = name
                priority = f.get("priority")
                if text(priority).lower() in {"crítica", "alta", "media", "normal", "baja"}:
                    self.priorities[i] = text(priority).lower()
                elif type(priority) is int and 1 <= priority <= 9:
                    self.priorities[i] = str(priority)
        status = mapping(read_json(root / "data" / "status.json", {}))
        self.last_run = instant(status.get("last_run"))
        self.enrichment = "Activo" if status.get("enrichment") == "activo" else "Pendiente"
        checks = mapping(status.get("sources"))
        self.sources: dict[str, Source] = {}
        for s in rows(read_json(root / "config" / "sources.json", [])):
            key = identifier(s.get("id"))
            if key:
                check = mapping(checks.get(key))
                domains = tuple(d for h in strings(s.get("domains")) if (d := domain(h)))
                kind = text(s.get("kind")).lower()
                self.sources[key] = Source(
                    slug(key), text(s.get("org")) or "Organización no indicada", text(s.get("name")),
                    domains, kind, public_url(s.get("endpoint"), domains, endpoint_kind=kind),
                    field_ids(s.get("fields")), s.get("enabled") is True,
                    public_status(check.get("status")), instant(check.get("last_success")),
                    instant(check.get("checked_at")))
        self.articles: dict[str, Article] = {}
        for path in sorted((root / "data" / "articles").glob("*.json")):
            a = parse_article(mapping(read_json(path)), self.sources)
            if a.key in self.articles:
                raise SiteError("Hay identificadores de artículo duplicados.")
            self.articles[a.key] = a
        self.membership: dict[str, list[str]] = defaultdict(list)
        self.editions: dict[str, Edition] = {}
        for path in sorted((root / "data" / "editions").glob("*.json"), reverse=True):
            e = mapping(read_json(path))
            day = iso_day(e.get("date"))
            if not day or day != path.stem:
                raise SiteError("La fecha de una edición no coincide con su archivo.")
            items = []
            for item in rows(e.get("items")):
                key = slug(identifier(item.get("id")))
                if key not in self.articles:
                    raise SiteError("Una edición remite a un artículo inexistente.")
                items.append((self.articles[key], LABELS.get(text(item.get("label")), "En esta edición")))
                if day not in self.membership[key]:
                    self.membership[key].append(day)
            briefing = text(e.get("briefing")) or "\n\n".join(strings(e.get("briefing")))
            self.editions[day] = Edition(day, instant(e.get("published_at")), briefing, tuple(items))
        self.latest = next(iter(self.editions.values()), None)
        self.by_date = sorted(self.articles.values(), key=lambda a: (a.published, a.relevance, a.key),
                              reverse=True)
        self.by_field = {i: [a for a in self.by_date if i in a.fields] for i in self.fields}

    def search_index(self) -> list[dict]:
        # Proyección editorial explícita: ningún volcado de artículos, estado o catálogo.
        return [{"id": a.key, "title": a.title, "body": a.body, "fields": a.fields,
                 "companies": a.companies, "industries": a.industries,
                 "sources": sorted({r.source.key for r in a.refs if r.source}),
                 "keywords": " ".join([*(self.fields[i] for i in a.fields),
                                       *(r.source.org + " " + r.source.name for r in a.refs if r.source),
                                       *a.companies, *a.industries]),
                 "published": a.published, "detected": a.detected.date().isoformat() if a.detected else "",
                 "added": a.added.date().isoformat() if a.added else "",
                 "editions": self.membership[a.key], "minutes": a.minutes, "relevance": a.relevance}
                for a in self.by_date]


def cited(s: str, n: int) -> str:
    def replace(match: re.Match) -> str:
        i = int(match[1])
        if 1 <= i <= n:
            return f'<sup><a href="#fuente-{i}" aria-label="Consultar la fuente {i}">[{i}]</a></sup>'
        return match[0]
    return CITATION.sub(replace, esc(s))


def paragraphs(s: str, refs: int = 0) -> str:
    return "".join(f"<p>{cited(p.strip(), refs)}</p>" for p in re.split(r"\r?\n\s*\n", s) if p.strip())


def link(path: str, label: str, cls: str = "") -> str:
    return f'<a class="{esc(cls)}" href="{esc(path)}">{esc(label)}</a>'


@cache
def asset_url(name: str) -> str:
    revision = hashlib.sha256((WEB / name).read_bytes()).hexdigest()[:12]
    return f"{BASE}assets/{name}?v={revision}"


def field_link(c: Catalog, i: int, cls: str = "field-link") -> str:
    return link(f"{BASE}categorias/{i}/", c.fields[i], cls)


def explore_link(param: str, value: str, label: str | None = None) -> str:
    return link(BASE + "explorar/?" + urlencode({param: value}), label or value, "tag")


def section_title(kicker: str, title: str, action: str = "") -> str:
    return (f'<div class="section-head"><div><p class="eyebrow">{esc(kicker)}</p>'
            f'<h2>{esc(title)}</h2></div>{action}</div>')


def notice(title: str, description: str, action: str = "") -> str:
    return f'<div class="notice"><h2>{esc(title)}</h2><p>{esc(description)}</p>{action}</div>'


def favorite_button(a: Article) -> str:
    return (f'<button type="button" class="tool" data-favorite="{a.key}" aria-pressed="false">'
            '<span aria-hidden="true">☆</span> <span data-caption>Guardar en favoritos</span></button>')


def card(c: Catalog, a: Article, label: str = "", number: int | None = None) -> str:
    num = f'<span class="ordinal" aria-hidden="true">{number:02d}</span>' if number is not None else ""
    badge = f'<span class="badge">{esc(label)}</span>' if label else ""
    source = next((r.source.org for r in a.refs if r.source), "")
    source_tag = f'<span>Fuente: {esc(source)}</span>' if source else ""
    return (f'<article class="story" data-story="{a.key}" data-minutes="{a.minutes}" '
            f'data-added="{a.added.isoformat() if a.added else ""}" '
            f'data-score="{a.relevance}">{num}<div class="story-copy"><div class="story-top">'
            f'{field_link(c, a.primary)}{badge}<span class="read-badge" data-read-badge hidden>Leído</span>'
            f'</div><h3>{link(a.path, a.title)}</h3>'
            f'<div class="story-meta">{day_tag(a.published)}<span>{a.minutes} min</span>'
            f'{source_tag}</div><details class="story-summary"><summary>Resumen ejecutivo</summary>'
            f'{paragraphs(a.summary)}</details>'
            f'</div></article>')


def resume_panel() -> str:
    return ('<aside class="resume" data-resume hidden><div><p class="eyebrow">Tu punto de lectura</p>'
            '<p data-resume-title></p></div><a class="button secondary" data-resume-link>Reanudar lectura</a></aside>')


def layout(title: str, desc: str, active: str, body: str, canonical: str) -> str:
    primary = {"Inicio", "Hoy", "Explorar"}
    def nav_link(name: str, path: str, cls: str) -> str:
        current = ' aria-current="page"' if name == active else ""
        return f'<a class="{cls}" href="{BASE}{path}"{current}>{name}</a>'
    nav = "".join(nav_link(n, p, "nav-direct" if n in primary else "nav-secondary") for n, p in NAV)
    more = "".join(nav_link(n, p, "nav-item") for n, p in NAV if n not in primary)
    more_active = " has-current" if active in {n for n, _ in NAV if n not in primary} else ""
    return f"""<!doctype html>
<html lang="es-CL"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(desc)}"><meta name="referrer" content="no-referrer">
<title>{esc(title)} · RADAR 9</title><link rel="canonical" href="{esc(canonical)}">
<link rel="icon" href="{asset_url('marca.svg')}" type="image/svg+xml">
<script src="{asset_url('preferencias.js')}"></script><link rel="stylesheet" href="{asset_url('radar.css')}">
<script defer src="{asset_url('radar.js')}"></script></head><body data-base="{BASE}">
<a class="skip" href="#contenido">Saltar al contenido</a>
<header class="mast"><div class="wrap mast-top"><a class="brand" href="{BASE}" aria-label="RADAR 9, inicio">
<img src="{asset_url('marca.svg')}" alt="" width="44" height="44"><span>RADAR <b>9</b></span></a>
<div class="mast-tag">Inteligencia profesional</div>
<a class="archive-link" href="{BASE}archivo/">Archivo de ediciones</a></div>
<nav aria-label="Principal" class="main-nav"><div class="wrap nav-inner">{nav}
<details class="nav-more{more_active}"><summary>Más secciones</summary><div class="nav-panel">{more}</div></details></div></nav></header>
<main class="wrap" id="contenido" tabindex="-1">{body}</main>
<footer><div class="wrap footer-grid"><div><strong>RADAR 9</strong><p>Proyecto personal e independiente. No es una publicación de HP Inc.</p></div>
<div><a href="{BASE}archivo/">Archivo de ediciones</a> · <a href="{BASE}fuentes/">Fuentes</a><p>Horarios de Santiago de Chile · America/Santiago</p></div></div></footer>
<div class="sr-only" role="status" id="anuncio"></div></body></html>
"""


def intro(kicker: str, title: str, desc: str) -> str:
    return (f'<header class="page-intro"><p class="eyebrow">{esc(kicker)}</p>'
            f'<h1>{esc(title)}</h1><p class="dek">{esc(desc)}</p></header>')


def field_grid(c: Catalog) -> str:
    tiles = []
    for i, name in c.fields.items():
        n = len(c.by_field[i])
        priority = f' · Prioridad {esc(c.priorities[i])}' if i in c.priorities else ""
        tiles.append(f'<li><a href="{BASE}categorias/{i}/"><span class="tile-top">'
                     f'<span>Campo {i:02d}</span><span aria-hidden="true">↗</span></span>'
                     f'<strong>{esc(name)}</strong><small>{count(n, "artículo", "artículos")}{priority}'
                     '</small></a></li>')
    return '<ol class="field-grid">' + "".join(tiles) + "</ol>"


def status_strip(c: Catalog) -> str:
    return ('<dl class="status-strip"><div><dt>Última actualización</dt>'
            f'<dd>{clock(c.last_run)}</dd></div><div><dt>Edición disponible</dt>'
            f'<dd>{day_tag(c.latest.day) if c.latest else "Aún no publicada"}</dd></div>'
            f'<div><dt>Enriquecimiento con IA</dt><dd>{c.enrichment}</dd></div></dl>')


def time_selector(e: Edition) -> str:
    choices = [(str(n), f"{n} min") for n in (5, 10, 15, 20, 30)] + [("completa", "Completa")]
    radios = "".join(f'<label><input type="radio" name="tiempo" value="{v}"'
                     + (" checked" if v == "completa" else "") + f'><span>{label}</span></label>'
                     for v, label in choices)
    total = sum(a.minutes for a, _ in e.items)
    return (f'<div class="budget" data-js hidden><fieldset><legend>¿Cuánto tiempo tienes?</legend>'
            f'<div class="segments">{radios}</div></fieldset>'
            '<p>Priorizamos relevancia y duración sin cambiar el orden editorial.</p></div>'
            '<div class="plan-state" data-plan-state hidden><p></p><a class="text-link">Continuar el plan</a></div>'
            f'<p class="list-count" data-budget-result role="status">Edición completa · '
            f'{count(len(e.items), "lectura", "lecturas")} · {total} min estimados</p>')


def edition_list(c: Catalog, e: Edition) -> str:
    listing = "".join(card(c, a, label, i) for i, (a, label) in enumerate(e.items, start=1))
    if not listing:
        listing = notice("Sin artículos en esta edición", "Puedes consultar la cobertura de otras fechas.",
                         link(BASE + "archivo/", "Ver el archivo", "text-link"))
    return (f'<section class="edition-list" id="plan-de-lectura" tabindex="-1" data-edition="{e.day}">' + section_title("Tu edición", "Plan de lectura")
            + time_selector(e) + f'<div class="story-list" data-edition-list>{listing}</div></section>')


def edition_notes(c: Catalog, e: Edition) -> str:
    labels = Counter(label for _, label in e.items)
    counts = " · ".join(count(labels[key], one, many) for key, one, many in
                        (("Nueva", "nueva", "nuevas"), ("Reciente", "reciente", "recientes"),
                         ("Seguimiento", "seguimiento", "seguimientos")) if labels[key]) or "Sin lecturas nuevas"
    return (f'<section class="brief-note"><div><h2>El briefing</h2>'
            f'<p class="brief-counts">{counts}</p></div><div class="brief-copy">'
            f'{paragraphs(e.briefing) or "<p>Consulta los artículos en su orden editorial.</p>"}'
            f'<p class="fine">Publicación: {clock(e.published)} · Santiago</p></div></section>')


def empty_edition() -> str:
    return notice("Aún no hay una edición publicada",
                  "Cuando se publique la primera edición, aquí aparecerán el briefing y sus lecturas.",
                  link(BASE + "explorar/", "Explorar el archivo de artículos", "text-link"))


def home(c: Catalog) -> str:
    e = c.latest
    lead = e.items[0][0] if e and e.items else None
    day = day_tag(e.day, True) if e else "Primera edición pendiente"
    headline = link(lead.path, lead.title) if lead else "Una mirada a nueve campos"
    source = next((r.source.org for r in lead.refs if r.source), "") if lead else ""
    description = " · ".join(filter(None, (source, f"{lead.minutes} min de lectura"))) if lead else (
        "Actualidad tecnológica y empresarial explicada en español.")
    artwork = f'<div class="sweep" aria-hidden="true"><img src="{asset_url("barrido.svg")}" alt="" width="540" height="390"></div>'
    result = (f'<header class="home-head"><h1 class="home-title">El radar del día<span>.</span></h1>'
              f'<p class="issue-line">{day}</p></header>'
              f'<section class="cover"><div class="cover-copy"><p class="signal">'
              f'{"En portada · " + esc(c.fields[lead.primary]) if lead else "Cuaderno editorial"}</p>'
              f'<h2>{headline}</h2><p>{esc(description)}</p><div class="cover-actions">'
              + (link(lead.path, "Leer el análisis", "button bright") if lead else "")
              + (link("#plan-de-lectura", "Ir al plan de lectura", "cover-link") if e else
                 link(BASE + "hoy/", "Ver la edición", "cover-link")) + f'</div></div>{artwork}</section>'
              + resume_panel())
    result += (edition_notes(c, e) + edition_list(c, e)) if e else empty_edition()
    return (result + '<p class="visit-note" data-novelty hidden></p>'
            + section_title("Mapa de cobertura", "Los nueve campos",
                                   link(BASE + "categorias/", "Ver categorías", "text-link"))
            + field_grid(c) + status_strip(c))


def edition_page(c: Catalog, e: Edition | None, today: bool) -> str:
    if e is None:
        return intro("Edición del día", "Hoy", "Tu selección editorial más reciente.") + empty_edition() + status_strip(c)
    days = list(c.editions)
    index = days.index(e.day)
    archive = link(BASE + "archivo/", "Todas las ediciones", "text-link")
    nav = [link(c.editions[days[index + 1]].path, "← Edición anterior", "text-link")] if index + 1 < len(days) else []
    nav.append(link(e.path, "Enlace permanente", "text-link") if today else archive)
    if index:
        nav.append(link(c.editions[days[index - 1]].path, "Edición siguiente →", "text-link"))
    return (f'<header class="page-intro"><p class="eyebrow">{"Hoy · Última edición disponible" if today else "Archivo · Edición histórica"}</p>'
            f'<h1>{day_tag(e.day, True)}</h1><p class="dek">El cuaderno de la jornada, artículo por artículo.</p>'
            f'{archive}</header>' + status_strip(c) + resume_panel() + '<p class="visit-note" data-novelty hidden></p>'
            + edition_notes(c, e) + edition_list(c, e)
            + '<nav class="neighbors" aria-label="Navegación entre ediciones">' + "".join(nav) + '</nav>')


def archive_page(c: Catalog) -> str:
    parts = [intro("Hemeroteca", "Archivo de ediciones", "Cada jornada conserva su selección, su fecha y su orden originales.")]
    month = ""
    for e in c.editions.values():
        if e.day[:7] != month:
            if month:
                parts.append("</ul></section>")
            month = e.day[:7]
            d = date.fromisoformat(e.day)
            parts.append(f'<section class="archive-month"><h2>{MONTHS[d.month].capitalize()} de {d.year}</h2><ul>')
        parts.append(f'<li><a href="{e.path}">{day_tag(e.day, True)}</a>'
                     f'<span>{count(len(e.items), "lectura", "lecturas")}</span></li>')
    parts.append("</ul></section>" if month else empty_edition())
    return "".join(parts)


def category_page(c: Catalog, i: int) -> str:
    articles = c.by_field[i]
    return (intro(f"Campo {i:02d} / 09", c.fields[i],
                  "Cobertura principal y conexiones con este campo, de lo más reciente a lo más antiguo.")
            + link(BASE + "categorias/", "← Todos los campos", "text-link")
            + section_title("Archivo temático", count(len(articles), "artículo", "artículos"))
            + ('<div class="story-list">' + "".join(card(c, a) for a in articles) + "</div>" if articles else
               notice("Todavía sin cobertura", "El campo está abierto; sus futuras lecturas quedarán reunidas aquí.")))


def options(items, empty: str = "") -> str:
    return ((f'<option value="">{esc(empty)}</option>' if empty else "")
            + "".join(f'<option value="{esc(v)}">{esc(name)}</option>' for v, name in items))


def select(name: str, label: str, items, empty: str) -> str:
    return (f'<div class="form-field"><label for="filtro-{name}">{esc(label)}</label>'
            f'<select id="filtro-{name}" name="{name}">{options(items, empty)}</select></div>')


def explore_page(c: Catalog) -> str:
    companies = sorted({s for a in c.by_date for s in a.companies}, key=str.casefold)
    industries = sorted({s for a in c.by_date for s in a.industries}, key=str.casefold)
    sources = sorted(c.sources.values(), key=lambda s: (s.org.casefold(), s.name.casefold()))
    facet = (select("campo", "Campo", c.fields.items(), "Todos los campos")
             + select("empresa", "Empresa", ((x, x) for x in companies), "Todas las empresas")
             + select("fuente", "Fuente", ((s.key, f"{s.org} · {s.name}") for s in sources), "Todas las fuentes")
             + select("industria", "Industria", ((x, x) for x in industries), "Todas las industrias")
             + select("edicion", "Edición", ((k, date_name(k)) for k in c.editions), "Todas las ediciones")
             + select("tipo_fecha", "Fecha de referencia", (("publicacion", "Publicación"),
                       ("deteccion", "Detección"), ("incorporacion", "Incorporación")), ""))
    form = f"""<form class="search-panel" id="buscador" hidden>
<label class="query">Buscar en el archivo<input name="q" type="search" placeholder="Tema, palabra u organización"></label>
<details><summary>Filtros combinables</summary><div class="filters">
<label>En el título<input name="titulo" type="search"></label><label>En el cuerpo<input name="cuerpo" type="search"></label>
{facet}<label>Fecha exacta<input name="fecha" type="date"></label><label>Desde<input name="desde" type="date"></label>
<label>Hasta<input name="hasta" type="date"></label></div></details>
<div class="search-actions">{select("orden", "Ordenar por", (("fecha", "Publicación más reciente"),
("antiguos", "Publicación más antigua"), ("relevancia", "Mayor relevancia"), ("incorporacion", "Última incorporación"),
("tiempo", "Menor tiempo de lectura"), ("titulo", "Título, de A a Z")), "")}
<button type="reset" class="tool">Limpiar filtros</button></div></form>"""
    return (intro("Índice de conocimiento", "Explorar", "Cruza temas, organizaciones y fechas en el archivo completo.")
            + form + '<noscript><p>La búsqueda combinable usa JavaScript. El índice completo está disponible a continuación.</p></noscript>'
            + f'<p role="status" id="resultado" class="list-count">{count(len(c.by_date), "artículo", "artículos")}</p>'
            + '<div class="story-list" id="resultados">' + "".join(card(c, a) for a in c.by_date) + "</div>"
            + '<p class="notice" id="sin-resultados" hidden>No hay coincidencias. Prueba con menos filtros o una fecha distinta.</p>')


def sources_page(c: Catalog) -> str:
    parts = [intro("Transparencia", "Fuentes", "Quién publica la información original y cuándo se comprobó por última vez."),
             status_strip(c), section_title("Registro editorial", count(len(c.sources), "fuente", "fuentes"))]
    for s in sorted(c.sources.values(), key=lambda s: (s.org.casefold(), s.name.casefold())):
        website = link("https://" + s.domains[0] + "/", s.domains[0], "text-link") if s.domains else ""
        manual = s.kind == "documental"
        mode = "Documental · revisión manual" if manual else "Automática"
        state = s.status if manual or s.enabled else "Deshabilitada"
        if manual and state == "Pendiente de comprobación":
            state = "Pendiente de verificación manual"
        action = ("Consultar publicación oficial" if manual else
                  "Abrir canal oficial" if s.kind in {"rss", "atom"} else "Consultar origen oficial")
        endpoint = link(s.endpoint, action, "text-link") if s.endpoint else "Sin enlace público al origen"
        success_label = "Última verificación satisfactoria" if manual else "Última respuesta satisfactoria"
        parts.append(f'<article class="source-card"><div><p class="eyebrow">{esc(s.org)}</p>'
                     f'<h3>{esc(s.name or s.org)}</h3>{website}</div><div>'
                     f'<p class="badge">{esc(state)}</p><dl class="source-times">'
                     f'<dt>Modalidad</dt><dd>{mode}</dd><dt>Tipo</dt><dd>{SOURCE_TYPES.get(s.kind, "Publicación oficial")}</dd>'
                     f'<dt>Enlace de origen</dt><dd>{endpoint}</dd><dt>Última comprobación</dt><dd>{clock(s.checked)}</dd>'
                     f'<dt>{success_label}</dt><dd>{clock(s.success)}</dd></dl>'
                     f'<div class="tags">{"".join(field_link(c, i, "tag") for i in s.fields)}</div>'
                     f'{explore_link("fuente", s.key, "Ver artículos de esta fuente")}</div></article>')
    if not c.sources:
        parts.append(notice("Registro aún vacío", "Las organizaciones aparecerán aquí cuando se configure la cobertura."))
    return "".join(parts)


def preferences() -> str:
    return ('<div class="preferences" data-js hidden>'
            '<div class="form-field"><label for="pref-tema">Apariencia</label><select id="pref-tema" data-pref="tema">'
            '<option value="sistema">Según el sistema</option><option value="claro">Modo claro</option>'
            '<option value="oscuro">Modo oscuro</option></select></div>'
            '<div class="form-field"><label for="pref-tamano">Tamaño del texto</label><select id="pref-tamano" data-pref="tamano">'
            '<option value="normal">Normal</option><option value="grande">Grande</option>'
            '<option value="mayor">Muy grande</option></select></div></div>')


def settings_page(c: Catalog) -> str:
    return (intro("Tu dispositivo", "Configuración", "Elige cómo leer. Tus ajustes y favoritos se guardan solo en este navegador.")
            + section_title("Lector", "Preferencias de lectura") + preferences()
            + '<noscript><p>Activa JavaScript para guardar preferencias; sin él se respeta el tema del sistema.</p></noscript>'
            + '<div class="type-sample"><p class="eyebrow">Muestra de texto</p>'
            '<p class="reading-text">Un buen análisis distingue el hecho, su contexto y las preguntas que siguen abiertas.</p></div>'
            + section_title("Datos personales", "Almacenamiento local")
            + '<p>No hay cuenta ni sincronización. Puedes borrar las preferencias, los favoritos, el historial y el progreso de RADAR 9 sin afectar otros sitios.</p>'
            '<details class="reset-panel" data-js hidden><summary>Borrar mis datos de lectura</summary>'
            '<p>Esta acción no se puede deshacer.</p><button class="tool danger" type="button" id="borrar">Confirmar borrado local</button></details>'
            + section_title("Publicación", "Estado del cuaderno") + status_strip(c))


def favorites_page() -> str:
    return (intro("Biblioteca personal", "Favoritos", "Las lecturas que decides conservar en este navegador.")
            + resume_panel() + '<p class="list-count" id="favoritos-cuenta" role="status"></p>'
            '<div id="favoritos-lista"></div><div id="favoritos-vacio" class="notice">'
            '<h2>Tu selección empieza en el lector</h2><p>Usa «Guardar en favoritos» en un artículo para volver a él desde aquí.</p>'
            + link(BASE + "explorar/", "Explorar artículos", "button secondary") + '</div>'
            '<noscript><p>Para acceder a los favoritos locales, activa JavaScript.</p></noscript>')


def related(c: Catalog, a: Article) -> list[Article]:
    fields, firms, industries = set(a.fields), set(a.companies), set(a.industries)
    def affinity(other: Article) -> int:
        return (3 * len(firms.intersection(other.companies)) + 2 * len(fields.intersection(other.fields))
                + int(a.primary == other.primary) + len(industries.intersection(other.industries)))
    ranks = [(score, b) for b in c.by_date if b.key != a.key and (score := affinity(b))]
    ranks.sort(key=lambda t: (t[0], t[1].relevance, t[1].published, t[1].key), reverse=True)
    return [b for _, b in ranks[:3]]


def reference_list(a: Article) -> str:
    parts = ['<ol class="refs">']
    for i, r in enumerate(a.refs, 1):
        title = link(r.url, r.title, "ref-title") if r.url else esc(r.title)
        org = esc(r.source.org) if r.source else "Organización no catalogada"
        parts.append(f'<li id="fuente-{i}"><span class="ref-org">{org}</span>{title}'
                     f'<p>Publicación: {day_tag(r.published)} · Verificación: {clock(r.verified)}</p></li>')
    return "".join(parts) + "</ol>"


def reader(c: Catalog, a: Article) -> str:
    sections = [(anchor, name, paragraphs(v, len(a.refs))) for anchor, name, v in a.sections]
    if a.concepts:
        glossary = "".join(f'<dt>{esc(t)}</dt><dd>{paragraphs(d, len(a.refs))}</dd>' for t, d in a.concepts)
        sections.append(("conceptos", "Conceptos clave", f'<dl class="glossary">{glossary}</dl>'))
    sections.append(("fuentes", "Fuentes y verificación", reference_list(a) if a.refs else '<p>Sin referencias registradas.</p>'))
    contents = "".join(f'<li>{link("#" + anchor, name)}</li>' for anchor, name, _ in sections)
    prose = "".join(f'<section id="{anchor}"><h2>{esc(name)}</h2>{content}</section>'
                    for anchor, name, content in sections)
    edition_links = "".join(link(c.editions[d].path, date_name(d), "tag") for d in c.membership[a.key])
    labels = [label for d in c.membership[a.key][:1] for b, label in c.editions[d].items if b.key == a.key]
    latest_label = f'<span class="badge">{esc(labels[0])}</span>' if labels else ""
    tags = [("Campos", "".join(field_link(c, i, "tag") for i in a.fields)),
            ("Empresas", "".join(explore_link("empresa", s) for s in a.companies)),
            ("Industrias", "".join(explore_link("industria", s) for s in a.industries)),
            ("En estas ediciones", edition_links)]
    tag_rows = "".join(f'<div><dt>{name}</dt><dd class="tags">{values}</dd></div>' for name, values in tags if values)
    recs = related(c, a)
    more = (section_title("Continuar explorando", "Lecturas relacionadas")
            + '<div class="story-list">' + "".join(card(c, b) for b in recs) + "</div>" if recs else "")
    return f"""<article id="lector" data-article="{a.key}">
<nav aria-label="Ruta de lectura" class="crumb">{link(BASE, "Inicio")} <span>/</span> {link(BASE + "categorias/", "Categorías")} <span>/</span> {field_link(c, a.primary)}</nav>
<header class="reader-head"><div class="story-top"><p class="eyebrow">Cuaderno de análisis · Campo {a.primary:02d}</p>{latest_label}</div>
<h1>{esc(a.title)}</h1><div class="lead">{paragraphs(a.summary, len(a.refs))}</div>
<div class="reader-meta"><span>Publicación: {day_tag(a.published)}</span><span>{a.minutes} min de lectura</span>
<span>{esc(a.depth)}</span></div></header>
<div class="reader-tools" data-js hidden>{favorite_button(a)}
<button class="tool" type="button" data-read aria-pressed="false">Marcar como leído</button>
<button class="tool" type="button" data-share>Compartir enlace</button></div>
<label class="share-field" data-copy-field hidden>Enlace para copiar<input readonly></label>
<div class="resume" data-reader-resume hidden><p></p><button type="button" class="tool">Volver al punto guardado</button></div>
<div class="progress-panel" data-js hidden><label for="avance">Progreso de lectura</label>
<progress id="avance" max="100" value="0"></progress><output for="avance">0 %</output></div>
<div class="reader-layout"><aside class="reader-aside"><div><details><summary>Índice del artículo</summary>
<nav aria-label="Índice del artículo"><ol>{contents}</ol></nav></details><details data-js hidden>
<summary>Ajustes de lectura</summary>{preferences()}</details></div></aside><div id="texto" class="prose reading-text">{prose}</div></div>
<section class="file-card"><p class="eyebrow">Trazabilidad</p><h2>Ficha de la lectura</h2><dl>
<div><dt>Elaboración</dt><dd>{esc(a.method)}</dd></div><div><dt>Detección</dt><dd>{clock(a.detected)}</dd></div>
<div><dt>Incorporación</dt><dd>{clock(a.added)}</dd></div><div><dt>Relevancia editorial</dt><dd>{a.relevance}/100</dd></div>{tag_rows}</dl>{link(a.path, "Enlace permanente", "text-link")}</section></article>{more}"""


def render(c: Catalog, out: Path) -> None:
    assets = out / "assets"
    assets.mkdir()
    for name in ASSETS:
        shutil.copyfile(WEB / name, assets / name)
    index = json.dumps(c.search_index(), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (assets / "indice.json").write_text(index.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"),
                                        encoding="utf-8")

    def page(route, title, description, active, body, canonical=None):
        dest = out / route / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(layout(title, description, active, body, canonical or (BASE + route)), encoding="utf-8")

    page("", "Inicio", "El radar del día: inteligencia profesional en nueve campos.", "Inicio", home(c))
    page("hoy/", "Hoy", "La última edición y su plan de lectura.", "Hoy", edition_page(c, c.latest, True),
         c.latest.path if c.latest else None)
    page("archivo/", "Archivo de ediciones", "Todas las ediciones de RADAR 9, por fecha.", "Archivo", archive_page(c))
    page("categorias/", "Categorías", "Los nueve campos de cobertura.", "Categorías",
         intro("Atlas editorial", "Los nueve campos", "Entra por un campo para seguir sus artículos y conexiones.") + field_grid(c))
    for i, name in c.fields.items():
        page(f"categorias/{i}/", name, f"Cobertura del campo {name}.", "Categorías", category_page(c, i))
    page("explorar/", "Explorar", "Busca en el archivo completo de artículos.", "Explorar", explore_page(c))
    page("favoritos/", "Favoritos", "Tu biblioteca local de artículos guardados.", "Favoritos", favorites_page())
    page("fuentes/", "Fuentes", "Organizaciones originales y estado de comprobación.", "Fuentes", sources_page(c))
    page("configuracion/", "Configuración", "Preferencias de lectura y estado de publicación.", "Configuración", settings_page(c))
    for e in c.editions.values():
        page(f"ediciones/{e.day}/", f"Edición del {date_name(e.day)}", e.briefing[:220] or "Edición de RADAR 9.",
             "Archivo", edition_page(c, e, False))
    for a in c.by_date:
        page(f"articulos/{a.key}/", a.title, a.summary[:220], "", reader(c, a))
    (out / MARKER).write_bytes(b"radar9-static-v1\n")
    (out / ".nojekyll").touch()


def rename_directory(source: Path, dest: Path) -> None:
    # En Windows, el indexador puede retener un identificador unos instantes
    # incluso después de cerrar el último HTML. No se reintentan otros errores.
    for attempt in range(8):
        try:
            source.rename(dest)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 7:
                raise
            time.sleep(min(.025 * 2 ** attempt, .6))


def build(root: Path, output: Path) -> None:
    root = root.resolve()
    if not output.is_absolute():
        output = root / output
    if output.is_symlink():
        raise SiteError("La salida no puede ser un enlace simbólico.")
    output = output.resolve()
    protected = (".git", "radar", "web", "config", "data", "tests", ".github")
    if root.is_relative_to(output) or any(output.is_relative_to(root / p) for p in protected):
        raise SiteError("La salida debe ser un directorio dedicado a la publicación.")
    if output.exists():
        if not output.is_dir():
            raise SiteError("La salida debe ser un directorio.")
        marker = output / MARKER
        if any(output.iterdir()) and not (marker.is_file() and
                                         marker.read_bytes() in (b"radar9-static-v1\n", b"radar9-static-v1\r\n")):
            raise SiteError("La salida no está vacía y no pertenece al generador.")
    catalog = Catalog(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Ninguna construcción parcial sustituye a la edición ya publicada.
    with tempfile.TemporaryDirectory(prefix=".radar9-build-", dir=output.parent) as work:
        new = Path(work) / "publicacion"
        new.mkdir()
        render(catalog, new)
        # Fuera de la carpeta temporal: si el sistema tampoco permite la
        # restauración, la última copia válida no se elimina al salir.
        old = Path(work).with_name(Path(work).name + "-anterior")
        if output.exists():
            rename_directory(output, old)
        try:
            rename_directory(new, output)
        except OSError:
            if old.exists():
                rename_directory(old, output)
            raise
        if old.exists():
            shutil.rmtree(old)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Construye la publicación estática de RADAR 9.")
    parser.add_argument("--output", type=Path, default=Path("dist"), help="directorio de salida (predeterminado: dist)")
    args = parser.parse_args(argv)
    try:
        build(Path.cwd(), args.output)
    except SiteError as exc:
        print(f"No se publicó el sitio: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("No se publicó el sitio. Revisa los archivos del generador y los permisos de salida.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
