"""Integración de la CLI sin paquetes instalados, con datos y salida aislados."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BASE = "/radar9/"
ORIGIN = "https://publicacion.example"
PRIVATE = "SOLO_PARA_EL_PROCESO"


class Page(HTMLParser):
    def __init__(self, filename: Path):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.words = []
        self.feed(filename.read_text(encoding="utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.words.append(data)

    def attrs(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]

    @property
    def content(self):
        return " ".join(self.words)


def store(root: Path, name: str, data) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fixture(root: Path) -> None:
    store(root, "config/fields.json", [
        {"id": i, "name": {1: "Observación", 3: "Industria"}.get(i, f"Área {i}"), "priority": i}
        for i in range(1, 10)
    ])
    store(root, "config/sources.json", [{
        "id": "instituto", "org": "Instituto Cívico", "name": "Boletín documental",
        "kind": "rss", "domains": ["instituto.example"], "enabled": True, "fields": [1, 3],
        "endpoint": "https://instituto.example/privado?token=" + PRIVATE
    }])
    store(root, "data/status.json", {
        "last_run": "2026-11-24T02:37:00Z", "enrichment": "pendiente", "debug": PRIVATE,
        "sources": {"instituto": {"status": "error", "last_success": "2026-07-01T03:45:00Z",
                                    "checked_at": "2026-11-24T02:30:00Z", "error": PRIVATE}}
    })
    common = dict(
        summary="Una síntesis con trazabilidad [1].", raw=PRIVATE, method="curaduría",
        primary=1, secondary=[3], industries=["Minería"], companies=["Empresa Álamo"],
        relevance=83, depth="estándar", published_at="2026-11-20",
        detected_at="2026-11-22T15:10:00Z", incorporated_at="2026-11-24T02:00:00Z",
        sections={"event": "Una línea de evidencia [1].\n\nOtro párrafo & su alcance.",
                  "context": "Antecedentes.", "analysis": "Una tendencia medible.",
                  "implications": "Implicaciones para el trabajo.", "watch": "El próximo informe."},
        concepts=[{"term": "Serie", "definition": "Una sucesión de observaciones."}],
        references=[{"source_id": "instituto", "org": PRIVATE, "title": "Documento original",
                     "url": "https://publicaciones.instituto.example/doc?id=32&token=" + PRIVATE,
                     "published_at": "2026-11-20", "verified_at": "2026-11-23T12:00:00Z"}]
    )
    for key, title, more in [
        ("primero", 'Lectura con <marca> & "datos"', {}),
        ("segundo", "Evidencia de archivo", {"relevance": 98, "published_at": "2026-11-19"}),
        ("inédito/../CON", "Artículo sin edición", {"primary": 7, "secondary": [], "companies": [],
                                                  "industries": [], "references": [
            {"source_id": "instituto", "title": "Dominio no registrado",
             "url": "https://instituto.example.otro.example/privado/" + PRIVATE},
            {"source_id": "instituto", "title": "URL con credenciales",
             "url": "https://usuario:" + PRIVATE + "@instituto.example/doc"},
            {"source_id": "instituto", "title": "Protocolo no público", "url": "file:///privado/" + PRIVATE}
        ]})
    ]:
        row = dict(common, id=key, title=title)
        row.update(more)
        store(root, "data/articles/" + {"primero": "a", "segundo": "b"}.get(key, "c") + ".json", row)
    for day, items in [("2026-11-20", [("segundo", "nueva")]),
                       ("2026-11-24", [("primero", "nueva"), ("segundo", "seguimiento")])]:
        store(root, f"data/editions/{day}.json", {"date": day, "published_at": day + "T11:00:00Z",
                                                  "briefing": "La nota de edición.",
                                                  "items": [{"id": key, "label": label} for key, label in items]})


class StaticSiteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix=".prueba-sitio-", dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def cli(self, *args, ok=True):
        # -S y una ruta IANA vacía también ejercen el respaldo en Windows.
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONTZPATH="", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-S", "-m", "radar.site", *args],
                              cwd=self.root, env=env, capture_output=True, encoding="utf-8", timeout=45)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
        return proc

    def page(self, route):
        return Page(self.root / "dist" / route / "index.html")

    def test_empty_repository_and_named_output(self):
        self.cli("--output", "publico")
        public = self.root / "publico"
        home = Page(public / "index.html")
        self.assertIn("Aún no hay una edición publicada", home.content)
        links = {a["href"] for a in home.attrs("a") if "href" in a}
        for i in range(1, 10):
            self.assertIn(f"{BASE}categorias/{i}/", links)
            self.assertTrue((public / "categorias" / str(i) / "index.html").is_file())
        self.assertEqual(json.loads((public / "assets" / "indice.json").read_text(encoding="utf-8")), [])
        self.assertFalse((self.root / "dist").exists())

    def test_history_is_ordered_linked_and_readable_without_javascript(self):
        fixture(self.root)
        self.cli()
        for route in ("hoy", "ediciones/2026-11-24"):
            ed = self.page(route)
            order = [a["data-story"] for a in ed.attrs("article") if "data-story" in a]
            self.assertEqual(order, ["primero", "segundo"])  # La segunda tiene mayor relevancia.
            self.assertIn("La nota de edición.", ed.content)
            self.assertIn("Seguimiento", ed.content)
            self.assertIn("Nueva", ed.content)
        self.assertIn("Evidencia de archivo", self.page("ediciones/2026-11-20").content)
        archive = [a["href"] for a in self.page("archivo").attrs("a")]
        self.assertLess(archive.index(BASE + "ediciones/2026-11-24/"), archive.index(BASE + "ediciones/2026-11-20/"))
        reader = self.page("articulos/primero")
        for literal in ['Lectura con <marca> & "datos"', "Otro párrafo & su alcance.", "Conceptos clave",
                        "Instituto Cívico", "Curaduría editorial", "Evidencia de archivo", "Empresa Álamo"]:
            self.assertIn(literal, reader.content)
        self.assertEqual(reader.attrs("marca"), [])
        urls = {a.get("href") for a in reader.attrs("a")}
        self.assertTrue({"#fuente-1", "#analisis", BASE + "ediciones/2026-11-24/",
                         BASE + "articulos/segundo/", "https://publicaciones.instituto.example/doc?id=32"} <= urls)
        self.assertIn("Artículo sin edición", self.page("explorar").content)
        self.assertIn("Lectura con <marca>", self.page("categorias/3").content)
        self.assertTrue(list((self.root / "dist" / "articulos").glob("~*/index.html")))

    def test_projection_timezone_and_search_facets(self):
        fixture(self.root)
        self.cli()
        directory = self.root / "dist"
        for path in directory.rglob("*"):
            if path.is_file():
                self.assertNotIn(PRIVATE.encode(), path.read_bytes(), path.name)
        self.assertFalse((directory / "data").exists())
        self.assertFalse((directory / "config").exists())
        src = self.page("fuentes")
        self.assertIn("Sin respuesta", src.content)
        times = {a["datetime"] for a in src.attrs("time")}
        self.assertIn("2026-06-30T23:45:00-04:00", times)
        self.assertIn("2026-11-23T23:37:00-03:00", times)
        index = json.loads((directory / "assets" / "indice.json").read_text(encoding="utf-8"))
        row = next(r for r in index if r["id"] == "segundo")
        self.assertIn("Una tendencia medible", row["body"])
        self.assertIn("Instituto Cívico", row["keywords"])
        self.assertEqual((row["fields"], row["companies"], row["sources"], row["industries"]),
                         ([1, 3], ["Empresa Álamo"], ["instituto"], ["Minería"]))
        self.assertEqual(row["editions"], ["2026-11-24", "2026-11-20"])
        self.assertEqual(row["added"], "2026-11-23")
        self.assertIn("2026-11-20", {a.get("value") for a in self.page("explorar").attrs("option")})

    def test_internal_routes_fragments_assets_and_csp(self):
        fixture(self.root)
        self.cli()
        public = self.root / "dist"
        pages = {file: Page(file) for file in public.rglob("index.html")}
        for filename, page in pages.items():
            path = filename.parent.relative_to(public).as_posix().strip(".")
            origin = ORIGIN + BASE + (path + "/" if path else "")
            with self.subTest(path=path):
                self.assertEqual(len(page.attrs("h1")), 1)
                self.assertTrue(page.attrs("html")[0]["lang"].startswith("es"))
                metas = page.attrs("meta")
                csp = next(m["content"] for m in metas if m.get("http-equiv") == "Content-Security-Policy")
                self.assertIn("script-src 'self'", csp)
                self.assertNotIn("unsafe-", csp)
                self.assertTrue(all(s.get("src", "").startswith(BASE + "assets/") for s in page.attrs("script")))
                for tag, attrs in page.tags:
                    for attribute in ("src", "href"):
                        if not attrs.get(attribute):
                            continue
                        u = urlsplit(urljoin(origin, attrs[attribute]))
                        if u.netloc != urlsplit(ORIGIN).netloc:
                            self.assertEqual(tag, "a")
                            self.assertEqual(u.scheme, "https")
                            continue
                        self.assertTrue(u.path.startswith(BASE), u)
                        target = public / u.path.removeprefix(BASE)
                        if u.path.endswith("/"):
                            target = target / "index.html"
                        self.assertTrue(target.is_file(), (filename, attrs))
                        if u.fragment:
                            self.assertIn(u.fragment, {a.get("id") for _, a in pages[target].tags})

    def test_directory_distinguishes_manual_automatic_and_disabled_sources(self):
        root = self.root
        common = {"org": "Instituto Cívico", "domains": ["instituto.example"], "fields": [1]}
        origin = "https://instituto.example/"
        epa = origin + "datos.json?brand_name=HP&$select=id,:created_at&$order=fecha%20DESC&$limit=12"
        inputs = [("manual", "Documentos", "documental", False, origin + "documentos/"),
                  ("auto", "Boletín", "rss", True, origin + "boletin?feed=rss2"),
                  ("datos", "Certificaciones", "epa-records", True, epa),
                  ("inactiva", "Canal antiguo", "rss", False, origin + "privado?token=" + PRIVATE)]
        store(root, "config/sources.json", [
            dict(common, id=k, name=name, kind=kind, enabled=enabled, endpoint=url)
            for k, name, kind, enabled, url in inputs])
        store(root, "data/status.json", {"sources": {
            "manual": {"status": "verificada manualmente", "checked_at": "2026-11-24T11:37:00Z",
                       "last_success": "2026-07-01T09:13:00Z"},
            "auto": {"status": "disponible"}, "inactiva": {"status": "disponible"}}})
        self.cli()
        directory = self.page("fuentes")
        self.assertIn("Documental · revisión manual", directory.content)
        self.assertIn("Verificada manualmente", directory.content)
        self.assertIn("Última verificación satisfactoria", directory.content)
        self.assertEqual(directory.content.count("Deshabilitada"), 1)
        self.assertIn("Automática", directory.content)
        self.assertIn("Canal RSS", directory.content)
        hrefs = {a["href"] for a in directory.attrs("a")}
        self.assertTrue({origin + "documentos/", origin + "boletin?feed=rss2", epa} <= hrefs)
        self.assertFalse(any("privado" in url or PRIVATE in url for url in hrefs))
        self.assertTrue({"2026-11-24T08:37:00-03:00", "2026-07-01T05:13:00-04:00"} <=
                        {t["datetime"] for t in directory.attrs("time")})

    def test_publish_is_dedicated_and_failed_build_preserves_previous_edition(self):
        occupied = self.root / "ajeno"
        occupied.mkdir()
        note = occupied / "conservar.txt"
        note.write_text("intacto", encoding="utf-8")
        self.cli("--output", str(occupied), ok=False)
        self.assertEqual(note.read_text(encoding="utf-8"), "intacto")
        fixture(self.root)
        self.cli()
        ordinal = self.root / "dist" / "ediciones" / "2026-11-20" / "index.html"
        before = ordinal.read_bytes()
        bad = self.root / "data" / "editions" / "2026-11-25.json"
        store(self.root, str(bad.relative_to(self.root)), {"date": "2026-11-25", "items": [{"id": "inexistente"}]})
        self.cli(ok=False)
        self.assertEqual(ordinal.read_bytes(), before)
        bad.unlink()
        (self.root / "data" / "editions" / "2026-11-20.json").unlink()
        self.cli()
        self.assertFalse(ordinal.exists())
        self.assertIn("La nota de edición.", self.page("hoy").content)


if __name__ == "__main__":
    unittest.main()
