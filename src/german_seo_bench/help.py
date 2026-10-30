"""`uv run german-seo-bench` — der Schnelleinstieg auf der Kommandozeile.

Bewusst **kein** Wrapper um `inspect eval`: Dieser Befehl führt keinen Lauf aus,
er zeigt nur die Aufrufe, die man tippt. Damit bleibt die Bedienoberfläche
Inspect selbst (mit `--limit`, `--epochs`, `--temperature`, `-T`), und eine Hülle
verdeckt nichts davon.

Die Aufgabenliste wird bei jedem Aufruf aus dem Paket **erzeugt**, nicht
gepflegt: `german_seo_bench.tasks` wird durchlaufen, jede Aufgabe gebaut und nach
Namen, Anzeigename und Anzahl der Datensätze gefragt. Eine gepflegte Liste wäre die
erste Stelle, die veraltet — und eine Hilfe, die eine Aufgabe verschweigt oder
eine erfundene nennt, ist schlimmer als keine.
"""

import importlib
import pkgutil
import sys
from dataclasses import dataclass

from inspect_ai import Task
from inspect_ai.model import ContentImage

import german_seo_bench.tasks

# Die Beispiele nennen eine echte Aufgabe, nicht `<aufgabe>`: Ein Aufruf, den man
# kopieren kann, wird kopiert. Welche das ist, steht hier und nicht viermal im
# Text — und `tests/test_help.py` prüft, dass sie existiert.
EXAMPLE = "german_seo_bench/m01_titles"


@dataclass(frozen=True)
class TaskCard:
    """Was die Liste je Aufgabe zeigt. Alles daran kommt aus der Aufgabe selbst."""

    name: str
    display_name: str
    samples: int
    needs_image_model: bool
    needs_docker: bool
    # Warum sich die Aufgabe gerade nicht bauen lässt, sonst None. Betrifft
    # etwa `m01_judge` mit einer leeren eigenen Bewertungsdatei.
    unavailable: str | None = None


def task_cards() -> list[TaskCard]:
    """Alle Aufgaben des Pakets, alphabetisch.

    Gefunden werden sie über die Module in `german_seo_bench.tasks` — eine
    Aufgabe je Modul, gleich benannt wie das Modul. Diese Zusage hält
    `tests/test_help.py` fest, zusammen mit der zweiten: dass der Name in der
    Liste derselbe ist, unter dem Inspect die Aufgabe auflöst.
    """
    cards: list[TaskCard] = []
    for module in sorted(m.name for m in pkgutil.iter_modules(german_seo_bench.tasks.__path__)):
        # Eine Aufgabe, die sich nicht bauen lässt, steht mit ihrem Grund in der
        # Liste, statt die Hilfe für alle anderen abzubrechen — Inspect lehnt
        # etwa einen leeren Datensatz schon beim Bauen ab. Nur ValueError — ein anderer Fehler ist ein Fehler im Code und soll
        # auch so aussehen.
        try:
            built = getattr(importlib.import_module(f"german_seo_bench.tasks.{module}"), module)()
        except ValueError as error:
            cards.append(
                TaskCard(
                    name=f"german_seo_bench/{module}",
                    display_name=module,
                    samples=0,
                    needs_image_model=False,
                    needs_docker=False,
                    # Nur der Befund, ohne die Abhilfe nach dem Gedankenstrich:
                    # Die Zeile muss ins Terminal passen, und den ganzen Text
                    # bekommt man beim Start der Aufgabe.
                    unavailable=str(error).splitlines()[0].split(" — ")[0],
                )
            )
            continue
        cards.append(
            TaskCard(
                name=built.name,
                display_name=built.display_name or built.name,
                samples=len(built.dataset),
                needs_image_model=_has_image(built),
                # Am Sandbox-Eintrag abgelesen, nicht notiert: Eine Aufgabe mit
                # Container braucht Docker, und wer das erst beim Abbruch
                # erfährt, hält es für einen Fehler der Aufgabe.
                needs_docker=built.sandbox is not None,
            )
        )
    return cards


def _has_image(built: Task) -> bool:
    """Schickt die Aufgabe Bilder? Am Datensatz abgelesen, nicht notiert.

    Ein reines Textmodell scheitert an einer Bildaufgabe nicht mit schlechten
    Werten, sondern mit einem Serverfehler — eine Aussage über den Aufbau, nicht
    über das Modell. Der Hinweis gehört deshalb dorthin, wo man die Aufgabe
    auswählt.
    """
    for sample in built.dataset:
        if isinstance(sample.input, str):
            continue
        for message in sample.input:
            content = message.content
            if not isinstance(content, str) and any(
                isinstance(part, ContentImage) for part in content
            ):
                return True
    return False


# Breite, in die jede Zeile passen muss, die Einrückung eingerechnet. Ein
# Beispiel, das im Terminal umbricht, ist nicht mehr kopierbar.
# `tests/test_help.py` hält die Grenze fest.
WIDTH = 88


def _command(tokens: list[str]) -> str:
    """Ein Aufruf über mehrere Zeilen, mit Fortsetzungszeichen.

    Nötig, weil die Zeile mit allen Aufgaben mit jeder neuen Aufgabe wächst —
    eine handgesetzte Umbruchstelle wäre beim nächsten Zuwachs falsch.
    """
    lines = ["  "]
    for token in tokens:
        # +2 für das " \" am Zeilenende, das die Shell braucht.
        if len(lines[-1]) + len(token) + 2 > WIDTH and lines[-1].strip():
            lines[-1] += " \\"
            lines.append("      ")
        lines[-1] += (" " if lines[-1].strip() else "") + token
    return "\n".join(lines)


def help_text() -> str:
    """Die Hilfe als Zeichenkette — damit ein Test sie lesen kann."""
    cards = task_cards()
    # Name und Beschreibung auf zwei Zeilen: Nebeneinander passt beides mit dem
    # Paketpräfix nicht in die Breite.
    listing = "\n".join(
        f"  {card.name}\n      noch nicht startbar: {card.unavailable}"
        if card.unavailable
        else f"  {card.name}\n      {card.display_name}, "
        f"{card.samples} {'Datensatz' if card.samples == 1 else 'Datensätze'}"
        + ("  [Bildmodell]" if card.needs_image_model else "")
        + ("  [Docker]" if card.needs_docker else "")
        for card in cards
    )
    # Nur startbare Aufgaben: Der Aufruf mit allen soll man kopieren können,
    # und eine nicht startbare darin ließe ihn als Ganzes scheitern.
    all_tasks = _command(
        [
            "uv run inspect eval",
            *(card.name for card in cards if not card.unavailable),
            "--model local/qwen35-4b",
        ]
    )

    return f"""german-seo-bench — Benchmarks für Sprachmodelle in deutschen SEO-Aufgaben

Verfügbare Aufgaben

{listing}

Typische Aufrufe

  uv run pytest
      Wertung und Prüfungen — ohne Modell, ohne Netz. Läuft nach jeder Änderung
      und fährt den Oracle-Lauf jeder Aufgabe mit.

  uv run inspect eval {EXAMPLE} --model local/qwen35-4b
      Ein Lauf gegen einen lokalen OpenAI-kompatiblen Server (llama-swap,
      llama-server, Ollama, LM Studio) auf http://localhost:8080/v1.

  uv run inspect eval {EXAMPLE} --model local/qwen35-4b \\
      --model-base-url http://192.168.1.50:8080/v1
      Derselbe Lauf gegen einen Server im Netz. Dauerhaft: LOCAL_BASE_URL
      setzen, als Umgebungsvariable oder in einer .env im Projektverzeichnis.

  uv run inspect eval {EXAMPLE} --model anthropic/claude-sonnet-5
      Zum Vergleich ein Frontier-Modell — dieselbe Wertung, dasselbe Maß.
      Schlüssel in ANTHROPIC_API_KEY bzw. OPENAI_API_KEY.

  uv run python -m german_seo_bench.media
      Einmalig vor M10: holt die Bilder von Pixabay und prüft sie gegen ihre
      Prüfsumme. Das Repo verteilt die Bilder nicht selbst; nötig ist ein
      kostenloser Pixabay-API-Schlüssel in PIXABAY_API_KEY.

  uv run inspect eval {EXAMPLE} --model local/qwen35-4b --epochs 3
      Drei Durchgänge. Ein einzelner Lauf zeigt keine Streuung, und ohne
      Streuung ist ein Unterschied zwischen zwei Modellen keine Aussage.

  uv run python -m german_seo_bench.judge logs/….eval --judge local/gemma-4-12b
      Einen fertigen M01-Lauf nachträglich von einem Judge werten (brauchbar
      oder unbrauchbar). Die Urteile landen in X.judges.json neben dem Log,
      das Log bleibt unverändert.

  uv run inspect eval {EXAMPLE} --model local/qwen35-4b --limit 3
      Nur die ersten drei Datensätze — zum Ausprobieren eines Aufbaus, nicht
      zum Messen.

{all_tasks}
      Alle Aufgaben hintereinander. [Bildmodell] oben heißt: Ein reines
      Textmodell scheitert dort mit einem Serverfehler, nicht mit Punkten.
      [Docker] heißt: Die Aufgabe fährt Container hoch (Agent mit Browser plus
      Prüflandschaft); der erste Lauf baut das Image.

  uv run inspect eval {EXAMPLE} -T oracle=true --model none/none
      Der Oracle-Lauf: Die Musterlösung geht durch denselben Scorer wie eine
      Modellantwort und muss 1.00 ergeben. Sonst ist nicht das Modell schlecht,
      sondern die Prüfung kaputt. Braucht weder Server noch GPU noch Netz.

  uv run inspect view
      Die Ergebnisse im Browser: jede Anfrage und jede Antwort im Wortlaut,
      je Datensatz die Begründung der Wertung.

  uv run python -m german_seo_bench.report logs --out auswertung
      Viele Läufe zusammengefasst: Score je Aufgabe und Modell, Gesamtscore je
      Modell — im Terminal, mit --out auch als Markdown, JSON und SVG.

Ohne Checkout, direkt aus dem Repo

  uvx --from git+https://github.com/stefanfis/german-seo-bench german-seo-bench
  uvx --from git+https://github.com/stefanfis/german-seo-bench \\
      inspect eval {EXAMPLE} --model local/qwen35-4b

Gut zu wissen

  Die Logs landen in ./logs — relativ zum Arbeitsverzeichnis, nicht zum
  Projekt. Wer die Befehle woanders aufruft, verteilt seine Läufe über den
  Rechner; INSPECT_LOG_DIR nagelt den Ort fest.

  Alle Optionen eines Laufs: uv run inspect eval --help
  Hintergrund, Methodik und Aufbau: README.md und docs/
"""


def main() -> None:
    """Einstiegspunkt des Konsolenskripts `german-seo-bench`."""
    argument = sys.argv[1] if len(sys.argv) > 1 else ""
    # `help` und `--help` sind mitgemeint, weil sie beide getippt werden. Alles
    # andere ist ein Missverständnis: Dieser Befehl führt nichts aus, und ein
    # `german-seo-bench eval …`, das still die Hilfe zeigt, sähe wie ein Lauf
    # aus, der nichts gemessen hat.
    if argument and argument not in ("help", "-h", "--help"):
        print(
            f"german-seo-bench: {argument!r} ist kein Befehl. "
            "`german-seo-bench` zeigt die Hilfe, gemessen wird mit `inspect eval`.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(help_text())
