"""Bilder und andere Medien, die das Repo nicht mitliefert.

Die Bilder von M10 stammen von Pixabay. Ob ein Uploader dort die Rechte an
einem Bild hatte, prüft Pixabay nicht, und die Lizenzbedingungen haben sich
über die Jahre mehrfach geändert. Das Repo verteilt die Dateien deshalb nicht
selbst, sondern nennt sie in `data/media.json`: je Datei die Pixabay-Seite, den
Uploader und die SHA-256-Prüfsumme der Fassung, gegen die gemessen wird.

    uv run python -m german_seo_bench.media

holt fehlende Dateien über die Pixabay-API und prüft jede vorhandene gegen
ihre Prüfsumme. Den Schlüssel gibt es kostenlos mit einem Pixabay-Konto
(https://pixabay.com/api/docs/); erwartet wird er in `PIXABAY_API_KEY`, aus der
Umgebung oder aus einer `.env` im Arbeitsverzeichnis.

Die Dateien liegen in `bench-media/` im Arbeitsverzeichnis, neben dem
`logs/`, das Inspect dort anlegt — nicht im Paket und nicht in einem
versteckten Verzeichnis je Benutzer. Im Paket wären sie beim nächsten Update
weg: Über `uvx` ist es eine Installation im Cache von uv, die mit jeder neuen
Fassung neu entsteht. Ein verstecktes Datenverzeichnis überlebte das zwar,
sammelte aber Dateien an einer Stelle, die niemand ansieht und niemand
aufräumt. `GERMAN_SEO_BENCH_MEDIA` legt das Verzeichnis woanders hin, etwa für
alle, die aus wechselnden Ordnern starten.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import version
from pathlib import Path

MANIFEST = "media.json"
ENV_DIR = "GERMAN_SEO_BENCH_MEDIA"
DEFAULT_DIR = "bench-media"
ENV_KEY = "PIXABAY_API_KEY"
PIXABAY_API = "https://pixabay.com/api/"


@dataclass(frozen=True)
class MediaFile:
    path: str
    source: str
    id: int
    page_url: str
    author: str
    sha256: str


def manifest() -> list[MediaFile]:
    raw = resources.files("german_seo_bench.data").joinpath(MANIFEST).read_text(encoding="utf-8")
    return [MediaFile(**entry) for entry in json.loads(raw)["files"]]


def media_dir() -> Path:
    """Bei jedem Aufruf neu gelesen, damit ein Test die Umgebung umsetzen kann.

    Immer absolut: Der Pfad steht in Meldungen und geht an Inspect, und
    beides soll auch dann eindeutig sein, wenn jemand aus einem anderen Ordner
    startet als dem, in dem die Bilder liegen.
    """
    configured = os.environ.get(ENV_DIR)
    return (Path(configured) if configured else Path.cwd() / DEFAULT_DIR).resolve()


def media_path(name: str) -> str:
    """Absoluter Pfad einer Mediendatei. `str`, weil `ContentImage.image` einen erwartet."""
    return str(media_dir() / name)


def missing(names: list[str]) -> list[str]:
    return [name for name in names if not (media_dir() / name).is_file()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get(url: str) -> bytes:
    # Ohne eigenen User-Agent lehnt Pixabay den Bildabruf mit 403 ab; die
    # API-Anfrage selbst ginge durch, und der Fehler sähe aus wie ein
    # ungültiger Schlüssel.
    request = urllib.request.Request(
        url, headers={"User-Agent": f"german-seo-bench/{version('german-seo-bench')}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _fetch_pixabay(entry: MediaFile, key: str) -> bytes:
    # `webformatURL` ist die Fassung mit 640 Pixeln an der langen Seite, gegen
    # die die Prüfsummen notiert sind. Die URL gilt nur 24 Stunden und wird
    # deshalb bei jedem Abruf neu erfragt statt im Manifest gespeichert.
    query = urllib.parse.urlencode({"key": key, "id": entry.id})
    hits = json.loads(_get(f"{PIXABAY_API}?{query}"))["hits"]
    if not hits:
        raise LookupError("bei Pixabay nicht mehr vorhanden")
    return _get(hits[0]["webformatURL"])


def _describe(error: Exception) -> str:
    """Die Fehlermeldung ohne die URL — in ihr stünde der Schlüssel."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}" + (" (Schlüssel ungültig?)" if error.code in (400, 401) else "")
    if isinstance(error, urllib.error.URLError):
        return f"nicht erreichbar ({error.reason})"
    return str(error)


def sync(key: str | None, out=sys.stdout) -> int:
    """Holt, was fehlt, und prüft alles. Rückgabe ist der Exit-Code.

    Eine Datei mit abweichender Prüfsumme bleibt liegen — die Aufgabe läuft
    damit —, aber der Exit-Code ist dann 1: Der Lauf mäße auf anderen Bytes
    als die Referenz, und das muss auffallen, bevor jemand Zahlen vergleicht.
    Pixabay kann eine Fassung neu kodieren; dann steht hier, welche.
    """
    target = media_dir()
    entries = manifest()
    todo = [entry for entry in entries if not (target / entry.path).is_file()]

    print(f"Medienverzeichnis: {target}", file=out)
    if todo and not key:
        print(
            f"{len(todo)} von {len(entries)} Dateien fehlen, und {ENV_KEY} ist nicht "
            "gesetzt.\nEinen kostenlosen Schlüssel gibt es mit einem Pixabay-Konto: "
            "https://pixabay.com/api/docs/",
            file=out,
        )
        return 1

    failed: list[str] = []
    for entry in todo:
        if entry.source != "pixabay":
            failed.append(f"{entry.path}: unbekannte Quelle {entry.source!r}")
            continue
        try:
            data = _fetch_pixabay(entry, key)
        except Exception as error:  # noqa: BLE001 — jeder Fehler wird je Datei gemeldet
            failed.append(f"{entry.path}: {_describe(error)} — {entry.page_url}")
            continue
        path = target / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Erst vollständig schreiben, dann umbenennen: Ein abgebrochener
        # Download hinterließe sonst eine Datei, die als vorhanden gilt.
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        partial.replace(path)
        print(f"  geholt   {entry.path}", file=out)

    changed = [
        entry.path
        for entry in entries
        if (target / entry.path).is_file() and _sha256(target / entry.path) != entry.sha256
    ]
    for path in changed:
        print(f"  ABWEICHEND  {path} — andere Bytes als die Referenzfassung", file=out)
    for line in failed:
        print(f"  FEHLER   {line}", file=out)

    ok = len(entries) - len(failed) - len(changed)
    print(f"{ok} von {len(entries)} Dateien vorhanden und unverändert.", file=out)
    return 0 if not failed and not changed else 1
