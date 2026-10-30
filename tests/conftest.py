"""Gemeinsame Helfer für die Testläufe."""

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from german_seo_bench.media import ENV_DIR, manifest

# Die Tests laufen gegen ein leeres Medienverzeichnis, nicht gegen das des
# Entwicklers. Sonst bestünde ein Test, der die Bilder stillschweigend
# voraussetzt, auf jedem Rechner, auf dem sie schon liegen — und schlüge erst
# auf einem frischen Checkout fehl, also dort, wo `uv run pytest` ohne Netz
# laufen soll.
os.environ[ENV_DIR] = tempfile.mkdtemp(prefix="gsb-media-empty-")


@pytest.fixture(scope="module")
def media_present(tmp_path_factory):
    """Ein Medienverzeichnis, in dem jede Datei aus dem Manifest liegt.

    Platzhalter, keine Bilder: Geprüft wird hier nur, ob eine Aufgabe ihre
    Dateien findet. Ob es die richtigen sind, prüft `media.sync` über die
    Prüfsumme.
    """
    folder = tmp_path_factory.mktemp("media")
    for entry in manifest():
        path = folder / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff\xd9")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(ENV_DIR, str(folder))
        yield Path(folder)


def assert_prompt_corridor(
    task: str,
    len_min: int,
    len_max: int,
    prompt_min: int,
    prompt_max: int,
    oracles: list[tuple[str, str]],
) -> None:
    """Die Regel für Längenvorgaben, für alle Aufgaben an einer Stelle.

    Der Prompt nennt einen **engeren** Korridor als die Wertung prüft. Warum,
    steht in `docs/architecture.md`: Ein Sprachmodell zählt
    keine Zeichen, es schätzt sie aus Tokens — eine Zeichengrenze als harte
    Prüfung misst also die Schätzgüte mit, und unter einer UND-Wertung schlägt
    das voll auf die Leitzahl durch.

    Zugesichert werden drei Dinge, und jedes hat seinen eigenen Fehlerfall:

    1. **Der Prompt-Korridor liegt im Prüfkorridor.** Andersherum — die
       Anweisung weiter als die Prüfung — wäre eine Falle: Die Aufgabe
       erlaubte etwas, das sie anschließend bestraft.
    2. **Es gibt überhaupt einen Abstand.** Sind beide Korridore gleich, ist
       die Regel zwar nicht verletzt, aber auch nicht angewendet — und das
       fiele beim Lesen der Konstanten nicht auf.
    3. **Die Musterlösungen halten den engeren Korridor ein.** Sonst verlangt
       die Anweisung etwas, das die eigene Referenz nicht einhält. Diese
       Zusicherung ist der Grund, warum M01 den Abstand nach oben über die
       Prüfgrenze holt statt über den Prompt: Dort liegen die Musterlösungen
       bei 47 bis 60 Zeichen.

    Args:
        task: Name für die Fehlermeldung.
        len_min, len_max: der Prüfkorridor.
        prompt_min, prompt_max: was die Anweisung nennt.
        oracles: `(id, musterlösung)` je Datensatz.
    """
    assert len_min <= prompt_min, f"{task}: Prompt-Untergrenze unter dem Prüfkorridor"
    assert prompt_max <= len_max, f"{task}: Prompt-Obergrenze über dem Prüfkorridor"
    assert (len_min, len_max) != (prompt_min, prompt_max), (
        f"{task}: Prompt- und Prüfkorridor sind identisch — die Regel ist nicht "
        "angewendet. Entweder einen Abstand setzen oder die Ausnahme begründen."
    )
    draussen = [
        (sid, len(text))
        for sid, text in oracles
        if not prompt_min <= len(text) <= prompt_max
    ]
    assert not draussen, (
        f"{task}: Musterlösungen außerhalb des Prompt-Korridors "
        f"{prompt_min}–{prompt_max}: {draussen}"
    )


def group_names(log: Any, group: str) -> set[str]:
    """Die Gruppennamen eines Profils aus dem Log, ohne Registry-Präfix.

    Inspect setzt als Metriknamen im Log den vollen Registry-Namen
    (`registry_log_name` in `_eval/task/results.py`). Für Metriken aus einem
    Paket ist das `german_seo_bench/by_level_1`, für Inspects eigene nur
    `mean` — das Präfix fällt nur für `inspect_ai` weg. Hier wird deshalb ein beliebiges
    Paketpräfix abgeschnitten statt eines bekannten: Die Zusicherung gilt dem
    Bestand der Gruppen, nicht dem Ort, an dem die Metrik registriert ist.
    Sonst meldete der Test eine intakte Aufschlüsselung als fehlend.
    """
    score = next(s for s in log.results.scores if s.name == "score")
    prefix = f"by_{group}_"
    return {
        name.rsplit("/", 1)[-1].removeprefix(prefix)
        for name in score.metrics
        if name.rsplit("/", 1)[-1].startswith(prefix)
    }


def group_metrics(log: Any, group: str) -> dict[str, float]:
    """Wie `group_names`, aber mit den Werten — für die Zusicherung auf 1.00."""
    score = next(s for s in log.results.scores if s.name == "score")
    prefix = f"by_{group}_"
    return {
        name: metric.value
        for name, metric in score.metrics.items()
        if name.rsplit("/", 1)[-1].startswith(prefix)
    }
