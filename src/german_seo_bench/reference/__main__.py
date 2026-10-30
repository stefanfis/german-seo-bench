"""Die Referenz von A01 erzeugen und gegen die abgelegte halten.

    uv run python -m german_seo_bench.reference                 # rendern und vergleichen
    uv run python -m german_seo_bench.reference --capture c.json  # ohne Docker, vorhandenes Capture
    uv run python -m german_seo_bench.reference --write         # Abweichung übernehmen

**Warum Vergleichen die Vorgabe ist und Schreiben der Ausnahmefall.** Die
Referenz ist die Behauptung, gegen die jede Zahl dieser Aufgabe entsteht. Ein
Werkzeug, das sie bei jedem Lauf stillschweigend überschreibt, macht jede
Abweichung zur Nebensache. Der Oracle-Lauf hilft dabei nicht: Er prüft die Referenz gegen sich selbst und zeigt auch
dann 1.00, wenn sie zur Prüflandschaft nicht mehr passt.

Der Vergleich ist deshalb der eigentliche Zweck dieses Moduls, nicht das
Schreiben: Er sagt, ob die eingefrorene Referenz noch das ist, was ein Browser
auf der ausgelieferten Site findet.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from german_seo_bench.dataset import data_path
from german_seo_bench.reference.audit import audit

DATA = "a01_browser_audit.json"
COMPOSE = "a01_browser_audit/reference.compose.yaml"


def frozen_reference() -> tuple[list[str], set[tuple[str, str]]]:
    """Seitenliste und eingefrorene Befunde aus dem Datensatz."""
    doc = json.loads(Path(data_path(DATA)).read_text(encoding="utf-8"))
    sample = doc["samples"][0]
    findings = {
        (row["page"], row["issue"]) for row in sample["reference"]["findings"]
    }
    return sample["input"]["pages"], findings


def render(pages: list[str], out_dir: Path) -> Path:
    """Das Capture im Container erzeugen. Braucht Docker.

    Die Seitenliste wird übergeben und nicht im Container aus dem Verzeichnis
    gelesen: Sechs Adressen der Prüflandschaft haben gar keine Datei
    (Weiterleitungen, kontextabhängige Auslieferung), und eine zweite
    Seitenliste neben der des Datensatzes liefe irgendwann auseinander.
    """
    compose = Path(data_path(COMPOSE))
    env = {
        **os.environ,
        "BENCH_PAGES": json.dumps(pages),
        "BENCH_CAPTURE_DIR": str(out_dir),
    }
    subprocess.run(
        [
            "docker", "compose", "-f", str(compose),
            "run", "--rm", "--build", "referencegen",
        ],
        check=True,
        env=env,
        cwd=compose.parent,
    )
    subprocess.run(
        ["docker", "compose", "-f", str(compose), "down", "-v"],
        check=False,
        env=env,
        cwd=compose.parent,
    )
    return out_dir / "capture.json"


def compare(
    computed: list[dict[str, str]], frozen: set[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """(im Capture, aber nicht in der Referenz | in der Referenz, aber nicht im Capture)."""
    gerechnet = {(row["page"], row["issue"]) for row in computed}
    neu = sorted(f"{page}/{issue}" for page, issue in gerechnet - frozen)
    fehlend = sorted(f"{page}/{issue}" for page, issue in frozen - gerechnet)
    return neu, fehlend


def write_reference(computed: list[dict[str, str]]) -> None:
    path = Path(data_path(DATA))
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["samples"][0]["reference"]["findings"] = sorted(
        computed, key=lambda row: (row["page"], row["issue"])
    )
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.reference",
        description="Die Referenz von A01 aus einem Render-Capture rechnen.",
    )
    parser.add_argument(
        "--capture",
        type=Path,
        help="Vorhandenes Capture auswerten, statt neu zu rendern (kein Docker nötig).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Verzeichnis für das erzeugte Capture (Vorgabe: ein temporäres).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Die gerechneten Befunde in den Datensatz übernehmen. "
        "Danach `version` in a01_browser_audit.json von Hand hochzählen — "
        "eine geänderte Referenz ist eine geänderte Aufgabe.",
    )
    args = parser.parse_args(argv)

    pages, frozen = frozen_reference()
    if args.capture:
        capture_path = args.capture
    else:
        out_dir = args.out or Path(tempfile.mkdtemp(prefix="a01-capture-"))
        out_dir.mkdir(parents=True, exist_ok=True)
        capture_path = render(pages, out_dir)

    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    computed = audit(capture)

    fehlende_seiten = sorted(set(pages) - set(capture.get("pages", {})))
    if fehlende_seiten:
        # Eine Seite, die nicht gerendert wurde, hat im Capture keine Befunde —
        # und der Vergleich läse das als „Referenz zu viel". Ursache vor Folge.
        print(f"Im Capture fehlen Seiten: {fehlende_seiten}", file=sys.stderr)
        return 2

    neu, fehlend = compare(computed, frozen)
    print(f"Capture: {capture_path}")
    print(f"gerechnet: {len(computed)} Befunde, eingefroren: {len(frozen)}")
    for eintrag in neu:
        print(f"  + {eintrag}   (Capture findet es, die Referenz kennt es nicht)")
    for eintrag in fehlend:
        print(f"  - {eintrag}   (Referenz behauptet es, das Capture findet es nicht)")

    if not neu and not fehlend:
        print("Referenz und Prüflandschaft stimmen überein.")
        return 0
    if args.write:
        write_reference(computed)
        print(
            "Referenz überschrieben. Jetzt `version` in "
            "data/a01_browser_audit.json hochzählen."
        )
        return 0
    print(
        "Abweichung — nichts geschrieben. Mit --write übernehmen, wenn die "
        "Prüflandschaft absichtlich geändert wurde.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
