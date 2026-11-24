"""Gate del artefacto de Pages: enlaces, anclas, assets y perímetro público."""
import argparse
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from .store import Invalid

BASE = "/radar9/"
ORIGIN = "https://dvoliva.github.io"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lang, self.links, self.ids = None, [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if "id" in a:
            self.ids.add(a["id"])
        for attr in ("href", "src"):
            if a.get(attr):
                self.links.append((tag, a[attr]))


def verify(out):
    out = Path(out).resolve()
    if not (out / "index.html").is_file():
        raise Invalid("Falta la portada")
    pages, targets = {}, defaultdict(set)
    for path in out.rglob("*"):
        if path.is_symlink():
            raise Invalid("Enlace simbólico en el artefacto")
        if not path.is_file():
            continue
        if path == out / ".nojekyll" and path.stat().st_size == 0:
            continue
        if path == out / ".radar9-publicacion" and path.read_bytes().rstrip(b"\r\n") == b"radar9-static-v1":
            continue
        if (any(part.startswith(".") for part in path.relative_to(out).parts) or
                path.suffix not in {".html", ".js", ".css", ".json", ".svg", ".ico", ".webmanifest",
                                    ".png", ".txt", ".xml", ".woff2"}):
            raise Invalid(f"Fichero inesperado en el sitio: {path.name}")
        if path.suffix in {".woff2", ".png", ".ico"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9_]{24,}|-----BEGIN .*PRIVATE KEY", text):
            raise Invalid("Posible secreto en el artefacto")
        if path.suffix == ".json":
            json.loads(text)
        if path.suffix != ".html":
            continue
        page = Page()
        page.feed(text)
        if (page.lang or "").split("-")[0] != "es":
            raise Invalid("Página sin idioma español: " + path.name)
        pages[path] = page
        uri = BASE + path.relative_to(out).as_posix()
        for tag, link in page.links:
            dest = urlsplit(urljoin(ORIGIN + uri, link))
            if dest.scheme not in ("https", "mailto"):
                raise Invalid(f"Protocolo no permitido: {uri}")
            if dest.netloc != "dvoliva.github.io":
                if tag not in ("a", "link"):
                    raise Invalid(f"Recurso ejecutable o visual externo: {uri}")
                continue
            if not dest.path.startswith(BASE):
                raise Invalid(f"Ruta fuera de la base de Pages: {dest.path}")
            local = (out / unquote(dest.path[len(BASE):])).resolve()
            if not local.is_relative_to(out):
                raise Invalid("Ruta fuera del artefacto")
            if local.is_dir():
                local = local / "index.html"
            if not local.is_file():
                raise Invalid(f"Enlace roto desde {uri}: {link}")
            if dest.fragment:
                targets[local].add(unquote(dest.fragment))
    for path, targets_on_page in targets.items():
        if path in pages and targets_on_page - pages[path].ids:
            raise Invalid(f"Ancla rota: {path.name} {sorted(targets_on_page - pages[path].ids)}")
    return len(pages)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", nargs="?", default="dist")
    print(f"{verify(cli.parse_args().directory)} páginas verificadas bajo {BASE}")
