"""Laufdaten für den Bericht: Sampling, Laufzeit, Tempo und Kontext je Lauf.

Der Score sagt, wie gut ein Modell ist. Ob es sich lohnt, hängt auch daran, wie
lange es dafür braucht und wie viel Kontext der Server bereithalten muss. Alle
Angaben hier stehen schon im Log; gemessen oder gewertet wird nichts neu, und
keine dieser Zahlen geht in einen Score ein.

Woher jede Zahl kommt und wo sie endet:

    Sampling     `metadata.server.params` je Datensatz (`server.py`), also
                 was llama-server laut `/props` tatsächlich als Vorgabe hatte.
                 Setzt der Lauf selbst Werte (`--temperature` usw.), gelten
                 die, denn sie stehen in jeder Anfrage und überschreiben die
                 Vorgabe des Servers.
    Laufzeit     `stats.started_at` bis `stats.completed_at`, Wanduhr des
                 ganzen Laufs. In A01 steckt darin auch die Zeit von Browser
                 und Werkzeugen, nicht nur die des Modells.
    tok/s        `timings` aus der Rohantwort von llama-server: Tokens und
                 Millisekunden für Prompt und Generierung, so wie der Server
                 sie selbst misst. Netz, Warteschlange und Inspect zählen
                 nicht mit. Über Anfragen und Läufe wird nach Tokens
                 gewichtet (Summe Tokens durch Summe Zeit), nicht das Mittel
                 der Raten: Sonst zählte eine Antwort aus zwei Tokens so viel
                 wie eine aus vierhundert.
    Kontext      `usage.total_tokens` je Modellaufruf, also Prompt samt
                 Cache und Antwort. Das Maximum ist die Kontextgröße, die der
                 Server mindestens braucht, damit kein Aufruf abgeschnitten
                 wird.

Die Rohantwort hebt Inspect nur für die ersten Modellaufrufe eines Datensatzes
auf (Vorgabe: fünf). Bei Einzelanfragen ist das jeder Aufruf; in A01, wo ein
Agent Dutzende Aufrufe stellt, sind es nur die ersten. Wie viele Aufrufe in eine
Rate eingegangen sind, steht deshalb dabei, und der Bericht markiert jede Zelle,
in der es nicht alle sind. Die Token-Zählung steht dagegen an jedem Aufruf; der
Kontext stützt sich immer auf alle.

Fehlt eine Angabe — ein Server ohne `timings`, ein Lauf ohne Serverabfrage —,
bleibt sie leer. Eine geschätzte Zahl neben gemessenen sähe aus wie eine
gemessene.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from inspect_ai.log import EvalLog

from german_seo_bench.modelname import model_label

# Diese Sampler stehen immer im Bericht.
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p")

# Diese nur, wenn sie wirken. Der Wert daneben ist der, bei dem der Sampler
# nichts tut; ihn bei jedem Modell aufzuführen, verdeckte die Fälle, in denen
# jemand eine Strafe oder XTC wirklich eingeschaltet hat.
SAMPLING_NEUTRAL = {
    "repeat_penalty": 1.0,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "typical_p": 1.0,
    "xtc_probability": 0.0,
    "dry_multiplier": 0.0,
    "top_n_sigma": -1.0,
}

# Was ein Lauf selbst setzen kann (Inspects GenerateConfig) und wie das Feld
# bei llama-server heißt. `min_p` und die übrigen kennt GenerateConfig nicht;
# für sie gilt immer die Vorgabe des Servers.
REQUEST_KEYS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}


@dataclass(frozen=True)
class RunStats:
    """Was ein einzelner Lauf über sich selbst verrät."""

    file: str
    task: str
    model: str
    seconds: float | None
    # Summen über alle Aufrufe mit `timings` in der Rohantwort.
    prompt_tokens: int
    prompt_ms: float
    generated_tokens: int
    generated_ms: float
    timed_calls: int
    calls: int
    max_context: int | None
    # Das Kontextfenster des Servers (`n_ctx` aus `/props`). Erreicht
    # `max_context` es, ist mindestens ein Aufruf an die Grenze gestoßen.
    server_context: int | None
    # Je verschiedener Sampling-Einstellung im Lauf ein Eintrag, als Text.
    # Mehr als einer heißt: Der Server hat mitten im Lauf gewechselt.
    sampling: tuple[str, ...]


def run_stats(log: EvalLog, file: str) -> RunStats:
    """Die Laufdaten eines vollständig gelesenen Logs."""
    prompt_tokens = generated_tokens = timed = calls = 0
    prompt_ms = generated_ms = 0.0
    contexts: list[int] = []
    sampling: list[str] = []
    windows: list[int] = []
    requested = _requested(log)
    for sample in log.samples or []:
        server = (sample.metadata or {}).get("server")
        params = server.get("params") if isinstance(server, dict) else None
        if isinstance(server, dict) and isinstance(server.get("n_ctx"), int):
            windows.append(server["n_ctx"])
        if isinstance(params, dict):
            text = sampling_text({**params, **requested})
            if text not in sampling:
                sampling.append(text)
        for event in sample.events:
            if event.event != "model":
                continue
            calls += 1
            usage = event.output.usage if event.output else None
            if usage is not None and usage.total_tokens:
                contexts.append(usage.total_tokens)
            timings = _timings(event.call.response if event.call else None)
            if timings is not None:
                timed += 1
                prompt_tokens += timings["prompt_n"]
                prompt_ms += timings["prompt_ms"]
                generated_tokens += timings["predicted_n"]
                generated_ms += timings["predicted_ms"]
    return RunStats(
        file=file,
        task=log.eval.task.rsplit("/", 1)[-1],
        model=model_label(log),
        seconds=wall_seconds(log),
        prompt_tokens=prompt_tokens,
        prompt_ms=prompt_ms,
        generated_tokens=generated_tokens,
        generated_ms=generated_ms,
        timed_calls=timed,
        calls=calls,
        max_context=max(contexts) if contexts else None,
        server_context=min(windows) if windows else None,
        sampling=tuple(sampling),
    )


def _requested(log: EvalLog) -> dict[str, Any]:
    config = log.plan.config if log.plan else None
    if config is None:
        return {}
    values = {server: getattr(config, field, None) for field, server in REQUEST_KEYS.items()}
    return {key: value for key, value in values.items() if value is not None}


def _timings(response: Any) -> dict[str, float] | None:
    """Die Zeitmessung des Servers, nur wenn alle vier Felder Zahlen sind."""
    timings = response.get("timings") if isinstance(response, dict) else None
    if not isinstance(timings, dict):
        return None
    keys = ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms")
    if not all(isinstance(timings.get(k), (int, float)) for k in keys):
        return None
    return {k: timings[k] for k in keys}


def wall_seconds(log: EvalLog) -> float | None:
    stats = log.stats
    if not stats or not stats.started_at or not stats.completed_at:
        return None
    started = datetime.fromisoformat(stats.started_at)
    completed = datetime.fromisoformat(stats.completed_at)
    return (completed - started).total_seconds()


def _number(value: Any) -> str:
    # llama-server meldet 0.7 als 0.699999988079071 (float32).
    return f"{round(value, 4):g}" if isinstance(value, float) else str(value)


def sampling_text(params: dict[str, Any]) -> str:
    """`temp 1 · top_p 0.95 · top_k 64 · min_p 0.05`, dazu jeder wirkende Sonder-Sampler."""
    parts = [
        f"{'temp' if key == 'temperature' else key} {_number(params[key])}"
        for key in SAMPLING_KEYS
        if key in params
    ]
    parts += [
        f"{key} {_number(params[key])}"
        for key, neutral in SAMPLING_NEUTRAL.items()
        if key in params and params[key] != neutral
    ]
    return " · ".join(parts)


# ------------------------------------------------------------ Zusammenfassen


@dataclass(frozen=True)
class StatsCell:
    """Alle Läufe einer Aufgabe auf einem Modell."""

    runs: tuple[RunStats, ...]

    @property
    def seconds(self) -> float | None:
        """Median der Laufzeiten: Ein Lauf, der auf einen kalten Server traf, verschiebt ihn nicht."""
        values = [r.seconds for r in self.runs if r.seconds is not None]
        return statistics.median(values) if values else None

    @property
    def generation_rate(self) -> float | None:
        ms = sum(r.generated_ms for r in self.runs)
        return sum(r.generated_tokens for r in self.runs) / ms * 1000 if ms else None

    @property
    def prompt_rate(self) -> float | None:
        ms = sum(r.prompt_ms for r in self.runs)
        return sum(r.prompt_tokens for r in self.runs) / ms * 1000 if ms else None

    @property
    def max_context(self) -> int | None:
        values = [r.max_context for r in self.runs if r.max_context is not None]
        return max(values) if values else None

    @property
    def context_full(self) -> bool:
        """Mindestens ein Lauf hat das Kontextfenster seines Servers ausgeschöpft."""
        return any(
            r.max_context is not None and r.server_context is not None
            and r.max_context >= r.server_context
            for r in self.runs
        )

    @property
    def timed_calls(self) -> int:
        return sum(r.timed_calls for r in self.runs)

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.runs)

    @property
    def partly_timed(self) -> bool:
        """Nur ein Teil der Aufrufe hatte Server-Zeiten im Log (Agent-Aufgaben)."""
        return 0 < self.timed_calls < self.calls


def combine(cells: list[StatsCell]) -> StatsCell:
    """Mehrere Zellen eines Modells als eine — für die Spalte über alle Aufgaben."""
    return StatsCell(tuple(run for cell in cells for run in cell.runs))


def sampling_by_model(stats: list[RunStats]) -> dict[str, dict[str, list[str]]]:
    """Je Modell jede Sampling-Einstellung mit den Aufgaben, in denen sie galt.

    Ein Eintrag je Modell ist der Normalfall. Mehrere heißen, dass dasselbe
    Modell in verschiedenen Läufen verschieden lief — dann muss das sichtbar
    sein und nicht von einer der Einstellungen verdeckt werden.
    """
    result: dict[str, dict[str, list[str]]] = {}
    for run in stats:
        for text in run.sampling:
            tasks = result.setdefault(run.model, {}).setdefault(text, [])
            if run.task not in tasks:
                tasks.append(run.task)
    return result
