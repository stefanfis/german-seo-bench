"""Laufvorgaben: vorsichtig, damit ein Lauf ohne Zutun durchläuft.

Inspects eigene Vorgaben sind auf gehostete Anbieter zugeschnitten, die viele
gleichzeitige Anfragen erwarten und mit HTTP 429 bremsen, wenn es zu viel wird.
Ein Inferenzserver auf dem eigenen Schreibtisch tut beides nicht. Er hat
typischerweise einen Slot und antwortet unter Last mit 503.

Was daraus ohne Gegenmaßnahme folgt:

  * Inspect startet adaptiv bei 20 gleichzeitigen Verbindungen. Bei 20
    Datensätzen gehen also alle auf einmal raus.
  * Der Server lehnt ab. 503 zählt bei Inspect als `transient`, nicht als
    `rate_limit` — und nur `rate_limit` senkt die Nebenläufigkeit. Der
    adaptive Regler sieht das Signal nie, auf das er reagieren würde.
  * Ohne `max_retries` wird unbegrenzt wiederholt, ohne `timeout` unbegrenzt
    gewartet. Der Backoff wächst auf bis zu 30 Minuten je Versuch.

Das Ergebnis sieht aus wie ein hängendes Modell und ist keins. Deshalb hier
das Gegenteil: eine Anfrage nach der anderen, eine harte Obergrenze, wenige
Wiederholungen.

Alle drei Werte lassen sich auf der Kommandozeile überschreiben — die
Aufgabenvorgabe wird mit den CLI-Argumenten verschmolzen, und dabei gewinnt
die Kommandozeile (`task.config.merge(...)`):

    --max-connections 4         mehrere Anfragen gleichzeitig
    --attempt-timeout 900       mehr Geduld je Versuch
    --timeout 900               mehr Geduld insgesamt
    --max-tokens 8192           längere Reasoning-Spuren zulassen
    --max-retries 5             mehr Wiederholungen
    --adaptive-connections 1-20   Inspects adaptiven Regler zurückholen
"""

from inspect_ai.model import GenerateConfig

# Eine Anfrage nach der anderen. Der Wert regelt zweierlei zugleich: die
# HTTP-Nebenläufigkeit und — weil Inspect die Sample-Parallelität ohne
# adaptiven Regler aus `max_connections` ableitet — auch die Zahl der
# gleichzeitig laufenden Datensätze.
MAX_CONNECTIONS = 1

# Die Grenze, die einen hängenden Request WIRKLICH abbricht.
#
# Das ist die wichtige der beiden Zeitgrenzen, und die Unterscheidung kostet
# leicht eine Stunde Fehlersuche: `timeout` hat bei Inspect die Semantik von
# tenacitys `stop_after_delay` und wird nur ZWISCHEN zwei Versuchen geprüft —
# Inspects eigener Kommentar dazu lautet "an in-flight attempt always drains
# first" (`model/_retry.py`). Ein einzelner Versuch, der nie zurückkommt, wird
# davon nie unterbrochen. Nachgemessen gegen einen Server, der auf POST
# niemals antwortet: Mit `--timeout 15` lief der Lauf unbegrenzt weiter, mit
# `--attempt-timeout 10` brach er nach drei Versuchen mit
# `AttemptTimeoutError` ab.
#
# Nur `attempt_timeout` legt einen echten Abbruch um den Versuch
# (`anyio.move_on_after`).
#
# Der Wert muss `MAX_TOKENS` bei 10 Tokens pro Sekunde abdecken, so langsam
# ist ein dichtes 30B-Modell auf einem Arbeitsplatzrechner. Sonst greift bei
# einer ausufernden Antwort die Zeitgrenze vor der Längengrenze: Statt einer
# abgeschnittenen Antwort, die gewertet wird und den Lauf weitergehen lässt,
# steht dann ein Fehler, der den ganzen Lauf abbricht — gemessen wäre die
# Hardware, nicht das Modell. Kürzer zu stellen, weil normale Antworten in
# Sekunden kommen, wäre deshalb falsch.
ATTEMPT_TIMEOUT_SECONDS = 600

# Obergrenze für die ganze Anfrage einschließlich Wiederholungen. Gleich groß
# gewählt, und das ergibt genau das gewünschte Verhalten: Ein Fehler, der
# sofort zurückkommt (503 beim Modellladen), verbraucht kaum Zeit und wird
# wiederholt. Ein Versuch, der in die Zeitgrenze läuft, hat das Budget danach
# aufgebraucht — es gibt keinen zweiten Zehn-Minuten-Versuch.
TIMEOUT_SECONDS = 600

# Obergrenze für die Antwortlänge. Der eigentliche Grund, warum ein Datensatz
# überhaupt sechs Minuten laufen kann: Ohne `max_tokens` entscheidet der
# Server, und llama-server generiert per Vorgabe bis EOS oder bis der Kontext
# voll ist. Ein kleines Reasoning-Modell, das sich an einem mehrdeutigen
# Begriff festdenkt ("Kiefer" = Baum oder Kieferknochen), läuft dann in
# Zehntausende Tokens.
#
# 4096 ist bewusst großzügig: Die längste beobachtete gültige Antwort lag bei
# rund 1000 Tokens inklusive Reasoning. Der Wert soll Ausreißer abschneiden,
# nicht ordentliche Antworten. Wird er erreicht, ist das eine Aussage über das
# Modell und keine über den Aufbau — der Scorer schreibt es in die Begründung.
MAX_TOKENS = 4096

# BEWUSST NICHT GESETZT: `seed`.
#
# Inspect reicht `config.seed` unverändert an jede Anfrage durch
# (`model/_openai.py`) und variiert ihn NICHT je Epoche. Ein fester Seed
# zusammen mit `--epochs 5` liefert deshalb fünfmal dieselbe Antwort, und die
# Streuung wäre exakt null — nicht weil das Modell stabil ist, sondern weil
# derselbe Seed dasselbe liefert. Wiederholungsläufe brauchen freies Sampling; wer Reproduzierbarkeit will, bekommt sie über
# `--temperature 0`, nicht über einen Seed.
#
# Wer ihn doch braucht, setzt ihn auf der Kommandozeile (`--seed`) — dann steht
# er im Log und ist als Laufbedingung sichtbar.

# Zwei Wiederholungen, also drei Versuche. Nicht null: llama-swap lädt das
# Modell erst mit der ersten Anfrage und antwortet währenddessen mit 503. Ein
# einziger Versuch würde genau daran scheitern.
MAX_RETRIES = 2


# Sampling-Parameter, die der Benchmark NICHT setzt — siehe server.py. `/props`
# meldet die Server-Vorgaben; sobald der Runner einen davon mitschickt, steht
# in der Aufzeichnung etwas anderes als in der Anfrage. Die Werte gehören in
# den llama-swap-Eintrag, nicht hierher.
SAMPLING_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
    "stop_seqs",
    "best_of",
    "num_choices",
)


def run_config(
    attempt_timeout: int = ATTEMPT_TIMEOUT_SECONDS, max_tokens: int = MAX_TOKENS
) -> GenerateConfig:
    """Die Vorgaben als `GenerateConfig` für `Task(config=...)`.

    `attempt_timeout` und `max_tokens` setzt eine Aufgabe herauf, deren
    Anfragen absehbar länger brauchen (A01: großer Kontext, lange Antworten).
    Das Gesamtbudget wächst mit: Bliebe `timeout` bei der Vorgabe, wäre es nach dem
    ersten langen Versuch erschöpft, und für einen Fehler, der sofort
    zurückkommt (503 beim Modellladen), gäbe es keine Wiederholung mehr.
    """
    return GenerateConfig(
        max_connections=MAX_CONNECTIONS,
        attempt_timeout=attempt_timeout,
        timeout=max(TIMEOUT_SECONDS, attempt_timeout),
        max_retries=MAX_RETRIES,
        max_tokens=max_tokens,
    )
