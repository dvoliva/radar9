"""Una edición por día de Santiago. Los fallos de adquisición no son días sin noticias."""
import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from .classify import classify, norm
from .editor import BudgetLimit, EditorialError, Ledger, OpenAI, draft, money, profile, suspicious_document
from .quality import authorized, check_article, validate
from .store import ROOT, SANTIAGO, Invalid, day, instant, load, records, stamp, uid, write


def log(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=True), flush=True)


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise Invalid("Ya hay una actualización local o un bloqueo pendiente de recuperación") from exc
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def checkpoint(path):
    # En el runner efímero, no iniciar un POST hasta que la reserva esté en main.
    relative = path.relative_to(ROOT).as_posix()
    if not (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and relative.startswith("data/usage/")):
        raise Invalid("La reserva remota solo está permitida en el workflow de main")
    for args in (["add", "--", relative], ["commit", "-m", "Reserve daily editorial usage", "--", relative],
                 ["push", "origin", "HEAD:refs/heads/main"]):
        subprocess.run(["git", *args], cwd=ROOT, check=True)


def refresh(root, now, discover, source_exception, *, provider=None, remote_budget=False, follow=()):
    validate(root, now=now)
    today = now.astimezone(SANTIAGO).date()
    key, ts = today.isoformat(), stamp(now)
    data = root / "data"
    sources = {s["id"]: s for s in load(root / "config/sources.json")}
    articles = records(data / "articles")
    editions = records(data / "editions")
    if editions and key < max(editions):
        raise Invalid("No se puede publicar hacia atrás en el histórico")
    known = {a["references"][0]["url"] for a in articles.values()}
    previously_listed = {item["id"] for e in editions.values() for item in e["items"]}
    if not set(follow) <= previously_listed or len(set(follow)) != len(follow):
        raise Invalid("El seguimiento debe referirse a artículos ya publicados y distintos")
    if key in editions and follow:
        raise Invalid("La edición del día ya está cerrada")
    status_file, detections_file = data / "status.json", data / "detections.json"
    status = load(status_file) if status_file.exists() else {"last_run": None, "sources": {}}
    detected = load(detections_file) if detections_file.exists() else {}
    metrics = Counter()
    queue = []
    observed = set(known)
    old_titles = {(sources[r["source_id"]]["org"], r["published_at"], norm(r["title"]))
                  for a in articles.values() for r in a["references"][:1]}
    documentary_checks = {}
    for a in articles.values():
        for r in a["references"]:
            sid = r["source_id"]
            if sources[sid]["kind"] == "documental":
                documentary_checks[sid] = max(documentary_checks.get(sid, ""), r["verified_at"])
    for sid, ts_check in documentary_checks.items():
        status["sources"][sid] = {"status": "verificada manualmente", "last_success": ts_check, "checked_at": ts_check}
    for src in sources.values():
        if not src["enabled"]:
            continue
        sid = src["id"]
        health = status["sources"].setdefault(sid, {"last_success": None})
        if health.get("last_success") and (now - instant(health["last_success"])).total_seconds() < int(src.get("poll_interval_seconds", 21600)):
            metrics["cached_sources"] += 1
            continue
        health["checked_at"] = ts
        try:
            candidates, warnings = discover(src, now, observed.copy(), limit=5)
        except source_exception as exc:
            missing_contact = src["kind"] == "sec-8k" and not os.getenv("RADAR9_SEC_CONTACT")
            health.update(status="requiere_contacto" if missing_contact else "no disponible")
            metrics["failed_sources"] += 1
            log("fuente_no_disponible", source=sid, reason=str(exc)[:160])
            continue
        health.update(status="parcial" if warnings else "disponible", last_success=ts)
        metrics["sources_ok"] += 1
        metrics["warnings"] += len(warnings)
        if warnings:
            log("fuente_parcial", source=sid, count=len(warnings), reason=str(warnings[0])[:160])
        for c in candidates:
            url = c["url"]
            try:
                age = (today - day(c["published_at"])).days
                if c["source_id"] != sid or not authorized(url, src["domains"]) or not 0 <= age <= 6:
                    raise Invalid("Dominio, identidad o fecha no admisibles")
                if len(c["body"].split()) < 40:
                    raise Invalid("Contenido insuficiente")
                if suspicious_document(c["title"] + "\n" + c["body"]):
                    raise Invalid("Señales de instrucciones en el documento")
            except Invalid:
                metrics["invalid"] += 1
                continue
            k, titlekey = uid(url), (src["org"], c["published_at"], norm(c["title"]))
            if url in observed or titlekey in old_titles:
                metrics["duplicates"] += 1
                continue
            observed.add(url)
            old_titles.add(titlekey)
            meta = classify(c, src, today)
            if not meta:
                metrics["outside_topics"] += 1
                continue
            digest = hashlib.sha256((c["title"] + "\n" + c["body"]).encode("utf-8")).hexdigest()
            info = detected.setdefault(k, {"first_seen": ts})
            if info.get("rejected_digest") == digest:
                metrics["previously_rejected"] += 1
                continue
            queue.append(({**c, "publisher": src["org"]}, meta, digest))
    metrics["eligible"] = len(queue)
    queue.sort(key=lambda e: (-e[1]["relevance"], -day(e[0]["published_at"]).toordinal(), e[0]["url"]))
    limit = int(os.environ.get("RADAR_MAX_ARTICLES", "4"))
    if not 0 <= limit <= 50:
        raise Invalid("RADAR_MAX_ARTICLES debe estar entre 0 y 50")
    ledger = Ledger(data / "usage" / (key + ".json"),
                    money(os.environ.get("RADAR_DAILY_USD", "0.75"), "presupuesto", zero=True), limit,
                    checkpoint=checkpoint if remote_budget else None)
    prices = load(root / "config/prices.json")
    ready = {}
    if provider:
        status["models"] = {}
        for tier in ("estándar", "profundo"):
            p = profile(tier, prices)
            try:
                provider.available(p.model)
            except EditorialError as exc:
                metrics["provider_errors"] += 1
                log("modelo_no_disponible", model=p.model, reason=str(exc))
                status["models"][tier] = {"name": p.model, "available": False, "checked_at": ts}
            else:
                ready[tier] = p
                status["models"][tier] = {"name": p.model, "available": True, "checked_at": ts}
    if key not in editions and ready and metrics["sources_ok"]:
        for c, meta, digest in queue:
            k = uid(c["url"])
            p = ready.get("profundo" if meta["depth"] == "profundo" else "estándar")
            if p is None:
                metrics["deferred"] += 1
                continue
            try:
                copy = draft(c, meta, p, ledger, provider)
                a = {"id": k, **copy, **meta, "published_at": c["published_at"],
                     "detected_at": detected[k]["first_seen"], "incorporated_at": ts, "method": "ia",
                     "references": [{"source_id": c["source_id"], "title": c["title"], "url": c["url"],
                                     "published_at": c["published_at"], "verified_at": ts}]}
                check_article(a, sources, now)
            except BudgetLimit:
                metrics["deferred"] += 1
                continue
            except Invalid as exc:
                detected[k]["rejected_digest"] = digest
                metrics["rejected"] += 1
                log("control_editorial", article=k, reason=str(exc))
                continue
            except EditorialError as exc:
                # Un problema del servicio no debe disparar una serie de POST.
                metrics["provider_errors"] += 1
                log("proveedor_no_disponible", reason=str(exc))
                break
            write(data / "articles" / (k + ".json"), a, immutable=True)
            articles[k] = a
            metrics["added"] += 1
    if metrics["added"]:
        status["last_ai_success"] = ts
    status.update(last_attempt=ts, enrichment="activo" if ready
                  and not metrics["provider_errors"] else "pendiente")
    if metrics["sources_ok"]:
        status["last_run"] = ts
    result = "edición ya cerrada" if key in editions else "sin edición nueva"
    if key not in editions and ready and metrics["sources_ok"]:
        available = sorted((a for k, a in articles.items() if k not in previously_listed and
                            0 <= (today - day(a["published_at"])).days <= 6),
                           key=lambda a: (-a["relevance"], a["id"]))
        items = [{"id": a["id"], "label": "nueva" if a["published_at"] == key else "reciente"} for a in available]
        items += [{"id": k, "label": "seguimiento"} for k in follow]
        incomplete = any(metrics[v] for v in ("failed_sources", "cached_sources", "warnings", "invalid", "provider_errors", "rejected",
                                               "deferred", "previously_rejected"))
        if items or not (incomplete or queue):
            if available:
                lead = available[0]
                briefing = lead["summary"]
                if len(available) > 1:
                    briefing += " Otros focos de la edición: " + "; ".join(a["title"] for a in available[1:4]) + "."
            elif follow:
                briefing = "Sin incorporaciones nuevas. Esta edición recupera análisis anteriores para darles seguimiento."
            else:
                briefing = "La revisión de las fuentes consultadas no encontró novedades verificables para incorporar."
            if incomplete:
                briefing += " La cobertura fue parcial; los contenidos no verificados quedaron fuera de esta edición."
            write(data / "editions" / (key + ".json"),
                  {"date": key, "published_at": ts, "briefing": briefing, "items": items}, immutable=True)
            result = "edición generada"
    usage = {"requests": len(ledger.rows),
             "input_tokens": sum(r.get("input_tokens", 0) for r in ledger.rows.values()),
             "output_tokens": sum(r.get("output_tokens", 0) for r in ledger.rows.values()),
             "estimated_usd": str(sum(Decimal(r.get("estimated_usd", 0)) for r in ledger.rows.values())),
             "pending_reserved_usd": str(sum(Decimal(r["reserved_usd"]) for r in ledger.rows.values()
                                             if "estimated_usd" not in r))}
    status.update(result=result, metrics=dict(metrics), usage=usage)
    if detected:
        write(detections_file, detected)
    write(status_file, status)
    validate(root, now=now)
    log("actualización", day=key, result=result, enrichment=status["enrichment"], usage=usage, **dict(metrics))
    if not (metrics["sources_ok"] or metrics["cached_sources"]) or metrics["provider_errors"] and not metrics["added"]:
        return 1
    return 0


def main():
    from .feeds import SourceError, discover

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-budget", action="store_true", help="Persistir reservas en GitHub Actions")
    parser.add_argument("--follow", action="append", default=[], metavar="ID")
    args = parser.parse_args()
    key = os.getenv("OPENAI_API_KEY")
    with exclusive(ROOT / ".radar/refresh.lock"):
        return refresh(ROOT, datetime.now(timezone.utc), discover, SourceError, provider=OpenAI(key) if key else None,
                       remote_budget=args.checkpoint_budget, follow=args.follow)


if __name__ == "__main__":
    raise SystemExit(main())
