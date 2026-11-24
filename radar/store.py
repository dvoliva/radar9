"""Archivos versionables: reemplazo atómico e inserciones históricas inmutables."""
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SANTIAGO = ZoneInfo("America/Santiago")


class Invalid(ValueError):
    """Datos inconsistentes: no construir ni publicar."""


def load(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write(path: Path, record, *, immutable=False) -> None:
    if immutable and path.exists():
        if load(path) != record:
            raise Invalid(f"No se puede reemplazar el registro histórico {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp",
                                       mode="w", encoding="utf-8", newline="\n")
    try:
        with temp:
            json.dump(record, temp, ensure_ascii=False, indent=2, allow_nan=False)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        if immutable:
            # No hay ventana entre comprobar existencia y reemplazar el histórico.
            os.link(temp.name, path)
        else:
            os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


def uid(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def instant(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise Invalid("Instante ISO no válido") from exc
    if dt.tzinfo is None:
        raise Invalid("Falta zona horaria en el instante")
    return dt


def day(value: str) -> date:
    try:
        d = date.fromisoformat(value)
        if d.isoformat() != value:
            raise ValueError
        return d
    except (TypeError, ValueError) as exc:
        raise Invalid("Fecha ISO no válida") from exc


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def records(directory: Path) -> dict:
    return {p.stem: load(p) for p in sorted(directory.glob("*.json"))}
