"""Contrato editorial público y controles que no se delegan al modelo."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from .classify import norm
from .store import ROOT, SANTIAGO, Invalid, day, instant, load, records, uid

PARTS = ("event", "context", "analysis", "implications", "watch")
TEXT = {"type": "string"}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


COPY = {"title": TEXT, "summary": TEXT, "sections": obj(dict.fromkeys(PARTS, TEXT)),
        "concepts": array(obj({"term": TEXT, "definition": TEXT}))}
REPLY = obj({"publishable": {"type": "boolean"}, **COPY, "evidence": array(TEXT)})
FIELD = {"type": "integer", "minimum": 1, "maximum": 9}
REF = obj(dict.fromkeys(("source_id", "title", "url", "published_at", "verified_at"), TEXT))
ARTICLE = obj({**COPY, "id": TEXT, "references": array(REF), "primary": FIELD, "secondary": array(FIELD),
               "industries": array(TEXT), "companies": array(TEXT),
               "relevance": {"type": "integer", "minimum": 0, "maximum": 100},
               "depth": {"enum": ["breve", "estándar", "profundo"]}, "published_at": TEXT,
               "detected_at": TEXT, "incorporated_at": TEXT, "method": {"enum": ["ia", "curaduría"]}})
EDITION = obj({"date": TEXT, "published_at": TEXT, "briefing": TEXT,
               "items": array(obj({"id": TEXT, "label": {"enum": ["nueva", "reciente", "seguimiento"]}}))})
_SCHEMAS = {key: Draft202012Validator(value) for key, value in
            (("reply", REPLY), ("article", ARTICLE), ("edition", EDITION))}


def shape(key, data):
    error = next(_SCHEMAS[key].iter_errors(data), None)
    if error:
        # Nunca volcar una respuesta del proveedor en los logs.
        raise Invalid("Esquema " + key + ": " + ".".join(map(str, error.absolute_path)) + " (" + error.validator + ")")


def authorized(url, domains):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in domains and parts.netloc == parts.hostname
                and not parts.fragment and not re.search(r"[\x00-\x20\\]", url))
    except (TypeError, ValueError):
        return False


def prose(article):
    return "\n".join([article["title"], article["summary"], *article["sections"].values(),
                      *(c["term"] + ": " + c["definition"] for c in article["concepts"])])


def check_copy(article):
    text = prose(article)
    if not 80 <= len(article["summary"].split()) <= 150:
        raise Invalid("Resumen ejecutivo fuera de 80–150 palabras")
    if (not 8 <= len(article["title"]) <= 220 or len(text) > 20000 or
            re.search(r"https?://|<[/!a-zA-Z][^>]*>|```|[\x00-\x08]", text)):
        raise Invalid("Contenido no admisible en el texto editorial")
    if any(len(article["sections"][part].split()) < 8 for part in PARTS):
        raise Invalid("Sección editorial incompleta")
    if not 1 <= len(article["concepts"]) <= 9 or any(not c["term"].strip() or not c["definition"].strip()
                                                     for c in article["concepts"]):
        raise Invalid("Glosario incompleto")
    # Margen de edición: no incentivar relleno si la fuente no aporta más contexto.
    lo, hi = {"breve": (225, 450), "estándar": (450, 890), "profundo": (810, 1650)}[article["depth"]]
    if not lo <= len(text.split()) <= hi:
        raise Invalid("Extensión incompatible con la profundidad")
    common = {"de", "que", "del", "una", "las", "los", "para", "como", "el", "con", "por", "en", "sin", "su"}
    for part in (article["summary"], article["sections"]["analysis"]):
        words = norm(part).split()
        if sum(w in common for w in words) < max(3, len(words) // 10):
            raise Invalid("No se reconoce redacción en español")
    citations = [int(n) for n in re.findall(r"\[(\d+)\]", text)]
    if (not citations or min(citations) < 1 or max(citations) > len(article["references"]) or
            any(not re.search(r"\[\d+\]", article["sections"][p]) for p in ("event", "context"))):
        raise Invalid("Faltan referencias trazables en hechos o contexto")


def check_article(a, sources, now):
    shape("article", a)
    refs = a["references"]
    if not refs or len(refs) > 12 or a["id"] != uid(refs[0]["url"]) or a["published_at"] != refs[0]["published_at"]:
        raise Invalid("Identidad o fecha incompatible con la referencia principal")
    if a["primary"] in a["secondary"] or len(set(a["secondary"])) != len(a["secondary"]):
        raise Invalid("Campos duplicados")
    detected, added = instant(a["detected_at"]), instant(a["incorporated_at"])
    if not detected <= added <= now or day(a["published_at"]) > detected.astimezone(SANTIAGO).date():
        raise Invalid("Cronología de incorporación no válida")
    for r in refs:
        if r["source_id"] not in sources or not authorized(r["url"], sources[r["source_id"]]["domains"]):
            raise Invalid("Referencia fuera de los dominios autorizados")
        verified = instant(r["verified_at"])
        if day(r["published_at"]) > verified.astimezone(SANTIAGO).date() or verified > added:
            raise Invalid("Referencia futura o no verificada al incorporar")
    check_copy(a)


def validate(root=ROOT, *, now=None):
    now = now or datetime.now(timezone.utc)
    fields = load(root / "config/fields.json")
    if sorted(f["id"] for f in fields) != list(range(1, 10)):
        raise Invalid("Se requieren nueve campos")
    catalog = load(root / "config/sources.json")
    sources = {s["id"]: s for s in catalog}
    if len(sources) != len(catalog) or not sources:
        raise Invalid("Catálogo vacío o con identificadores repetidos")
    for s in catalog:
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]*", s["id"]) or not s["fields"] or
                set(s["fields"]) - set(range(1, 10)) or not authorized(s["endpoint"], s["domains"])):
            raise Invalid("Fuente no válida: " + s["id"])
    articles = records(root / "data/articles")
    primaries = set()
    for key, a in articles.items():
        check_article(a, sources, now)
        url = a["references"][0]["url"]
        if key != a["id"] or url in primaries:
            raise Invalid("Artículo duplicado o nombre de archivo incorrecto")
        primaries.add(url)
    seen = set()
    for key, e in records(root / "data/editions").items():
        shape("edition", e)
        published = instant(e["published_at"])
        if key != e["date"] or published.astimezone(SANTIAGO).date() != day(key) or published > now:
            raise Invalid("Fecha de edición incompatible con America/Santiago")
        if not e["briefing"].strip():
            raise Invalid("Falta briefing de edición")
        selected = set()
        for item in e["items"]:
            k = item["id"]
            if k not in articles or k in selected or instant(articles[k]["incorporated_at"]) > published:
                raise Invalid("Selección histórica no válida")
            age = (day(key) - day(articles[k]["published_at"])).days
            expected = "seguimiento" if k in seen else "nueva" if age == 0 else "reciente"
            if age < 0 or (k not in seen and age > 6) or item["label"] != expected:
                raise Invalid("La edición presenta una fecha o novedad incorrecta")
            selected.add(k)
        seen |= selected
    return len(articles), len(seen)


if __name__ == "__main__":
    count, listed = validate()
    print(f"Histórico válido: {count} artículos; {listed} seleccionados en ediciones.")
