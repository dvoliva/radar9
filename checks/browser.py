"""Abre el artefacto real en Chromium nuevo; no usa el perfil del lector."""
import argparse
import re
import threading
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import expect, sync_playwright

from radar.preview import server
from radar.store import ROOT, records
from radar.verify import BASE


@contextmanager
def target(live):
    if live:
        yield "https://dvoliva.github.io/radar9/"
        return
    with server(ROOT / "dist", 0) as httpd:
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}{BASE}"
        finally:
            httpd.shutdown()
            worker.join(timeout=5)


def luminance(rgb):
    channels = [int(v) / 255 for v in re.fullmatch(r"rgb\((\d+), (\d+), (\d+)\)", rgb).groups()]
    linear = [(v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) for v in channels]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))


def check_contrast(page):
    colors = page.locator(".issue-line, .cover h2, .signal, .bright, .story-summary summary, "
                          ".story-top .badge, .segments input:checked + span").evaluate_all("""items =>
        items.filter(el => el.getClientRects().length).map(el => {
          let bg = el;
          while (bg.parentElement && getComputedStyle(bg).backgroundColor === 'rgba(0, 0, 0, 0)')
            bg = bg.parentElement;
          return [el.className || el.tagName, getComputedStyle(el).color, getComputedStyle(bg).backgroundColor];
        })""")
    for name, fg, bg in colors:
        low, high = sorted((luminance(fg), luminance(bg)))
        if (high + .05) / (low + .05) < 4.5:
            raise AssertionError(f"Contraste de lectura insuficiente en {name}: {fg} / {bg}")


def exercise(browser, base):
    issues = []
    articles = records(ROOT / "data/articles")
    editions = records(ROOT / "data/editions")
    origin = urlsplit(base).netloc
    for width, height in [(1366, 900), (390, 844)]:
        ctx = browser.new_context(viewport={"width": width, "height": height}, locale="es-CL", color_scheme="light")
        page = ctx.new_page()
        page.set_default_timeout(7000)
        page.on("pageerror", lambda exc: issues.append("JavaScript: " + str(exc)))
        page.on("response", lambda r: issues.append(f"HTTP {r.status} {r.url}")
                if r.status >= 400 and urlsplit(r.url).netloc == origin else None)
        page.on("console", lambda msg: issues.append(msg.text[:240]) if msg.type == "error" else None)
        page.goto(base)
        expect(page.locator("html")).to_have_attribute("lang", re.compile("^es"))
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        for scheme in ("dark", "light"):
            page.emulate_media(color_scheme=scheme)
            paper = page.locator("body").evaluate("el => getComputedStyle(el).backgroundColor")
            if (luminance(paper) < .5) != (scheme == "dark"):
                raise AssertionError("El tema automático no respeta el sistema")
            check_contrast(page)
        if editions and next(reversed(editions.values()))["items"]:
            page.get_by_role("link", name="Ir al plan de lectura", exact=True).click()
            expect(page).to_have_url(re.compile(r"#plan-de-lectura$"))
            page.get_by_role("radio", name="5 min", exact=True).check()
            expect(page.locator("input[name=tiempo][value='5']")).to_be_checked()
            expect(page.locator("[data-budget-result]")).to_contain_text("5 min")
            page.get_by_role("radio", name="Completa", exact=True).check()
            lead = articles[next(reversed(editions.values()))["items"][0]["id"]]
            digest = page.locator(f'[data-story="{lead["id"]}"] .story-summary')
            expect(digest.locator("p").first).to_be_hidden()
            digest.locator("summary").click()
            expect(digest.locator("p").first).to_contain_text(lead["summary"].split("\n\n")[0])
        routes = {}
        principal = page.get_by_role("navigation", name="Principal", exact=True)
        if width <= 768:
            more = principal.get_by_text("Más secciones", exact=True)
            more.focus()
            more.press("Enter")
            expect(principal.get_by_role("link", name="Categorías")).to_be_visible()
            more.press("Escape")
            expect(more).to_be_focused()
            expect(principal.get_by_role("link", name="Categorías")).to_be_hidden()
            more.click()
        for name in ("Hoy", "Categorías", "Explorar", "Favoritos", "Fuentes", "Configuración"):
            nav = principal.get_by_role("link", name=name, exact=True)
            href = nav.get_attribute("href")
            if not href or not urlsplit(urljoin(base, href)).path.startswith(BASE):
                raise AssertionError("Ruta de navegación incorrecta: " + name)
            routes[name] = urljoin(base, href)
        for url in routes.values():
            page.goto(url)
            expect(page.get_by_role("heading", level=1)).to_be_visible()
            if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                raise AssertionError("Desbordamiento horizontal: " + url)
        page.goto(routes["Configuración"])
        page.get_by_label("Apariencia", exact=True).select_option("oscuro")
        page.get_by_label("Tamaño del texto", exact=True).select_option("grande")
        page.reload()
        expect(page.get_by_label("Apariencia", exact=True)).to_have_value("oscuro")
        expect(page.get_by_label("Tamaño del texto", exact=True)).to_have_value("grande")
        page.goto(routes["Explorar"])
        if articles:
            # El archivo sirve del lado estático también con los campos interactivos activados.
            article = next(iter(articles.values()))
            field = page.get_by_label("Buscar en el archivo", exact=True)
            field.fill(article["title"])
            page.get_by_text("Filtros combinables", exact=True).click()
            page.get_by_label("Campo", exact=True).select_option(str(article["primary"]))
            page.get_by_label("Fuente", exact=True).select_option(article["references"][0]["source_id"])
            page.get_by_label("Fecha exacta", exact=True).fill(article["published_at"])
            ed = next((key for key, value in editions.items() if any(i["id"] == article["id"] for i in value["items"])), None)
            if ed:
                page.get_by_label("Edición", exact=True).select_option(ed)
            result = page.get_by_role("link", name=article["title"], exact=True).first
            expect(result).to_be_visible()
            field.fill("sin-coincidencias-radar9-xyzz")
            expect(page.locator("#sin-resultados")).to_be_visible()
            field.fill(article["title"])
            result.click()
            expect(page.get_by_role("heading", name=article["title"], exact=True)).to_be_visible()
            page.locator(".reader-aside summary").filter(has_text="Índice del artículo").click()
            toc = page.get_by_role("navigation", name="Índice del artículo", exact=True)
            toc.get_by_role("link", name="Qué ocurrió", exact=True).click()
            expect(page.locator("#hecho")).to_be_in_viewport()
            if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                raise AssertionError("El lector desborda la pantalla")
            expect(page.locator(f'a[href="{article["references"][0]["url"]}"]').first).to_be_visible()
            button = page.get_by_role("button", name=re.compile("guardar", re.I)).first
            button.click()
            read = page.locator("[data-read]")
            read.click()
            expect(read).to_have_attribute("aria-pressed", "true")
            page.goto(routes["Favoritos"])
            expect(page.get_by_role("link", name=article["title"], exact=True).first).to_be_visible()
            page.reload()
            expect(page.get_by_role("link", name=article["title"], exact=True).first).to_be_visible()
        ctx.close()
    if editions:
        # HTML del lector es un documento, no una aplicación vacía en espera de JavaScript.
        ctx = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(base)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        nav = page.get_by_role("navigation", name="Principal", exact=True)
        nav.get_by_text("Más secciones", exact=True).click()
        nav.get_by_role("link", name="Explorar", exact=True).click()
        if articles:
            detail = page.locator(".story-summary").first
            detail.locator("summary").click()
            expect(detail.locator("p").first).to_be_visible()
        ctx.close()
    if issues:
        raise AssertionError("Errores de navegador:\n" + "\n".join(issues[:12]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Comprobar el despliegue público, no localhost")
    args = parser.parse_args()
    with target(args.live) as destination, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            exercise(browser, destination)
            print("Chromium: lectura y navegación correctas en escritorio y móvil.")
        finally:
            browser.close()
