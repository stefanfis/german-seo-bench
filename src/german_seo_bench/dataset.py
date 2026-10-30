"""Datensätze laden — und dabei die Referenz vom Prompt fernhalten.

Die Regel, an der die Aussagekraft des ganzen Benchmarks hängt: Die
Prompt-Funktion bekommt **nur** den `input`-Teil eines Datensatzes. Sie kann die
Referenz nicht erreichen, weil sie sie nicht übergeben bekommt — nicht aus
Disziplin, sondern weil die Bauweise es verhindert.

Der `reference`-Teil landet in `Sample.metadata` und damit ausschließlich im
Log und beim Scorer. Er wird nie Teil einer Nachricht an das Modell.

`Sample.target` bleibt absichtlich leer: In M01 gibt es keine richtige Antwort,
gegen die verglichen wird. Die Musterlösung in `reference.oracle` dient allein
dem Oracle-Lauf (siehe `oracle.py`) und geht in keine Wertung ein.

In M01 steht das Zielkeyword deshalb in `reference` (`required`) und nicht in
`input`: Das Modell bekommt es gar nicht zu sehen und muss das Thema aus dem
Seiteninhalt erschließen.
"""

import json
from importlib import resources
from typing import Any, Callable

from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessage

# Eine Prompt-Funktion darf statt eines Textes auch eine fertige Nachrichtenliste
# liefern. Nötig für M10: Ein ALT-Text-Datensatz ist ein Bild, und ein
# Bild geht nur als `ContentImage` in einer `ChatMessageUser` an das Modell.
#
# Bewusst dieselbe Ladefunktion und keine zweite daneben. Die eine Regel, an der
# hier alles hängt — `prompt()` sieht nur `input` —, wäre sonst zweimal
# implementiert, und die zweite Fassung müsste sie jedes Mal neu einhalten.
PromptBuilder = Callable[[dict[str, Any]], "str | list[ChatMessage]"]


def load_dataset(filename: str, prompt: PromptBuilder) -> MemoryDataset:
    """Liest `data/<filename>` und baut daraus die Samples.

    Args:
        filename: Dateiname unterhalb von `german_seo_bench/data`.
        prompt: Baut aus dem `input`-Teil das, was das Modell sieht — einen
            Text oder eine Nachrichtenliste (für Bilder).
    """
    raw = resources.files("german_seo_bench.data").joinpath(filename).read_text(encoding="utf-8")
    doc = json.loads(raw)

    samples = [
        Sample(
            id=record["id"],
            input=prompt(record["input"]),
            metadata={**record["input"], **record["reference"]},
        )
        for record in doc["samples"]
    ]
    return MemoryDataset(
        samples=samples,
        name=doc["task"],
        location=filename,
    )


def data_path(name: str) -> str:
    """Absoluter Pfad einer mitgelieferten Datei unterhalb von `german_seo_bench/data`.

    A01 braucht das für `compose.yaml` und die Prüflandschaft darunter. Ein
    relativer Pfad stimmt nur, solange jemand im richtigen Verzeichnis steht,
    und über `uvx` ohne Checkout steht dort niemand. Die Bilder von M10 liefert
    das Paket nicht mit; ihren Pfad kennt `german_seo_bench.media`.

    Bewusst hier und nicht in der Aufgabe: `resources.files("german_seo_bench.data")`
    steht damit an einer einzigen Stelle. Die Rückgabe ist ein `str`, weil
    `Task(sandbox=("docker", …))` einen erwartet.
    """
    return str(resources.files("german_seo_bench.data").joinpath(name))


def dataset_version(filename: str) -> int:
    """Aufgabenversion aus der Datendatei.

    Wandert in `Task.version` und damit in jedes Log. Sobald sich Prompt oder
    Prüfung ändern, wird sie hochgezählt — sonst vergleicht man später Zahlen
    aus zwei verschiedenen Aufgaben unter einem Namen.
    """
    raw = resources.files("german_seo_bench.data").joinpath(filename).read_text(encoding="utf-8")
    return int(json.loads(raw)["version"])
