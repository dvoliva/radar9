(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const navMore = $(".nav-more");
  if (navMore) {
    const closeOutside = (event) => {
      if (navMore.open && !navMore.contains(event.target)) navMore.open = false;
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    navMore.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && navMore.open) {
        navMore.open = false;
        $("summary", navMore).focus();
      }
    });
  }
  const base = document.body.dataset.base;
  const prefix = "radar9:v1:";
  const keys = ["preferencias", "favoritos", "leidos", "progreso", "ultima", "visita"];
  const fallback = new Map();
  const validId = (s) => typeof s === "string" && /^(?:[a-z0-9][a-z0-9_-]{0,119}|~[a-f0-9]{64})$/.test(s);
  const articleURL = (id) => base + "articulos/" + id + "/";
  const announce = (s) => { $("#anuncio").textContent = s; };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function load(key, other) {
    try {
      const raw = localStorage.getItem(prefix + key);
      if (raw !== null) return JSON.parse(raw);
    } catch { /* La copia en memoria permite seguir usando los controles. */ }
    return fallback.has(key) ? fallback.get(key) : other;
  }
  function save(key, value) {
    fallback.set(key, value);
    try { localStorage.setItem(prefix + key, JSON.stringify(value)); }
    catch { announce("El navegador no permite guardar estos cambios de forma permanente."); }
  }
  function idSet(key) {
    const value = load(key, []);
    return new Set(Array.isArray(value) ? value.filter(validId) : []);
  }
  function toggle(key, id) {
    const ids = idSet(key);
    if (ids.has(id)) ids.delete(id); else ids.add(id);
    save(key, [...ids]);
    return ids.has(id);
  }
  function node(tag, value, cls) {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = value;
    if (cls) el.className = cls;
    return el;
  }
  let indexPromise;
  function index() {
    if (!indexPromise) {
      indexPromise = fetch(base + "assets/indice.json", { credentials: "omit", referrerPolicy: "no-referrer" })
        .then((r) => { if (!r.ok) throw Error(); return r.json(); })
        .then((data) => {
          if (!Array.isArray(data)) throw Error();
          return data.filter((r) => r && validId(r.id) && typeof r.title === "string");
        });
    }
    return indexPromise;
  }

  const values = {
    tema: ["sistema", "claro", "oscuro"],
    tamano: ["normal", "grande", "mayor"],
    tiempo: ["completa", "5", "10", "15", "20", "30"]
  };
  function prefs() {
    const raw = load("preferencias", {}) || {};
    return Object.fromEntries(Object.entries(values).map(([key, allowed]) =>
      [key, allowed.includes(raw[key]) ? raw[key] : allowed[0]]));
  }
  function setPref(name, value) {
    if (Object.hasOwn(values, name) && values[name].includes(value)) {
      save("preferencias", { ...prefs(), [name]: value });
    }
  }
  function reflectPrefs() {
    const p = prefs();
    document.documentElement.dataset.tema = p.tema;
    document.documentElement.dataset.tamano = p.tamano;
    $$("[data-pref]").forEach((el) => { el.value = p[el.dataset.pref]; });
  }
  reflectPrefs();
  $$("[data-js]").forEach((el) => { el.hidden = false; });
  $$("[data-pref]").forEach((el) => el.addEventListener("change", () => {
    setPref(el.dataset.pref, el.value);
    reflectPrefs();
    announce("Preferencia de lectura actualizada.");
  }));

  function reflectMarks() {
    const favorites = idSet("favoritos");
    const read = idSet("leidos");
    $$("button[data-favorite]").forEach((btn) => {
      const marked = favorites.has(btn.dataset.favorite);
      btn.setAttribute("aria-pressed", String(marked));
      (btn.querySelector("[data-caption]") || btn).textContent = marked ? "Quitar de favoritos" : "Guardar en favoritos";
    });
    const reader = $("#lector");
    if (reader) {
      const btn = $("[data-read]", reader);
      const marked = read.has(reader.dataset.article);
      btn.textContent = marked ? "Marcar como no leído" : "Marcar como leído";
      btn.setAttribute("aria-pressed", String(marked));
    }
    $$("[data-read-badge]").forEach((el) => { el.hidden = !read.has(el.closest("[data-story]").dataset.story); });
  }
  reflectMarks();
  document.addEventListener("click", (evt) => {
    const button = evt.target.closest("button[data-favorite]");
    if (button && validId(button.dataset.favorite)) {
      const on = toggle("favoritos", button.dataset.favorite);
      reflectMarks();
      if ($("#favoritos-lista")) showFavorites(button.dataset.favorite);
      announce(on ? "Artículo guardado en favoritos." : "Artículo retirado de favoritos.");
    }
  });

  async function showFavorites(restoreFocus) {
    const list = $("#favoritos-lista");
    if (!list) return;
    const tally = $("#favoritos-cuenta");
    try {
      const fav = idSet("favoritos");
      const data = fav.size ? await index() : [];
      const byId = new Map(data.map((r) => [r.id, r]));
      const articles = [...fav].reverse().map((id) => byId.get(id)).filter(Boolean);
      const frag = document.createDocumentFragment();
      const read = idSet("leidos");
      articles.forEach((r) => {
        const wrap = node("article", undefined, "saved-story");
        const copy = node("div");
        const ref = node("a", r.title);
        ref.href = articleURL(r.id);
        const heading = node("h2");
        heading.append(ref);
        copy.append(heading, node("p", `${r.minutes} min · ${read.has(r.id) ? "Leído" : "Pendiente de lectura"}`, "fine"));
        const remove = node("button", "Quitar de favoritos", "tool");
        remove.type = "button";
        remove.dataset.favorite = r.id;
        remove.setAttribute("aria-pressed", "true");
        wrap.append(copy, remove);
        frag.append(wrap);
      });
      list.replaceChildren(frag);
      $("#favoritos-vacio").hidden = Boolean(articles.length);
      tally.textContent = plural(articles.length, "artículo guardado", "artículos guardados");
      if (restoreFocus) (list.querySelector("button") || $("#favoritos-vacio a")).focus();
    } catch {
      $("#favoritos-vacio").hidden = true;
      tally.textContent = "No se pudo cargar el índice. Tus favoritos no se han borrado; vuelve a intentarlo más tarde.";
    }
  }
  showFavorites();

  function progressMap() {
    const raw = load("progreso", []);
    return new Map((Array.isArray(raw) ? raw : []).filter((p) =>
      Array.isArray(p) && validId(p[0]) && Number.isInteger(p[1]) && p[1] >= 0 && p[1] <= 100));
  }
  function resume() {
    const last = load("ultima", null);
    const ok = last && validId(last.id) && typeof last.title === "string" && !idSet("leidos").has(last.id);
    $$("[data-resume]").forEach((el) => {
      el.hidden = !ok;
      if (!ok) return;
      $("[data-resume-title]", el).textContent = `${last.title} · ${progressMap().get(last.id) || 0} %`;
      $("[data-resume-link]", el).href = articleURL(last.id) + "?reanudar=1";
    });
  }
  resume();

  function reading() {
    const reader = $("#lector");
    if (!reader) return;
    const id = reader.dataset.article;
    const title = $("h1", reader).textContent;
    const prose = $("#texto");
    const progress = $("#avance");
    const output = $("output[for='avance']");
    const initial = progressMap().get(id) || 0;
    let current = initial, written = initial, frame, timer, progressed = false;
    save("ultima", { id, title });
    function geometry() {
      const box = prose.getBoundingClientRect();
      const start = box.top + scrollY - innerHeight * 0.16;
      return [Math.max(0, start), Math.max(1, box.height - innerHeight * 0.80)];
    }
    function write() {
      if (!progressed || current === written) return;
      const map = progressMap();
      map.delete(id);
      map.set(id, current);
      save("progreso", [...map].slice(-500));
      written = current;
    }
    function update() {
      frame = 0;
      const [start, distance] = geometry();
      current = Math.round(Math.min(1, Math.max(0, (scrollY - start) / distance)) * 100);
      progress.value = current;
      output.textContent = `${current} %`;
      if (current > 0) progressed = true;
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
      clearTimeout(timer);
      timer = setTimeout(write, 180);
    }
    const prompt = $("[data-reader-resume]", reader);
    function restore() {
      const [start, distance] = geometry();
      scrollTo(0, start + distance * initial / 100);
      prompt.hidden = true;
    }
    if (new URLSearchParams(location.search).get("reanudar") === "1" && initial > 0) {
      restore();
      const clean = new URL(location.href);
      clean.searchParams.delete("reanudar");
      history.replaceState(null, "", clean);
    } else if (initial > 0) {
      $("p", prompt).textContent = `Tu punto guardado está al ${initial} % de esta lectura.`;
      prompt.hidden = false;
      $("button", prompt).addEventListener("click", () => {
        prompt.hidden = true;
        restore();
        announce("Se ha recuperado tu punto de lectura.");
      });
    }
    update();
    addEventListener("scroll", schedule, { passive: true });
    addEventListener("resize", schedule);
    addEventListener("pagehide", () => { update(); write(); });
    $("[data-read]", reader).addEventListener("click", () => {
      const on = toggle("leidos", id);
      reflectMarks();
      announce(on ? "Artículo marcado como leído." : "Artículo marcado como no leído.");
    });
    $("[data-share]", reader).addEventListener("click", async () => {
      const url = new URL(articleURL(id), location.origin).href;
      try {
        if (navigator.share) {
          await navigator.share({ title, url });
          return;
        }
      } catch (err) { if (err.name === "AbortError") return; }
      try {
        await navigator.clipboard.writeText(url);
        announce("Enlace permanente copiado.");
      } catch {
        const field = $("[data-copy-field]", reader);
        field.hidden = false;
        const input = $("input", field);
        input.value = url;
        input.focus();
        input.select();
        announce("Selecciona y copia el enlace permanente.");
      }
    });
  }
  reading();

  function editionPlan() {
    const ed = $("[data-edition]");
    if (!ed) return;
    const cards = $$("[data-story]", ed);
    const radios = $$("[name='tiempo']", ed);
    const ranked = [...cards].sort((a, b) =>
      Number(b.dataset.score) - Number(a.dataset.score) || Number(a.dataset.minutes) - Number(b.dataset.minutes));
    function select(value) {
      const full = value === "completa";
      let left = full ? Infinity : Number(value), spent = 0;
      const selected = new Set();
      ranked.forEach((el) => {
        const n = Number(el.dataset.minutes);
        if (n <= left) { selected.add(el); left -= n; spent += n; }
      });
      const digest = selected.size === 0 && ranked.length > 0;
      if (digest) selected.add(ranked[0]);
      cards.forEach((el) => { el.hidden = !selected.has(el); });
      const desc = digest ? "Ningún análisis completo cabe en ese tiempo. Te proponemos el resumen de la lectura más relevante."
        : `${plural(selected.size, "lectura", "lecturas")} · ${spent} min estimados`;
      $("[data-budget-result]", ed).textContent = (full ? "Edición completa" : `Plan de ${value} min`) + " · " + desc;
      const state = $("[data-plan-state]", ed);
      const read = idSet("leidos");
      const pending = cards.filter((el) => selected.has(el) && !read.has(el.dataset.story));
      const complete = selected.size - pending.length;
      state.hidden = complete === 0;
      if (complete) {
        $("p", state).textContent = pending.length ? `Has leído ${complete} de ${selected.size} artículos del plan.` : "Plan completado.";
        const next = $("a", state);
        next.hidden = pending.length === 0;
        if (pending.length) next.href = articleURL(pending[0].dataset.story);
      }
    }
    function refresh() {
      const value = prefs().tiempo;
      radios.forEach((radio) => { radio.checked = radio.value === value; });
      select(value);
    }
    radios.forEach((radio) => {
      radio.addEventListener("change", () => {
        if (radio.checked) { setPref("tiempo", radio.value); select(radio.value); }
      });
    });
    refresh();
    return refresh;
  }
  const refreshPlan = editionPlan() || (() => {});

  function visit() {
    const now = Date.now();
    const recent = Number(load("visita", 0)) || 0;
    let before = recent;
    try {
      const name = prefix + "sesion";
      const session = JSON.parse(sessionStorage.getItem(name) || "null");
      if (session && now - session.last < 30 * 60 * 1000) before = session.before;
      sessionStorage.setItem(name, JSON.stringify({ before, last: now }));
    } catch { /* Sin almacenamiento por pestaña, se usa la última visita conocida. */ }
    save("visita", now);
    $$("[data-novelty]").forEach((el) => {
      const cards = $$("[data-edition-list] [data-story]");
      if (!cards.length) return;
      let message = "Primera visita · Encuentra las incorporaciones en el plan de lectura.";
      if (Number.isFinite(before) && before > 0) {
        const n = cards.filter((card) => Date.parse(card.dataset.added) > before).length;
        message = n ? `Desde tu última visita: ${plural(n, "incorporación", "incorporaciones")} en esta edición.`
          : "Desde tu última visita: sin nuevas incorporaciones en esta edición.";
      }
      el.textContent = message;
      el.hidden = false;
    });
  }
  visit();

  const fold = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  function matches(haystack, needle) {
    return needle.split(/\s+/u).filter(Boolean).every((word) => haystack.includes(word));
  }
  async function search() {
    const form = $("#buscador");
    if (!form) return;
    const list = $("#resultados");
    const controls = $$("[name]", form);
    const tally = $("#resultado");
    const staticCards = new Map($$("[data-story]", list).map((el) => [el.dataset.story, el]));
    let entries;
    try {
      entries = (await index()).filter((r) => staticCards.has(r.id)).map((r) => {
        const title = fold(r.title), body = fold(r.body);
        return { ...r, ft: title, fb: body, all: `${title}\n${body}\n${fold(r.keywords)}` };
      });
    } catch {
      tally.textContent += " · La búsqueda no está disponible. Puedes recorrer el índice completo.";
      return;
    }
    const params = new URLSearchParams(location.search);
    for (const el of controls) {
      const v = params.get(el.name);
      if (v !== null && (!el.options || Array.from(el.options).some((opt) => opt.value === v))) el.value = v;
    }
    const sorters = {
      fecha: (a, b) => b.published.localeCompare(a.published) || b.relevance - a.relevance,
      antiguos: (a, b) => a.published.localeCompare(b.published),
      relevancia: (a, b) => b.relevance - a.relevance || b.published.localeCompare(a.published),
      incorporacion: (a, b) => b.added.localeCompare(a.added),
      tiempo: (a, b) => a.minutes - b.minutes || b.relevance - a.relevance,
      titulo: (a, b) => a.title.localeCompare(b.title, "es")
    };
    function apply(sync) {
      const f = Object.fromEntries(new FormData(form));
      const q = fold(f.q), title = fold(f.titulo), body = fold(f.cuerpo);
      const dkey = f.tipo_fecha === "deteccion" ? "detected" : f.tipo_fecha === "incorporacion" ? "added" : "published";
      const result = entries.filter((r) => {
        const d = r[dkey];
        return (!q || matches(r.all, q)) && (!title || matches(r.ft, title)) && (!body || matches(r.fb, body))
          && (!f.campo || r.fields.includes(Number(f.campo))) && (!f.empresa || r.companies.includes(f.empresa))
          && (!f.industria || r.industries.includes(f.industria)) && (!f.fuente || r.sources.includes(f.fuente))
          && (!f.edicion || r.editions.includes(f.edicion)) && (!f.fecha || d === f.fecha)
          && (!f.desde || (d && d >= f.desde)) && (!f.hasta || (d && d <= f.hasta));
      });
      result.sort(sorters[f.orden] || sorters.fecha);
      const fragment = document.createDocumentFragment();
      result.forEach((r) => fragment.append(staticCards.get(r.id)));
      list.replaceChildren(fragment);
      tally.textContent = `${plural(result.length, "resultado", "resultados")} de ${entries.length}`;
      $("#sin-resultados").hidden = result.length !== 0;
      if (sync) {
        const u = new URL(location.href);
        u.search = "";
        controls.forEach((el) => {
          const v = el.value;
          if (v && v !== (el.options ? el.options[0].value : "")) u.searchParams.set(el.name, v);
        });
        history.replaceState(null, "", u);
      }
    }
    if (controls.some((el) => el.closest("details") && params.has(el.name))) $("details", form).open = true;
    form.hidden = false;
    apply(false);
    form.addEventListener("input", () => apply(true));
    form.addEventListener("change", () => apply(true));
    form.addEventListener("reset", () => { setTimeout(() => apply(true), 0); });
    form.addEventListener("submit", (e) => { e.preventDefault(); apply(true); });
  }
  search();

  $("#borrar")?.addEventListener("click", () => {
    fallback.clear();
    try {
      keys.forEach((key) => localStorage.removeItem(prefix + key));
      sessionStorage.removeItem(prefix + "sesion");
    } catch { /* El navegador no permitió el almacenamiento. */ }
    reflectPrefs();
    announce("Se han borrado los datos locales de RADAR 9.");
    const panel = $(".reset-panel");
    panel.open = false;
    $("summary", panel).focus();
  });
  function refreshLocal() {
    reflectPrefs();
    reflectMarks();
    resume();
    refreshPlan();
    if ($("#favoritos-lista")) showFavorites();
  }
  addEventListener("storage", (e) => {
    if (e.key === null || e.key.startsWith(prefix)) refreshLocal();
  });
  addEventListener("pageshow", (e) => {
    if (e.persisted) refreshLocal();
  });
})();
