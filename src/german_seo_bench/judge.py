"""`python -m german_seo_bench.judge` — die Titel eines M01-Laufs von einem Judge werten.

Der Judge ist ein **eigener Schritt nach dem Lauf**, nicht Teil davon. Hinge er
als Option an M01, hätte das drei Nachteile:

- Ein Lauf ließe sich nicht nachträglich von einem neuen Judge werten.
- Kandidat und Judge müssten gleichzeitig auf dem Inferenzrechner liegen. Mit
  zwei lokalen Modellen auf einer Maschine ist das oft nicht möglich.
- Mehrere Judges für denselben Lauf gingen nur mit mehreren Läufen.

Stattdessen liest dieses Modul die Titel aus dem Log und legt die Urteile in eine
**Sidecar-Datei** daneben: `X.eval` → `X.judges.json`. Das Log selbst bleibt
unverändert. Jeder Judge ist dort ein eigener Eintrag unter `judges`, und ein
weiterer Aufruf mit einem anderen Judge fügt einen hinzu.

Warum nicht Inspects `inspect score`, das genau fürs Nachwerten gebaut ist? Es
schreibt immer ein vollständiges `.eval`-Log, also entweder eine Kopie neben
das Original oder eine geänderte Messdatei. Kopien würden `report.py` doppelt
zählen, und an einer Messdatei wird nachträglich nichts geändert.

Prompt und Auslesen kommen aus `checks/judge.py`. Die Kalibrierung
(`m01_judge`) nutzt dieselben Funktionen, sonst sagte sie nichts über diesen
Judge aus.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.model import get_model

from german_seo_bench.checks.judge import JUDGE_VERSION, UNUSABLE, judge_prompt, parse_verdict
from german_seo_bench.config import MAX_CONNECTIONS, run_config
from german_seo_bench.server import endpoint_record, probe_server

# Welche Aufgabe Titel liefert. Ein Judge über die Antworten von M02 oder
# M03 bewertete Buchstaben und JSON mit einer Rubrik für Seitentitel.
TASK = "m01_titles"


class JudgeError(Exception):
    """Der Judge kann diesen Lauf nicht werten. Der Text sagt, warum."""


class SelfJudgeError(JudgeError):
    """Eigene Klasse, damit ein Stapelskript diesen Fall überspringen statt melden kann."""


def sidecar_path(log_path: Path) -> Path:
    """`X.eval` → `X.judges.json`. Nicht `.eval`, damit die Auswertung sie übergeht."""
    return log_path.with_suffix(".judges.json")


def load_titles(log: EvalLog) -> list[dict[str, Any]]:
    """Die Titel des Laufs, je Datensatz und Epoche, in Log-Reihenfolge."""
    return [
        {"id": sample.id, "epoch": sample.epoch, "title": sample.output.completion.strip()}
        for sample in log.samples or []
    ]


def check_log(log: EvalLog, judge: str) -> None:
    """Bricht ab, wenn dieser Lauf nicht mit diesem Judge gewertet werden kann."""
    task = log.eval.task.rsplit("/", 1)[-1]
    if task != TASK:
        raise JudgeError(f"{log.location}: Aufgabe {task!r}, der Judge wertet nur {TASK}")
    if log.status != "success" or not log.samples:
        raise JudgeError(f"{log.location}: Lauf ohne Ergebnis ({log.status})")
    if log.eval.model == judge:
        # Richter und Kandidat dürfen nicht dasselbe Modell sein: Ein Modell
        # bewertet eigene Formulierungen erwiesenermaßen zu gut
        # (Self-Preference-Bias).
        raise SelfJudgeError(
            f"Judge und bewertetes Modell sind beide {judge!r}. Ein Modell "
            "bewertet eigene Titel zu gut — bitte ein anderes Judge-Modell wählen."
        )


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Anteil „unbrauchbar“ nur über abgegebene Urteile.

    Ein fehlendes Urteil ist weder Freispruch noch Verwerfung, dieselbe Regel
    wie für die Kalibrierungsmetriken (`NO_VERDICT` in metrics.py). Ohne ein einziges Urteil steht `null` da, nicht 0.
    """
    titled = [s for s in samples if s["title"]]
    judged = [s["verdict"] for s in titled if s["verdict"] is not None]
    return {
        "unusable_rate": sum(v == UNUSABLE for v in judged) / len(judged) if judged else None,
        "parsed": len(judged) / len(titled) if titled else None,
        "judged": len(judged),
        "titles": len(titled),
    }


# Wird nach jedem Titel aufgerufen: (erledigt, gesamt, Ergebnis des Titels).
Progress = Callable[[int, int, dict[str, Any]], None]


async def judge_titles(
    titles: list[dict[str, Any]],
    judge: str,
    base_url: str | None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Fragt den Judge je Titel und gibt den Eintrag für den Sidecar zurück.

    Die Laufbedingungen werden aufgezeichnet wie bei jeder Aufgabe: Wohin
    gefragt wird, VOR der ersten Anfrage, und was der Server geladen hatte,
    NACH der letzten. So steht auch für den Judge fest, ob unter seinem Alias
    wirklich das Modell lief, das man meint (siehe server.py).
    """
    model = get_model(judge, base_url=base_url, config=run_config())
    resolved_url = getattr(model.api, "base_url", None)
    endpoint = endpoint_record(judge, resolved_url)

    limit = asyncio.Semaphore(MAX_CONNECTIONS)
    done = 0

    async def one(entry: dict[str, Any]) -> dict[str, Any]:
        nonlocal done
        if not entry["title"]:
            # Kein Titel, kein Urteil. Dass hier nichts kam, zeigt `score`.
            result = {**entry, "verdict": None, "answer": None}
        else:
            async with limit:
                output = await model.generate(judge_prompt(entry["title"]))
            result = {
                **entry,
                "verdict": parse_verdict(output.completion),
                "answer": output.completion,
            }
        # Gezählt wird in Reihenfolge der Fertigstellung, nicht der Titel:
        # Bei mehr als einer Verbindung kommen sie durcheinander zurück, und
        # die Anzeige soll den Fortschritt zeigen, nicht die Position.
        done += 1
        if progress:
            progress(done, len(titles), result)
        return result

    samples = await asyncio.gather(*(one(entry) for entry in titles))

    if resolved_url:
        server = await probe_server(resolved_url, getattr(model.api, "model_name", judge))
    else:
        server = {"note": "Provider ohne base_url — nichts abzufragen"}

    return {
        "judge_version": JUDGE_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": endpoint,
        "server": server,
        "summary": summarize(samples),
        "samples": samples,
    }


def read_sidecar(path: Path, log: EvalLog, log_name: str) -> dict[str, Any]:
    """Vorhandenen Sidecar lesen, oder ein leeres Gerüst für diesen Lauf.

    Gehört ein vorhandener Sidecar zu einem anderen Lauf (andere `eval_id`),
    wird abgebrochen. Das passiert, wenn eine `.eval` ersetzt wurde und die
    alte Datei daneben liegen geblieben ist. Die Urteile darin gehören dann zu
    anderen Titeln.
    """
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("eval_id") != log.eval.eval_id:
            raise JudgeError(
                f"{path.name} gehört zu einem anderen Lauf (eval_id "
                f"{data.get('eval_id')!r} statt {log.eval.eval_id!r})"
            )
        return data
    return {
        "log": log_name,
        "eval_id": log.eval.eval_id,
        "task": TASK,
        "task_version": log.eval.task_version,
        "model": log.eval.model,
        "judges": {},
    }


def write_sidecar(path: Path, data: dict[str, Any]) -> None:
    """Atomar schreiben: Ein Abbruch mittendrin hinterlässt die alte Datei, keine halbe."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        # mkstemp legt die Datei immer mit 0600 an, und das Umbenennen behält
        # das bei. Der Sidecar läge dann mit -rw------- neben Logs mit
        # -rw-r--r--, für andere Konten unlesbar. Gesetzt werden die
        # Rechte, die ein gewöhnliches open() unter der aktuellen umask ergäbe.
        umask = os.umask(0)
        os.umask(umask)
        os.fchmod(handle, 0o666 & ~umask)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def run(
    log_path: Path,
    judge: str,
    base_url: str | None = None,
    progress: Progress | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    """Wertet ein Log mit einem Judge. Rückgabe: Sidecar, Eintrag, ob er einen alten ersetzt hat."""
    log = read_eval_log(str(log_path))
    check_log(log, judge)
    path = sidecar_path(log_path)
    data = read_sidecar(path, log, log_path.name)
    entry = asyncio.run(judge_titles(load_titles(log), judge, base_url, progress))
    replaced = judge in data["judges"]
    data["judges"][judge] = entry
    write_sidecar(path, data)
    return path, entry, replaced


def print_progress(done: int, total: int, result: dict[str, Any]) -> None:
    """Eine Zeile je Titel: Zähler, Urteil, Titel.

    Eine Zeile je Titel und keine, die sich per `\r` überschreibt: So bleibt
    der Verlauf in tmux und in einer umgeleiteten Ausgabe lesbar, und das
    Urteil steht gleich dabei. Mit dem Titel daneben sieht man früh, ob der Judge
    sinnvoll urteilt, statt erst nach dem ganzen Lauf. Auf stderr, damit stdout
    nur die Zusammenfassung trägt.
    """
    width = len(str(total))
    if not result["title"]:
        mark = "·"  # leerer Titel, nicht gefragt
    elif result["verdict"] is None:
        mark = "?"  # Antwort ohne eindeutiges Urteil
    else:
        # ✗ für verworfen, damit die Ausreißer beim Überfliegen auffallen.
        mark = "✗" if result["verdict"] == UNUSABLE else "✓"
    title = result["title"] or "(leer)"
    if len(title) > 70:
        title = title[:69] + "…"
    print(f"  [{done:>{width}}/{total}] {mark}  {title}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.judge",
        description="Wertet die Titel eines fertigen M01-Laufs mit einem Judge-Modell "
        "und legt die Urteile neben das Log (X.eval → X.judges.json). Das Log bleibt "
        "unverändert; jeder weitere Judge kommt als eigener Eintrag dazu.",
    )
    parser.add_argument("log", type=Path, help="die .eval-Datei eines M01-Laufs")
    parser.add_argument("--judge", required=True,
                        help="Judge-Modell, z. B. local/gemma-4-12b oder anthropic/claude-opus-5")
    parser.add_argument("--judge-base-url",
                        help="Adresse des Judge-Servers; ohne Angabe gilt für local/… "
                        "LOCAL_BASE_URL, sonst localhost")
    args = parser.parse_args(argv)

    try:
        path, entry, replaced = run(
            args.log, args.judge, args.judge_base_url, progress=print_progress
        )
    except SelfJudgeError as error:
        print(f"Übersprungen: {error}", file=sys.stderr)
        # Eigener Rückgabewert, damit ein Stapelskript diesen Fall von einem
        # Fehler unterscheiden kann.
        return 3
    except JudgeError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    summary = entry["summary"]
    rate = "—" if summary["unusable_rate"] is None else f"{summary['unusable_rate']:.0%}"
    print(
        f"{args.judge}: {rate} unbrauchbar, aus {summary['judged']} von {summary['titles']} "
        "Titeln mit Urteil "
        f"→ {path.name}" + (" (vorheriges Urteil dieses Judges ersetzt)" if replaced else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
