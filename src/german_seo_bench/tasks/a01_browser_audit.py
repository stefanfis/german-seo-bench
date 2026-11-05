"""A01 — Browser-Audit einer JS-Site (agentisch, deterministisch gewertet).

Die Agent-Aufgabe im Repo. Was sie von M01 bis M10 unterscheidet, ist
nicht die Wertung, sondern woher die Antwort kommt: Das Modell bekommt das HTML
**nicht** in den Prompt. Es fährt über MCP einen echten Chrome und meldet Fehler
aus einem geschlossenen Katalog — Dinge, die erst sichtbar werden, wenn die Seite
läuft: das DOM nach JavaScript, der Netzwerk-Waterfall, die Stabilität des
Layouts nach dem Laden, das Verhalten beim Klick. Genau das rechtfertigt den
Browser; ein Quelltext-Audit kann das meiste davon prinzipiell nicht sehen.

Ein Teil der Befunde entsteht erst beim **Scrollen** (nachgeladener Bestand ohne
Paginierung) und beim **Klicken** (ein Handler, der den Hauptthread 700 ms
blockiert). Wer den Browser nur als Renderer benutzt und nicht als Werkzeug,
findet sie nicht — und daran trennen sich Agenten, die eine Seite wirklich
bedienen, von solchen, die einen Snapshot lesen.

Gewertet wird das **F1 je Fähigkeitsklasse**, gemittelt über die Klassen
(`capability_f1`), gegen eine eingefrorene Referenz aus (Seite, Problem)-Paaren.
Warum hier nicht alles-oder-nichts wie in M01, M03 und M10: Dort ist ein
Datensatz eine Frage mit einer Antwort, hier ist er ein Auftrag über
sechsundzwanzig Seiten mit einunddreißig Befunden. Eine UND-Wertung wäre eine
Münze und unterschiede nicht zwischen vierzehn gefundenen Befunden und keinem.

**Die Topologie ist die Zusicherung, nicht der Python-Code.** `compose.yaml`
neben den Daten definiert zwei Container: den Agenten mit Chrome und die
Prüflandschaft, die `site/` ausliefert. Gemountet wird nur `site/`, read-only.
Referenz, Scorer und Aufgabencode liegen nicht in diesen Containern, und das Netz
ist `internal` — es gibt keinen Weg nach draußen. Ein Agent, der die Seiten nicht
rendern kann, kann auch nicht ausweichen und etwas nachschlagen.

**Der Viewport gehört zur Messbedingung.** `layout_shift_after_load` und
`lazy_lcp_image` hängen an der Fenstergröße: Dasselbe Bild liegt bei 1280x900 im
ersten sichtbaren Bereich und bei 375x667 nicht, und derselbe CLS-Wert kann je
nach Viewport unter oder über der Schwelle liegen. Deshalb stellt der
**MCP-Server** den Viewport (`--viewport`), und der Prompt nennt ihn zusätzlich.
Die Angabe im Prompt allein wäre eine Bitte; der Schalter ist die Bedingung.

**Woher die Referenz kommt.** Die Paare in `data/a01_browser_audit.json` sind
nicht getippt, sondern gerechnet: `python -m german_seo_bench.reference` rendert
jede Seite einmal in einem eigenen Container, friert das Ergebnis als Capture ein
und lässt einen deterministischen Auditor (`reference/audit.py`) darüber laufen.
Was der findet, IST die Referenz. Was die Datei zusichert, prüft zusätzlich
`tests/test_a01_browser_audit.py` gegen die ausgelieferte Site — für jeden
Befund, der schon im Quelltext entscheidbar ist, und gegen die Kontrollseiten,
die keinen Befund haben dürfen.

**Zwei Fallen gegen das Raten sind Absicht.** `noindex_after_render` kommt
zweimal vor (`angebot.html`, `suche.html`) — wer nach dem ersten Treffer je
Katalogeintrag aufhört, verliert Recall. Und `ratgeber.html` trägt alle
Warnsignale ohne einen Fehler zu haben: lazy geladene Bilder (aber unterhalb des
ersten Viewports, also richtig eingesetzt), einen Button (aber mit schnellem
Handler), ein Skript (aber `defer` und nach dem CSS). Wer Muster statt Zustände
meldet, sammelt hier Falschmeldungen.
"""

import json
import os
import re
from dataclasses import dataclass, replace
from importlib.metadata import version
from datetime import datetime
from time import perf_counter
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.agent import (
    Agent,
    AgentPrompt,
    AgentState,
    agent,
    as_solver,
    react,
    sandbox_agent_bridge,
)
from inspect_ai.log import HeadlineMetric
from inspect_ai.model import ModelOutput, get_model, get_model_info
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.model import ChatMessageUser
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import mcp_connection, mcp_server_sandbox, text_editor
from inspect_ai.util import OutputLimitExceededError, sandbox, store

from german_seo_bench.checks import (
    as_findings,
    behaviour,
    grade_audit,
    pages_seen,
    parse_findings,
    tool_usage,
)
from german_seo_bench.config import run_config
from german_seo_bench.dataset import data_path, dataset_version, load_dataset
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.providers import STORE_CONTEXT_OVERFLOWS
from german_seo_bench.server import record_endpoint, record_server, server_context

DATA = "a01_browser_audit.json"

# Die Sandbox liegt bei den Daten, nicht bei diesem Modul: `site/` gehört zum
# Datensatz, und `compose.yaml` montiert es relativ zu sich selbst. Über
# `data_path` findet Inspect die Datei auch dann, wenn das Paket per `uvx` ohne
# Checkout läuft.
COMPOSE = "a01_browser_audit/compose.yaml"

SYSTEM = (
    "Du bist technischer SEO und prüfst Websites mit den Entwicklerwerkzeugen "
    "des Browsers. Du arbeitest gründlich und meldest nur, was du tatsächlich "
    "beobachtet hast."
)

# Der Dienstname aus `compose.yaml`. Steht hier doppelt, weil der Prompt die
# Startadresse nennen muss; `tests/test_a01_browser_audit.py` hält die beiden
# Stellen zusammen.
SITE_URL = "http://www.example.net/"

# Wo der Agent anfängt. Der Prompt nennt keine Seitenliste: Ein Auftrag nennt
# die Website, die Seiten findet der Prüfer selbst — über die Links und über
# die Sitemap, die `robots.txt` angibt. Die Startseite steht mit Dateinamen da
# und nicht als `/`, weil Befunde den Dateinamen tragen und die Startseite
# selbst welche hat.
START_PAGE = "home.html"

# Der Viewport, an den die Wertung gebunden ist — siehe Modul-Docstring.
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 900

# Chrome im Agent-Container (`agent.Dockerfile`).
CHROME_PATH = "/usr/bin/chromium"

# Die Kommandozeile des MCP-Servers. Jedes Flag steht einzeln begründet:
#
#   --executablePath   DER Schalter, ohne den nichts läuft. chrome-devtools-mcp
#                      sucht per Vorgabe (`--channel=stable`) eine echte
#                      Google-Chrome-Installation und ignoriert dabei
#                      CHROME_PATH und PUPPETEER_EXECUTABLE_PATH. Ohne ihn
#                      scheitert jedes `new_page`, und das Modell weicht auf
#                      curl plus Quelltextlesen aus — es RÄT dann die Antworten
#                      statt zu rendern. Ein Lauf, der so zustande kommt, misst
#                      nicht, was er behauptet.
#   --viewport         Die Messbedingung, nicht eine Vorliebe. Siehe oben.
#   --headless=true    Kein Display im Container.
#   --isolated=true    Ein frisches Profil je Start; sonst tragen zwei
#                      Wiederholungen denselben Cache und derselbe Lauf
#                      liefert beim zweiten Mal andere Netzwerkbefunde.
#   --chromeArg=…      Chromium startet im Container sonst nicht (--no-sandbox)
#                      bzw. fällt bei kleinem /dev/shm um.
MCP_COMMAND = "npx"
MCP_ARGS = [
    "chrome-devtools-mcp",
    "--headless=true",
    "--isolated=true",
    f"--executablePath={CHROME_PATH}",
    f"--viewport={VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT}",
    "--chromeArg=--no-sandbox",
    "--chromeArg=--disable-dev-shm-usage",
]

# Zeitgrenze je MCP-Aufruf. Inspects Vorgabe ist 180 s; ein Trace-Aufruf mit
# Performance-Aufzeichnung über eine Seite mit Bildern kann darüber liegen, und
# ein Abbruch mitten in der Messung sähe aus wie ein Agent, der aufgibt.
MCP_TIMEOUT = 300

# Wie viele Nachrichten ein Audit haben darf. Sechsundzwanzig Seiten, je Seite
# mehrere Werkzeugaufrufe (navigieren, Snapshot, Netzwerk, scrollen, klicken),
# dazu die seitenübergreifenden Befunde, für die ein Agent Seiten zusammen
# betrachten muss — eine zu knappe Grenze machte die Null zu einer Aussage über
# die Grenze statt über das Modell. Bewusst großzügig gewählt.
# Die Kommandozeile überschreibt: `--message-limit`.
MESSAGE_LIMIT = 400

# Obergrenze für den ganzen Datensatz, für beide Achsen dieselbe. Großzügig,
# weil nach einem Abbruch nur gewertet wird, was bis dahin in der Befundliste
# steht: Auf langsamer Hardware braucht ein vollständiges Audit weit über eine
# Stunde. Ein Modell, das sich im Kreis dreht, hält vorher `MESSAGE_LIMIT` an.
# Inspect wertet auch einen Datensatz, der an dieser Grenze endet; der Scorer
# liest die Befundliste dann aus dem noch laufenden Container.
# Die Cache-Frist und die Cookies der Prüflandschaft (`testsite.nginx.conf`)
# müssen mindestens so lange gelten, sonst hängen die Fallen an der Uhr.
TIME_LIMIT = 7200

# Obergrenze für die Länge einer Antwort, statt der 4096 Tokens der
# Einzelaufgaben. Ein Audit braucht lange Zwischenüberlegungen (Seitenlisten,
# Abgleich der Befunde) und am Ende eine Schlussantwort mit allen Befunden;
# bei eingeschaltetem Denkmodus zählt die Denkphase mit. 4096 schneidet schon
# ohne Denkmodus ordentliche Überlegungen ab. Ein höherer Wert kostet ein
# Modell, das ihn nicht braucht, nichts.
ANSWER_MAX_TOKENS = 16384

# Zeitgrenze je Modellanfrage, statt der 600 s der Einzelaufgaben. Hier wächst
# der Kontext über das Audit auf Zehntausende Tokens, und die langen Antworten
# kommen gegen Ende. Mit 1800 s schafft ein lokales Modell mit 10 Tokens pro
# Sekunde `ANSWER_MAX_TOKENS`, eines mit 5 Tokens pro Sekunde die Hälfte —
# mehr, als eine Schlussantwort ohne Denkmodus braucht. Mit 600 s scheitert
# genau die letzte Anfrage, und ein vollständiges Audit endet als Fehler ohne
# Score — gemessen wäre die Hardware, nicht das Modell.
# Unbegrenzt laufen kann trotzdem nichts: `TIME_LIMIT` gilt für alles.
MODEL_CALL_TIMEOUT = 1800

# --------------------------------------------------------------------------
# Das Kontextfenster — gemessen und genannt, nicht gepflegt
# --------------------------------------------------------------------------
#
# Der Benchmark pflegt keinen Kontext. Was ein Agent mit einem vollen Fenster
# macht, ist Sache des Prüflings: im `react`-Pfad die Sparsamkeit des Modells
# (die Befundliste ist sein Gedächtnis), bei einem Agent-CLI zusätzlich dessen
# eigene Kontextpflege — die Teil dessen ist, was diese Achse vergleicht.
#
# Das Fenster wird trotzdem ermittelt: als Laufbedingung fürs Log, und weil ein
# CLI wie goose es bei lokalen Modellen nicht selbst herausfindet. Die
# Rechnung am Beispiel des Minimums:
#
#   Fenster             65.536   was der Server je Anfrage hat (`n_ctx`)
#   - ANSWER_MAX_TOKENS 16.384   jede Anfrage hält so viel für die Antwort frei
#   = input_budget      49.152   so lang darf eine Anfrage höchstens sein

# Das Minimum, mit dem A01 sinnvoll läuft, und die Annahme, wenn sich das
# Fenster nicht ermitteln lässt. Darunter wird trotzdem gemessen, aber der
# Bericht vermerkt es.
MIN_CONTEXT = 65_536

# Provider, hinter denen ein eigener Server steht. Für sie zählt nur, was der
# Server meldet: Inspects Modelldatenbank kennt das Fenster des MODELLS
# (Qwen 3.5 9B: 262.144), nicht das, mit dem der Server gestartet wurde.
SERVER_PROVIDERS = ("local/", "openai-api/")

# Woher das Fenster stammt (`Score.metadata.context.source`).
WINDOW_FROM_SERVER = "server"
WINDOW_FROM_MODEL_INFO = "model_info"
WINDOW_ASSUMED = "assumed"


@dataclass(frozen=True)
class ContextBudget:
    """Das Fenster eines Laufs und was daraus folgt."""

    window: int
    source: str
    # Warum das Fenster nur angenommen ist — sonst `None`.
    note: str | None = None

    @property
    def input_budget(self) -> int:
        return self.window - ANSWER_MAX_TOKENS

    def as_dict(self) -> dict[str, Any]:
        eintrag: dict[str, Any] = {
            "window": self.window,
            "source": self.source,
            "input_budget": self.input_budget,
        }
        if self.note:
            eintrag["note"] = self.note
        return eintrag


def pick_window(
    model: str, server_n_ctx: int | None, model_info_tokens: int | None
) -> ContextBudget:
    """Welches Fenster gilt: der Server, sonst Inspects Modelldaten, sonst das Minimum.

    Eine eigene Funktion, damit sich die Reihenfolge ohne Server prüfen lässt.
    """
    if model.startswith(SERVER_PROVIDERS):
        if server_n_ctx:
            return ContextBudget(server_n_ctx, WINDOW_FROM_SERVER)
        return ContextBudget(MIN_CONTEXT, WINDOW_ASSUMED)
    if model_info_tokens:
        return ContextBudget(model_info_tokens, WINDOW_FROM_MODEL_INFO)
    return ContextBudget(MIN_CONTEXT, WINDOW_ASSUMED)


# Unter diesem Schlüssel steht das Budget im Store; der Scorer schreibt es ins
# Log, der Bericht vermerkt ein Fenster unter dem Minimum.
STORE_CONTEXT = "a01_context"


async def plan_context() -> ContextBudget:
    """Das Fenster dieses Laufs ermitteln und im Store festhalten.

    Läuft zu Beginn des Agenten, also vor der ersten Anfrage. Bei llama-swap
    startet die Abfrage das Modell und wartet, bis es geladen ist; das hätte
    die erste Anfrage ohnehin getan. Ist das Fenster nur angenommen, steht
    der Grund mit im Store.
    """
    model = get_model()
    name = str(model)
    n_ctx, grund = None, None
    base_url = getattr(model.api, "base_url", None)
    if name.startswith(SERVER_PROVIDERS):
        if base_url:
            n_ctx, grund = await server_context(
                base_url, getattr(model.api, "model_name", name)
            )
        else:
            grund = "Provider ohne base_url"
    info = None if name.startswith(SERVER_PROVIDERS) else get_model_info(model)
    budget = pick_window(name, n_ctx, info.input_tokens if info else None)
    if budget.source == WINDOW_ASSUMED:
        budget = replace(
            budget, note=grund or "Inspect kennt das Fenster dieses Modells nicht"
        )
    store().set(STORE_CONTEXT, budget.as_dict())
    return budget

# --------------------------------------------------------------------------
# Die zweite Messachse: über WELCHEN Agenten läuft das Audit?
# --------------------------------------------------------------------------
#
# `react` ist Inspects eigener Werkzeug-Loop — billig, reproduzierbar, und er
# misst Modellfähigkeit an einem neutralen Gerüst. Das ist die Grundlinie und
# bleibt Vorgabe.
#
# `goose` und `pi` sind echte Agent-CLIs im Container, jedes mit eigenem
# Systemprompt, eigenem Kontextmanagement und eigenen Werkzeugen. Damit lässt
# sich die Frage „Welches Agentic Framework taugt für SEO?" messen. Gleicher Prompt, gleiche Wertung, gleiche Referenz — die einzige geänderte Größe ist
# das Framework.
#
# Der Weg dorthin ist `sandbox_agent_bridge`: Inspect startet IM Container einen
# Proxy, der die OpenAI-API spricht und an den Modell-Provider des Laufs
# weiterreicht. Damit gilt dreierlei, und alles drei ist der Grund für diese
# Bauart statt eines direkten Endpunkts im CLI:
#
#   1. Jede Modellanfrage des CLI steht im Inspect-Log (Nachrichten, Werkzeuge,
#      Tokens). Ein CLI, das selbst gegen einen Inferenzserver fährt, ist eine
#      Blackbox, über deren Kontextverbrauch man nichts erfährt.
#   2. `local/<alias>` funktioniert unverändert, weil der Proxy an Inspect
#      weiterreicht.
#   3. Das `internal: true`-Netz bleibt: Der Proxy läuft IM Container.
# Die Fähigkeitsklassen: WAS GENÜGT, um einen Befund zu holen — nicht, worum es
# im Item inhaltlich geht. Der Unterschied ist der Kern dieser Spalten:
# `interaction_blocked` heißt "Interaktion" und ist trotzdem ein `source`-Item —
# der Mechanismus steht im Skript, und wer JavaScript liest, braucht den Klick
# nicht.
#
#   source      die ausgelieferten Bytes lesen (HTML, JS, CSS, sitemap.xml)
#   network     die Antwortdaten statt des Rumpfs (Status, Weiterleitungskette)
#   render      gerendertes DOM, Geometrie, Layoutmessung
#   request     eine Anfrage unter besonderer Bedingung (Kennung, Cookie,
#               Cache-Umgehung)
#   second_load Zustand über zwei Ladevorgänge — wer eine Adresse genau einmal
#               ansieht, kann den Befund nicht haben
#
# Die Reihenfolge ist die aufsteigende Mühe. Eine Klasse steht hier erst, wenn
# die Referenz Befunde dafür hat — eine Spalte ohne Datensatz zeigte sonst eine
# Eins, die niemand verdient hat. `test_klassen_stimmen_mit_dem_datensatz`
# erzwingt den Gleichstand.
CAPABILITY_CLASSES = ("source", "network", "render", "request", "second_load")

def capability_f1(
    by_capability: dict[str, dict[str, int]], false_by_capability: dict[str, int]
) -> tuple[float, dict[str, float | None]]:
    """DER Score von A01: F1 je Fähigkeitsklasse, gemittelt über die belegten.

    **Warum nicht Micro-F1 über alle Paare.** Im F1 über alle (Seite,
    Problem)-Paare wöge eine Fähigkeit so viel wie ihr Anteil an den Items —
    und der ist extrem schief: Die meisten Befunde sind `source`, also aus den
    ausgelieferten Bytes entscheidbar. Ein Löser, der alle `source`-,
    `network`- und `render`-Befunde holt und in `request` und `second_load`
    nichts, käme in Micro-F1 auf über 0.9. Eine Zahl, in der das Verfehlen
    einer ganzen Fähigkeit im Rauschen verschwindet, kann „lokal gegen
    Frontier" nicht beantworten.

    Im Mittel über die Klassen wiegt jede Fähigkeit gleich viel — genau die
    Gewichtung, die der Zweck der Aufgabe verlangt. Derselbe Löser erreicht
    darin 0.600.

    **Falschmeldungen zählen gegen die Klasse des gemeldeten Fehlertyps.** Wer
    `content_requires_js` falsch meldet, hat einen `source`-Fehler gemacht und
    keinen `render`-Fehler. Ohne diese Zuordnung wäre der Score ein reines
    Recall-Maß, und ein Agent, der alles meldet, bekäme eine Eins.

    **Eine Klasse ohne Befunde zählt nicht mit.** Sie steht als `None` in der
    Aufschlüsselung. Als 0.0 gezählt, drückte sie den Score auf einem
    Ausschnitt der Aufgabe (`--sample-id`, eigene Datensätze) auch dann, wenn
    der Agent alles gefunden hat, was dort zu finden war. Im vollen Datensatz
    hat jede Klasse Befunde (`test_klassen_stimmen_mit_dem_datensatz`).

    Eine eigene Funktion, damit sich dieser Fall prüfen lässt, ohne einen
    ganzen Datensatz zu bauen.
    """
    je_klasse: dict[str, float | None] = {}
    for name in CAPABILITY_CLASSES:
        eintrag = by_capability.get(name) or {}
        total = eintrag.get("total", 0)
        if not total:
            je_klasse[name] = None
            continue
        tp = eintrag.get("found", 0)
        fp = false_by_capability.get(name, 0)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / total
        je_klasse[name] = (
            2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        )
    belegt = [wert for wert in je_klasse.values() if wert is not None]
    return (sum(belegt) / len(belegt) if belegt else 0.0), je_klasse


def evaluate_audit(completion: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Eine Antwort gegen die Referenz rechnen — alles, was die Wertung braucht.

    Warum das eine eigene Funktion ist: Sie hat **zwei** Aufrufer. Der Scorer wertet damit, und der Berichtsschritt blendet die
    Bewertung in den Kundenbericht ein. Der Bericht entsteht VOR der Wertung —
    er könnte sie also nicht abwarten, sondern müsste sie nachbauen, und zwei
    Fassungen derselben Rechnung laufen auseinander. Dann stünde im PDF eine
    andere Zahl als im Log, und man glaubte der falschen.
    """
    issues = [entry["issue"] for entry in meta["catalog"]]
    reported, discarded, error = parse_findings(completion, meta["pages"], issues)
    reference = as_findings(meta["findings"])
    verdict = grade_audit(reported, reference)

    # Recall je Klasse. Gezählt wird gegen die Referenz, nicht gegen die
    # Meldung: Eine Klasse, die der Agent gar nicht erst versucht, soll als
    # Null dastehen und nicht als leere Menge fehlen.
    by_capability: dict[str, dict[str, int]] = {}
    for page, issue in reference:
        eintrag = by_capability.setdefault(
            meta["capability_by_issue"][issue], {"found": 0, "total": 0}
        )
        eintrag["total"] += 1
        eintrag["found"] += int((page, issue) in reported)

    # Falschmeldungen je Klasse. Sie sind sehr wohl einer Klasse zuzuordnen:
    # Wer `content_requires_js` falsch meldet, hat einen `source`-Fehler
    # gemacht, nicht einen `render`-Fehler. Ohne diese Zuordnung wäre der Score
    # ein reines Recall-Maß — und ein Agent, der alles meldet, bekäme eine Eins.
    false_by_capability: dict[str, int] = {name: 0 for name in CAPABILITY_CLASSES}
    for page, issue in reported - reference:
        klasse = meta["capability_by_issue"].get(issue)
        if klasse in false_by_capability:
            false_by_capability[klasse] += 1

    score_value, f1_by_capability = capability_f1(by_capability, false_by_capability)
    return {
        "reported": reported,
        "discarded": discarded,
        "parse_error": error,
        "reference": reference,
        "verdict": verdict,
        "by_capability": by_capability,
        "false_by_capability": false_by_capability,
        "recalls": {
            name: (
                by_capability[name]["found"] / by_capability[name]["total"]
                if by_capability.get(name, {}).get("total")
                else 0.0
            )
            for name in CAPABILITY_CLASSES
        },
        "score": score_value,
        "f1_by_capability": f1_by_capability,
    }


AGENT_REACT = "react"
AGENT_GOOSE = "goose"
AGENT_PI = "pi"
AGENTS = (AGENT_REACT, AGENT_GOOSE, AGENT_PI)

# Wo das CLI arbeitet. Derselbe Pfad wie `WORKDIR` im Agent-Image; die Antwort
# wird von dort absolut gelesen und nicht relativ — `sandbox().read_file` löst
# einen relativen Pfad gegen das Arbeitsverzeichnis der SANDBOX auf, nicht gegen
# das des Prozesses, den wir gestartet haben.
CLI_WORKDIR = "/root"

# Die Befundliste. Der Agent hält sie während des Audits laufend aktuell, und
# sie ist das Ergebnis, das gewertet wird — in jedem Gerüst dieselbe Datei an
# derselben Stelle. Das ist der Vertrag mit einem Gerüst: Wer Dateien schreiben
# kann, kann A01 abgeben. `react` bekommt dafür `text_editor`, ein Agent-CLI
# bringt seine Dateiwerkzeuge mit.
#
# Warum eine Datei und nicht nur die Schlussantwort: Ein Lauf, der an der
# Zeitgrenze oder am Kontext scheitert, hätte sonst nichts abgegeben, und alles
# bis dahin Gefundene zählte null. Mit der Datei wird gewertet, was bis zum
# Abbruch feststand.
#
# Warum JSON Lines: Eine kaputte Zeile kostet nur diese Zeile (sie zählt als
# verworfene Meldung), nicht die ganze Liste. Ob der Agent anhängt oder die
# Datei neu schreibt, ist ihm überlassen — `react` hat nur `text_editor`, und
# der kann eine Datei bequem ganz schreiben, aber nicht anhängen.
ANSWER_FILE = f"{CLI_WORKDIR}/OUT.jsonl"

# Woher die gewertete Antwort kam (`Score.metadata.answer_source`).
FROM_FILE = "findings_file"
FROM_FINAL = "final_answer"


def findings_from_lines(text: str) -> str | None:
    """Die Befundliste (JSON Lines) als JSON-Array — oder `None`, wenn sie nichts enthält.

    Jede Zeile wird für sich gelesen. Eine Zeile, die kein JSON ist, kommt als
    Text ins Array und erscheint damit in der Wertung als verworfene Meldung
    („kein Objekt“). Ein Komma am Zeilenende wird übergangen, ebenso ein
    Array in einer Zeile oder in der ganzen Datei: Die Verpackung ist nicht,
    was gemessen wird (dieselbe Toleranz wie bei der Schlussantwort).

    `None` heißt: kein einziges Objekt. Dann gilt die Schlussantwort — eine
    leere oder ganz kaputte Datei soll eine gültige Schlussantwort nicht
    verdrängen.
    """
    try:
        ganz = json.loads(text)
    except ValueError:
        ganz = None
    if isinstance(ganz, list):
        eintraege = ganz
    else:
        eintraege = []
        for zeile in text.splitlines():
            zeile = zeile.strip().rstrip(",").strip()
            if not zeile or zeile in ("[", "]"):
                continue
            try:
                wert = json.loads(zeile)
            except ValueError:
                eintraege.append(zeile)
                continue
            eintraege.extend(wert if isinstance(wert, list) else [wert])
    if not any(isinstance(eintrag, dict) for eintrag in eintraege):
        return None
    return json.dumps(eintraege, ensure_ascii=False)


def choose_answer(file_text: str | None, final_text: str) -> tuple[str, str]:
    """Welche Antwort gewertet wird: die Befundliste, sonst die Schlussantwort.

    Die Datei gilt, sobald sie mindestens einen Befund als JSON-Objekt
    enthält (`findings_from_lines`). Die Schlussantwort ist der Rückfall für
    einen Agenten, der die Datei nie geschrieben hat. Umgekehrt ginge es
    nicht: Ein Agent-CLI schreibt auf stdout auch Bruchstücke seiner
    Werkzeugaufrufe, und das erste JSON darin ist nicht zwingend die Liste.
    """
    liste = findings_from_lines(file_text) if file_text is not None else None
    if liste is not None:
        return liste, FROM_FILE
    return final_text, FROM_FINAL


async def read_answer_file() -> str | None:
    """Die Befundliste aus dem Container, oder `None`, wenn es keine gibt.

    `ProcessLookupError` heißt: kein Container — der Oracle-Lauf. Die übrigen
    Fehler heißen, dass der Agent die Datei nicht oder unbrauchbar hinterlassen
    hat. Beides ist kein Fehler des Laufs, sondern eine Auskunft.
    """
    try:
        return await sandbox().read_file(ANSWER_FILE)
    except (
        ProcessLookupError,
        FileNotFoundError,
        PermissionError,
        IsADirectoryError,
        UnicodeDecodeError,
        OutputLimitExceededError,
    ):
        return None


async def graded_answer(state: TaskState) -> dict[str, Any]:
    """Die gewertete Antwort und ihre Bewertung — für Scorer und Bericht.

    Beide Aufrufer brauchen dieselbe Antwort, sonst stünde im PDF eine andere
    Zahl als im Log. Die Datei ändert sich nach dem Agenten nicht mehr, zwei
    Lesevorgänge ergeben also dasselbe.
    """
    meta = state.metadata
    issues = [entry["issue"] for entry in meta["catalog"]]
    final_text = state.output.completion if state.output else ""
    text, source = choose_answer(await read_answer_file(), final_text)
    bewertung = evaluate_audit(text, meta)
    bewertung["answer"] = text
    bewertung["answer_source"] = source
    # Ob die Schlussantwort selbst auswertbar war. Kam die Wertung aus der
    # Datei und die Schlussantwort fehlt, ist der Lauf vorzeitig zu Ende
    # gegangen — das gehört in den Bericht und in die Begründung.
    bewertung["final_answer_error"] = parse_findings(
        final_text, meta["pages"], issues
    )[2]
    return bewertung


# Die Kommandozeile von goose. Jeder Schalter einzeln begründet, weil jeder
# einzelne eine Messbedingung ist:
#
#   --no-session    Kein Sitzungsverlauf in HOME. Ein zweiter Lauf im selben
#                   Container würde sonst auf dem ersten aufsetzen.
#   --no-profile    Nur die hier genannten Erweiterungen. Ohne das lädt goose
#                   die `config.yaml` des Benutzers — im Image gibt es keine,
#                   aber dann entschiede die Umgebung mit, was gemessen wird.
#   --with-extension  chrome-devtools-mcp, mit DENSELBEN Flags wie im
#                   react-Pfad (`MCP_ARGS`). Die Werkzeug-Verdrahtung wandert
#                   damit vom Inspect-Task in die CLI-Konfiguration, aber der
#                   Browser und der Viewport bleiben dieselben — sonst wäre der
#                   Frameworkvergleich ein Vergleich zweier Browser.
#   --with-builtin developer  Datei- und Shell-Werkzeuge. Sie sind der Weg, auf
#                   dem das CLI die Befundliste führt (`ANSWER_FILE`) — ein CLI
#                   hat kein `submit`. Dass damit auch `shell` im Werkzeugkasten
#                   liegt, ist eine Eigenschaft dieses Frameworks und keine
#                   Nachlässigkeit: Schwache Modelle weichen darüber auf curl
#                   aus statt zu rendern. Genau das soll die Achse zeigen; `tool_calls` im Log macht es
#                   sichtbar.
#   --max-turns     Das Gegenstück zu `MESSAGE_LIMIT` im react-Pfad.
#   -q              Nur die Modellantwort auf stdout. Sie ist die Rückfallebene,
#                   falls keine Befundliste geschrieben wurde.
GOOSE_MAX_TURNS = 150


def goose_command(prompt_text: str = "") -> list[str]:
    """Die Kommandozeile, mit der goose im Container startet.

    Der Auftrag geht als `-t` mit und nicht über eine Datei: Er ist derselbe
    Text, den der react-Pfad als erste Nachricht bekommt, und soll auch genau
    so im Log stehen.
    """
    extension = " ".join([f"chrome-devtools:{MCP_COMMAND}", *MCP_ARGS])
    return [
        "goose",
        "run",
        "--no-session",
        "--no-profile",
        "-q",
        "--provider",
        "openai",
        # Der Modellname, unter dem der Bridge-Proxy an den Provider des Laufs
        # weiterreicht. Welches Modell das tatsächlich ist, steht im Log —
        # hier steht nur die Adresse.
        "--model",
        "inspect",
        "--with-extension",
        extension,
        "--with-builtin",
        "developer",
        "--max-turns",
        str(GOOSE_MAX_TURNS),
        "-t",
        prompt_text,
    ]


def goose_env(port: int, budget: ContextBudget) -> dict[str, str]:
    """Die Umgebung des CLI. Alles, was es über das Modell weiß, steht hier.

    `OPENAI_HOST` zeigt auf den Bridge-Proxy im eigenen Container — kein
    Schlüssel und keine Adresse eines echten Anbieters verlässt damit den
    Benchmark, und der Lauf bleibt im `internal`-Netz.
    """
    return {
        "OPENAI_HOST": f"http://localhost:{port}",
        "OPENAI_API_KEY": "inspect",
        # Werkzeuge ohne Rückfrage. Der Container IST die Sandbox: kein Mount,
        # kein Weg nach draußen, die Prüflandschaft read-only.
        "GOOSE_MODE": "auto",
        # Ohne das sucht goose im Container einen Schlüsselbund und scheitert.
        "GOOSE_DISABLE_KEYRING": "1",
        # Das Fenster, mit dem goose rechnet. Für den Modellnamen `inspect`
        # kennt es keines und nimmt 128.000 Tokens an — dann verdichtet es
        # erst ab gut 100.000 und läuft vorher in jedes 64k-Fenster. Genannt
        # wird `input_budget`, nicht das ganze Fenster: Die Antwortreserve
        # nimmt Inspect jeder Anfrage ohnehin weg. Das ist eine Angabe zum
        # Aufbau wie die Serveradresse; wann und wie goose verdichtet, bleibt
        # seine Sache und ist Teil dessen, was diese Achse misst.
        "GOOSE_CONTEXT_LIMIT": str(budget.input_budget),
        "HOME": CLI_WORKDIR,
    }


# pi (pi.dev) läuft so, wie ein typischer pi-Nutzer es einrichtet. Das betrifft
# vor allem den Browser: pi reicht MCP-Werkzeuge in seiner Vorgabe nicht einzeln
# an das Modell weiter, sondern über `codemode`. Das Modell schreibt dort
# JavaScript, das die Werkzeuge als `tools.mcp__chrome_devtools__click(…)`
# aufruft. Gemessen wird damit das Konzept des Frameworks, nicht nur sein Loop.
#
# Die Schalter:
#
#   --print         Auftrag abarbeiten, Schlussantwort auf stdout, beenden.
#   --no-session    Kein Sitzungsverlauf auf der Platte, wie bei goose.
#   --no-context-files / --no-skills / --no-prompt-templates
#                   Nichts aus der Umgebung lesen (AGENTS.md, Skills, Vorlagen).
#                   Im Image gibt es nichts davon, aber dann entschiede die
#                   Umgebung mit, was gemessen wird.
#   --model         Der Bridge-Proxy aus `pi_config`.
#
# Erweiterungen bleiben an: MCP und `codemode` SIND eingebaute Erweiterungen
# von pi, `--no-extensions` schaltete den Browser ab. Eine Grenze wie
# `--max-turns` kennt pi nicht; es gilt `TIME_LIMIT`.
PI_AGENT_DIR = f"{CLI_WORKDIR}/.pi/agent"
PI_MODEL = "inspect/inspect"


def pi_command(prompt_text: str = "") -> list[str]:
    """Die Kommandozeile, mit der pi im Container startet."""
    return [
        "pi",
        "--print",
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--model",
        PI_MODEL,
        prompt_text,
    ]


def pi_config(port: int, budget: ContextBudget) -> dict[str, dict[str, Any]]:
    """Die Konfigurationsdateien von pi, je Dateiname ihr Inhalt.

    `models.json` hängt pi an den Bridge-Proxy, `mcp.json` an denselben
    Browser wie im react-Pfad (`MCP_ARGS`). `exposure` bleibt bei der Vorgabe
    `codemode`, siehe oben.

    `contextWindow` ist das ganze Fenster: pi verdichtet, sobald der Kontext
    `contextWindow - reserveTokens` überschreitet, und `reserveTokens` ist in
    seiner Vorgabe 16.384 Tokens, also `ANSWER_MAX_TOKENS`. Die Schwelle ist
    damit `input_budget`, wie bei goose. `cost` verlangt pi, für die Bridge
    ist sie null.
    """
    provider, model = PI_MODEL.split("/")
    return {
        "models.json": {
            "providers": {
                provider: {
                    "baseUrl": f"http://localhost:{port}/v1",
                    "api": "openai-completions",
                    "apiKey": "inspect",
                    "models": [
                        {
                            "id": model,
                            "contextWindow": budget.window,
                            "maxTokens": ANSWER_MAX_TOKENS,
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                        }
                    ],
                }
            }
        },
        "mcp.json": {
            "mcpServers": {
                "chrome-devtools": {
                    "command": MCP_COMMAND,
                    "args": MCP_ARGS,
                    # pi bricht einen MCP-Aufruf sonst nach 60 s ab; ein Trace
                    # braucht mehr (`MCP_TIMEOUT`).
                    "timeout": MCP_TIMEOUT,
                }
            }
        },
    }


def pi_env() -> dict[str, str]:
    """Die Umgebung von pi: sein Konfigurationsverzeichnis, kein Weg nach draußen.

    Versionsprüfung, Katalogabgleich und Telemetrie gingen ins Netz. Das
    `internal`-Netz ließe sie ohnehin scheitern, aber erst nach einer
    Wartezeit.
    """
    return {
        "PI_CODING_AGENT_DIR": PI_AGENT_DIR,
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
        "HOME": CLI_WORKDIR,
    }


def prompt(inputs: dict[str, Any]) -> str:
    """Was das Modell sieht. Bekommt NUR den Eingabe-Teil des Datensatzes.

    Der Fehlerkatalog steht hier drin und ist deshalb Eingabe, keine Referenz:
    Ohne ihn wäre die Aufgabe Vokabelraten — ein Agent könnte denselben Befund
    "lazy-geladenes LCP-Bild" nennen und wäre nicht zu werten. WELCHE Seite
    welchen Fehler hat, steht nicht im Prompt; das ist die Aufgabe. Die
    Seitenliste (`pages`) steht ebenfalls nicht darin, siehe `START_PAGE`.

    Die Werkzeugzuordnung sagt, was die Werkzeuge leisten, nicht, wonach zu
    suchen ist. So bekäme ein Agent sie auch in der Praxis mit. Prüfschritte
    stehen bewusst nicht darin: Welche Bedingung eine Seite ändert, herauszufinden
    ist Teil der Aufgabe (`test_die_werkzeuge_verraten_keine_falle`).
    """
    catalog = "\n".join(
        f"- \"{entry['issue']}\": {entry['description']}" for entry in inputs["catalog"]
    )
    return (
        "Diese kleine Website rankt schlecht und wirkt im Betrieb träge. Sie "
        f"beginnt unter {SITE_URL}{START_PAGE}. Prüfe alle ihre Seiten mit den "
        "Browser-Werkzeugen auf technische SEO- und Performance-Fehler.\n\n"
        "Wichtig: Beurteile nicht den HTML-Quelltext allein, sondern das, was "
        "der Browser daraus macht — das DOM nach dem Ausführen von JavaScript, "
        "die Netzwerk-Requests samt nachgeladener Ressourcen, Weiterleitungen, "
        "die Stabilität des Layouts nach dem Laden und das Verhalten bei "
        "Interaktion. Ein Teil der Fehler zeigt sich erst, wenn du eine Seite "
        "scrollst oder ein Bedienelement anklickst.\n\n"
        f"Beurteile im Desktop-Viewport {VIEWPORT_WIDTH} x {VIEWPORT_HEIGHT} "
        "Pixel (Breite x Höhe). Das ist keine Nebensache: Layout-Verschiebung "
        "und die Frage, welches Bild im ersten sichtbaren Bereich liegt, hängen "
        "an der Fenstergröße — die Bewertung ist an genau diesen Viewport "
        "gebunden.\n\n"
        "Was die Browser-Werkzeuge leisten:\n"
        "- take_snapshot zeigt die sichtbaren Elemente der Seite mit einer uid, "
        "nicht aber den <head> (Title, Meta-Angaben, Canonical). Die uid "
        "brauchst du für click, hover und fill.\n"
        "- evaluate_script führt JavaScript in der geladenen Seite aus. Damit "
        "liest du den <head> und alles, was der Snapshot nicht zeigt, aus dem "
        "DOM nach dem Rendern, und damit scrollst du.\n"
        "- list_network_requests und get_network_request zeigen die Anfragen "
        "seit dem letzten Laden: Status, Header, Weiterleitungen und den Inhalt "
        "der Antwort, auch nachgeladener Skripte und Ressourcen.\n"
        "- navigate_page lädt eine Adresse oder lädt die Seite neu, new_page "
        "öffnet einen weiteren Tab.\n"
        "- emulate ändert Eigenschaften des Browsers für die Seite, etwa "
        "User-Agent, Netzbedingungen oder CPU-Drosselung. Den Viewport lässt "
        "du dabei unverändert.\n"
        "- performance_start_trace zeichnet das Laden auf und nennt LCP und CLS "
        "samt den beteiligten Elementen.\n"
        "- list_console_messages zeigt Fehler und Meldungen der Seite.\n"
        "- lighthouse_audit liefert allgemeine Bewertungen, keine Befunde im "
        "Sinne des Katalogs unten.\n"
        "Nutze das passende Werkzeug, bevor du eigenen Code schreibst.\n\n"
        "Verwende AUSSCHLIESSLICH diesen Fehlerkatalog (nur diese Ids):\n"
        f"{catalog}\n\n"
        "Eine Seite kann mehrere Probleme haben, keines, oder dasselbe Problem "
        "wie eine andere Seite. Melde NUR tatsächlich vorhandene Probleme — ein "
        "gemeldetes Problem, das es nicht gibt, zählt genauso gegen dich wie ein "
        "übersehenes.\n\n"
        "Jeder Befund ist ein JSON-Objekt: "
        '{"page": "<dateiname>", "issue": "<id aus dem Katalog>"}. Ein Objekt '
        "je Seite und Problem, sonst nichts. Die Seite steht als Dateiname "
        f"ohne Pfad da, die Startseite also als {START_PAGE}.\n\n"
        # Derselbe Absatz für alle Gerüste: Jedes hat Dateiwerkzeuge (`react`
        # bekommt `text_editor`), und die Datei ist das, was gewertet wird
        # (`choose_answer`). Zwei Prompts nebeneinander hätten den
        # Frameworkvergleich still um eine zweite geänderte Größe erweitert.
        f"Führe deine Befunde laufend in der Datei {ANSWER_FILE}, ein Objekt "
        "je Zeile (JSON Lines). Trage jeden Befund ein, sobald er feststeht — "
        "ob du dazu Zeilen anhängst oder die Datei neu schreibst, ist dir "
        "überlassen. Die Datei ist dein Ergebnis und wird gewertet, auch wenn "
        "die Prüfung vorzeitig endet. Dein Kontextfenster ist begrenzt; was "
        "nicht in der Datei steht, kann verloren gehen.\n\n"
        "Wenn du fertig bist, gib alle Befunde zusätzlich als JSON-Array in "
        "der Schlussantwort ab."
    )


@agent
def audit_agent() -> Agent:
    """Der Agent: Werkzeuge aus chrome-devtools-mcp, Antwort über `submit`.

    Der MCP-Server läuft **im Sandbox-Container** (`mcp_server_sandbox`), nicht
    auf dem Host. Das ist der Kern der Topologie: Chrome, der Agent und die
    Prüflandschaft liegen in Containern, der Scorer und die Referenz im
    Inspect-Prozess. Ein Server auf dem Host wäre bequemer und hätte Zugriff auf
    dasselbe Dateisystem, in dem die Referenz liegt.

    `mcp_connection` hält eine Sitzung über den ganzen Lauf: Ohne sie würde je
    Werkzeugaufruf ein Chrome gestartet, und Zustand über mehrere Aufrufe hinweg
    — geöffnete Seite, Scrollposition, laufende Aufzeichnung — gäbe es nicht.
    Damit wäre genau die Hälfte der Aufgabe unlösbar.

    **Keine Kontextpflege.** Der Benchmark verdichtet und kürzt nicht: Wie
    sparsam ein Modell mit seinem Fenster umgeht, ist Teil der Messung. Läuft
    eine Anfrage über (der Server meldet es, `local/` übersetzt die Meldung),
    beendet `react()` den Agenten sauber, und gewertet wird die Befundliste.
    `plan_context` ermittelt das Fenster nur fürs Log.

    `text_editor` ist das Dateiwerkzeug für die Befundliste (`ANSWER_FILE`).
    Mehr bekommt der Agent nicht dazu, insbesondere keine Shell: Der
    react-Pfad misst das Modell am Browser, nicht an curl.
    """

    async def execute(state: AgentState) -> AgentState:
        await plan_context()
        server = mcp_server_sandbox(
            name="chrome-devtools",
            command=MCP_COMMAND,
            args=MCP_ARGS,
            timeout=MCP_TIMEOUT,
        )
        async with mcp_connection(server):
            return await react(
                name="browser_audit",
                prompt=AgentPrompt(instructions=SYSTEM),
                tools=[server, text_editor()],
            )(state)

    return execute


# Schlüssel im Store, über den der Agent dem Scorer mitteilt, wie das CLI
# geendet hat. Der Scorer sieht `TaskState`, nicht die Innereien des Agenten —
# und "das CLI ist mit rc=1 gestorben" gehört in die Wertung, nicht in den
# Verdacht.
# Vor dem Start steht dort `finished: False`. Endet der Datensatz an
# `TIME_LIMIT`, kehrt das CLI nie zurück, und genau dieser Eintrag bleibt
# stehen.
STORE_CLI = "a01_cli"


@agent
def cli_agent(cli: str = AGENT_GOOSE) -> Agent:
    """Ein Agent-CLI im Container (goose oder pi), angebunden über `sandbox_agent_bridge`.

    Der Ablauf ist absichtlich kurz: Bridge hochfahren, das CLI im Container
    starten, Antwort einsammeln. pi bekommt vorher seine Konfigurationsdateien
    (`pi_config`), goose alles über Kommandozeile und Umgebung. Alles Weitere — Werkzeuge,
    Systemprompt, Kontextpflege — macht das CLI, und genau das ist der
    Messgegenstand dieser Achse.

    **Die Bridge verdichtet nicht.** Kontextpflege ist Sache des CLI und Teil
    dessen, was diese Achse misst; es erfährt nur das Fenster (`goose_env`,
    `pi_config`).
    Inspects Compaction in der Bridge wäre zudem falsch: Sie führt einen
    einzigen Verlauf und hängt jede neue Nachricht daran an — auch die einer
    Nebenanfrage. goose fragt nebenher nach einem Titel für die Sitzung, und
    diese Anweisung stand dann in jeder folgenden Anfrage des Audits.

    **Woher die Antwort kommt** entscheidet nicht dieser Agent, sondern
    `choose_answer`: die Befundliste, sonst die letzte Ausgabe auf stdout
    (goose mit `-q` und pi mit `--print` lassen dort nur Modellantworten
    übrig). Hier wird stdout nur als
    Schlussantwort hinterlegt.

    **Der Gesprächsverlauf kommt aus der Bridge** (`bridge.state.messages`),
    nicht aus dem CLI-Log. Werkzeugaufrufe des CLI landen dort als
    Assistenten-Nachrichten mit `tool_calls` — `clicked`, `scrolled` und
    `ua_switched` tragen damit auch auf diesem Pfad.
    """

    async def execute(state: AgentState) -> AgentState:
        # Der Auftrag ist die letzte Nachricht des Zustands — dieselbe, die
        # `react()` als Eingabe bekommt. Fehlt sie, startet das CLI ohne
        # Aufgabe, und das sieht so aus:
        # `rc=1, Must provide either --instructions (-i), --text (-t) …`,
        # und ohne die CLI-Felder im Score stünde da nur eine Null.
        prompt_text = "\n\n".join(
            message.text
            for message in state.messages
            if message.role == "user" and message.text
        )
        budget = await plan_context()
        async with sandbox_agent_bridge(state) as bridge:
            if cli == AGENT_PI:
                for name, inhalt in pi_config(bridge.port, budget).items():
                    await sandbox().write_file(
                        f"{PI_AGENT_DIR}/{name}", json.dumps(inhalt, indent=2)
                    )
                cmd, env = pi_command(prompt_text), pi_env()
            else:
                cmd = goose_command(prompt_text)
                env = goose_env(bridge.port, budget)
            store().set(STORE_CLI, {"returncode": None, "finished": False})
            # Ohne eigene Zeitgrenze: Es gilt `TIME_LIMIT` wie im react-Pfad.
            result = await sandbox().exec(cmd=cmd, env=env, cwd=CLI_WORKDIR)
            store().set(
                STORE_CLI,
                {
                    "returncode": result.returncode,
                    "finished": True,
                    # Der Grund, warum das hier steht: Ein CLI, das gar nicht
                    # startet (fehlendes Binary, kaputte Erweiterung), liefert
                    # dieselbe Null wie ein Modell, das nichts findet. Ohne
                    # diese zwei Felder schickt man die Suche zum Modell statt
                    # zum Container.
                    "stderr_tail": (result.stderr or "")[-2000:],
                    "stdout_tail": (result.stdout or "")[-2000:],
                },
            )

            state = bridge.state
            state.output = ModelOutput.from_content(
                model=str(get_model().name), content=result.stdout or ""
            )
        return state

    return execute


# --------------------------------------------------------------------------
# Der Bericht — Beiwerk für Menschen, ausdrücklich nicht Teil der Wertung
# --------------------------------------------------------------------------
#
# Warum das ein EIGENER Schritt nach dem Audit ist und nicht Teil des Auftrags:
# Der Prompt des Audits verlangt ein JSON-Array und sonst nichts. Stünde der
# Wunsch nach einem Bericht mit darin, müsste ein Modell zwei Dinge zugleich
# liefern — und gerade kleine Modelle vermischen das dann (Fließtext um das
# JSON herum, oder ein Bericht statt der Liste). Gemessen wird die Analyse;
# der Bericht ist die Zugabe für den Menschen, der die Zahlen später liest.
#
# Deshalb drei Trennungen, und jede hat einen Grund:
#
#   1. NACH der Antwort. Der Scorer liest `state.output`, und dieser Schritt
#      fasst es nicht an — er stellt es hinterher wieder her. Eine Meldung,
#      die im Bericht steht, aber nicht im JSON, zählt nicht.
#   2. In einem FRISCHEN, kurzen Kontext. Das Modell bekommt seine eigene
#      JSON-Antwort zurückgereicht, nicht die ganze Browser-Sitzung. Die ist
#      bei 26 Seiten riesig, und ein kleines Modell hätte am Ende des Audits
#      kein Fenster mehr für einen Bericht. Nebenbei ist das genau die
#      Arbeitsteilung eines echten Beraters: erst messen, dann berichten.
#   3. FEHLER SIND FOLGENLOS. Der Schritt fängt alles ab. Ein Modell, das
#      keinen Bericht zustande bringt, verliert keinen Punkt — es fehlt nur
#      eine PDF-Datei, und warum, steht im Log.
#
# Die Umwandlung nach PDF macht nicht das Modell, sondern der Chromium, der
# ohnehin im Container liegt. Ein Modell, das PDF-Bytes erzeugen soll, ist ein
# Modell, das scheitert; HTML kann jedes.

STORE_REPORT = "a01_report"

# Wo Bericht und PDF im Container entstehen. Dasselbe Verzeichnis, in dem auch
# das CLI arbeitet — es ist beschreibbar und existiert in beiden Pfaden.
REPORT_HTML = f"{CLI_WORKDIR}/bericht.html"
REPORT_PDF = f"{CLI_WORKDIR}/bericht.pdf"


# Wohin das fertige PDF auf dem HOST wandert. Im Container bringt es niemandem
# etwas: Der wird nach dem Lauf weggeworfen.
REPORT_DIR_ENV = "GERMAN_SEO_BENCH_REPORT_DIR"
REPORT_DIR_DEFAULT = "reports"

# Wohin der Bewertungskasten verweist. Ohne diesen Zeiger steht in einem
# Kundendokument eine Tabelle mit `second_load` und `request` darin, und
# niemand weiß, was das heißt — die Begriffe sind Benchmark-Vokabular und
# nirgends im Bericht erklärt.
#
# Ein Link und kein Repo-Pfad: Der Bericht geht an jemanden ohne Checkout.
# Er zeigt auf das Tag der Paketversion, mit der der Bericht entstand — auf
# `main` stünde nach der nächsten Änderung eine Erklärung, die zu diesem
# Bericht nicht mehr passt.
# Ein Entwicklungsstand (`2026.2.dev0`) hat kein Tag; dann zeigt der Link auf
# `main`.
REPORT_DOCS_PATH = "docs/A01.md"
_PAKETVERSION = version("german-seo-bench")
REPORT_DOCS = (
    "https://github.com/stefanfis/german-seo-bench/blob/"
    + ("main" if "dev" in _PAKETVERSION else f"v{_PAKETVERSION}")
    + f"/{REPORT_DOCS_PATH}"
)

# Zeitgrenze für den Chromium-Aufruf. Großzügig, weil ein kaltes Chromium im
# Container ein paar Sekunden braucht — aber endlich, damit ein hängender
# Druckauftrag nicht den Lauf blockiert.
REPORT_RENDER_TIMEOUT = 120


def report_prompt(findings: str, pages: list[str]) -> str:
    """Der Auftrag für den Bericht. Bekommt die eigene Antwort zurückgereicht.

    Bewusst ausführlich in dem, was der Bericht LEISTEN soll, und knapp in der
    Form: Was ein technischer SEO seinem Kunden hinlegt, ist nicht die
    JSON-Liste mit anderen Klammern, sondern eine Einordnung — was ist
    schlimm, was kostet Sichtbarkeit, was macht man zuerst.

    **Warum Markdown und nicht HTML.** Bei gleichem Inhalt braucht ein
    HTML-Bericht etwa doppelt so viele Ausgabe-Tokens wie Markdown: Fast die
    Hälfte geht für `<style>`-Block und Tags drauf, also für Markup, das wir
    selbst beisteuern können. Bei einem lokalen Modell ist das Minuten
    Erzeugungszeit. Der Druck nach PDF kostet in beiden Fällen nichts
    Nennenswertes; die Länge der Antwort ist der Kostenfaktor. Dazu kommt ein
    zweiter Gewinn: Mit unserem eigenen Stylesheet sieht der Bericht bei jedem Modell
    gleich aus, statt dass jedes Modell sein eigenes CSS erfindet — und
    Markdown bringt ein kleines Modell zuverlässiger zustande als ein
    vollständiges HTML-Dokument.

    **Nur der eigene Befund** — das Modell soll nichts dazuerfinden, was es
    nicht gemeldet hat.
    """
    return (
        "Danke, das Audit ist abgeschlossen. Jetzt noch eine zweite, davon "
        "unabhängige Aufgabe — sie geht NICHT in die Bewertung ein.\n\n"
        "Das hier ist das Ergebnis, das du gerade abgegeben hast:\n\n"
        f"{findings}\n\n"
        f"Geprüft wurden diese Seiten von {SITE_URL}:\n"
        f"{', '.join(pages)}\n\n"
        "Mach daraus einen Bericht, wie ihn ein technischer SEO seinem Kunden "
        "übergibt. Der Kunde ist kein Entwickler: Er will wissen, was kaputt "
        "ist, warum ihn das Sichtbarkeit kostet und was er zuerst anpacken "
        "soll.\n\n"
        "Inhaltlich erwartet:\n"
        "- eine kurze Zusammenfassung an den Anfang (zwei bis drei Sätze, "
        "keine Aufzählung): Wie steht die Website da?\n"
        "- die Befunde nach Dringlichkeit gruppiert, nicht nach Seite — "
        "Indexierung und Crawling wiegen schwerer als Ladeverhalten\n"
        "- je Befund: betroffene Seite(n), was beobachtet wurde, welche "
        "Auswirkung das auf die Suche hat, und die konkrete Empfehlung\n"
        "- eine Tabelle der Befunde als Überblick\n"
        "- zum Schluss die Reihenfolge der Maßnahmen\n\n"
        "Schreib auf Deutsch, sachlich und ohne Marketing-Ton. Erfinde nichts "
        "dazu: Es gehört genau das in den Bericht, was du oben gemeldet hast. "
        "Wenn du nichts gefunden hast, sag das.\n\n"
        "Formal: Gib **Markdown** aus — Überschriften mit `#`, Listen mit `-`, "
        "die Übersichtstabelle mit `|`. Kein HTML und kein CSS: Das Layout "
        "kommt von uns, du schreibst nur den Inhalt. Beginne mit einer "
        "`#`-Überschrift. Gib nur den Bericht aus, keinen Text davor oder "
        "danach und keinen Codeblock darum."
    )


# Ab welcher Länge und Form eine Antwort als Bericht durchgeht. Beides ist
# nötig: Ein Modell, das „Ich kann keinen Bericht erstellen" antwortet, darf
# kein PDF erzeugen — ein leeres PDF sähe aus, als hätte es geliefert.
REPORT_MIN_CHARS = 200
# Der Zaun, falls das Modell den ganzen Bericht in einen Codeblock packt.
_FENCE = re.compile(r"\A\s*```[\w-]*\s*\n(?P<inhalt>.*?)\n\s*```\s*\Z", re.DOTALL)
# Mindestens eine Markdown-Überschrift am Zeilenanfang.
_HEADING = re.compile(r"^#{1,6} \S", re.MULTILINE)


def extract_markdown(text: str) -> str | None:
    """Den Bericht aus der Modellantwort holen.

    Markdown hat keine Klammern wie `<html>…</html>`, an denen man schneiden
    könnte — deshalb wird nicht geschnitten, sondern **geprüft**: Steckt der
    ganze Bericht in einem Codeblock, fällt der Zaun weg; danach muss das
    Ergebnis lang genug sein und mindestens eine Überschrift tragen.

    Gibt `None` zurück, wenn das nicht zutrifft. Bewusst kein Notbehelf, der
    irgendeinen Text durchlässt: Dann entstünde ein PDF, das aussieht, als
    hätte das Modell einen Bericht geliefert.

    Eine Vorrede („Gern! Hier ist der Bericht:") wird NICHT entfernt. Sie zu
    erkennen hieße zu raten, wo der Bericht anfängt — und eine Zeile Höflichkeit
    über einer Überschrift ist ein Schönheitsfehler, ein falsch abgeschnittener
    Bericht ein inhaltlicher.
    """
    if not text:
        return None
    inhalt = text.strip()
    zaun = _FENCE.match(inhalt)
    if zaun:
        inhalt = zaun.group("inhalt").strip()
    if len(inhalt) < REPORT_MIN_CHARS or not _HEADING.search(inhalt):
        return None
    return inhalt


# Das Stylesheet des Berichts. Es liegt HIER und nicht im Modell, und das ist
# eine Kostenentscheidung: Ein `<style>`-Block vom Modell kostet Ausgabe-Tokens
# und damit Laufzeit (siehe `report_prompt`). Dazu kommt, dass der Bericht damit bei
# jedem Modell gleich aussieht — sonst vergleicht man beim Durchblättern zweier
# Berichte zwei Geschmäcker statt zwei Analysen.
REPORT_CSS = """
@page { size: A4; margin: 20mm 18mm; }
html { font-size: 11pt; }
body {
  font-family: "Liberation Serif", Georgia, serif;
  line-height: 1.45; color: #1a1a1a; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3 { font-family: "Liberation Sans", Helvetica, Arial, sans-serif; line-height: 1.25; }
h1 { font-size: 1.9rem; margin: 0 0 .2rem; }
h2 { font-size: 1.3rem; margin: 1.8rem 0 .4rem; padding-bottom: .2rem;
     border-bottom: 1.5pt solid #1a1a1a; break-after: avoid; }
h3 { font-size: 1.05rem; margin: 1.1rem 0 .3rem; break-after: avoid; }
p, ul, ol, table { margin: 0 0 .7rem; }
ul, ol { padding-left: 1.3rem; }
li { margin-bottom: .2rem; }
strong { font-weight: 700; }
code { font-family: "Liberation Mono", monospace; font-size: .9em;
       background: #f0f0f0; padding: .05rem .2rem; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { border: .5pt solid #999; padding: .3rem .45rem; text-align: left;
         vertical-align: top; }
th { background: #ececec; font-family: "Liberation Sans", Helvetica, sans-serif; }
tr, h1, h2, h3, li { break-inside: avoid; }
blockquote { margin: 0 0 .7rem; padding-left: .8rem; border-left: 2pt solid #bbb;
             color: #444; }
hr { border: none; border-top: .5pt solid #bbb; margin: 1.2rem 0; }
.kopf { font-family: "Liberation Sans", Helvetica, sans-serif; font-size: .85rem;
        color: #555; margin: 0 0 1.6rem; }

/* Der Bewertungskasten. Rot umrandet, weil er NICHT zum Bericht gehört: Er
   kommt vom Benchmark und sagt, was der Bericht darüber wert ist. Ein Leser
   soll ihn auf den ersten Blick vom Text des Modells unterscheiden. */
.bewertung { border: 2pt solid #b00020; background: #fdf4f5; padding: .7rem .9rem;
             margin: 1.4rem 0; break-inside: avoid;
             font-family: "Liberation Sans", Helvetica, sans-serif;
             font-size: .88rem; color: #1a1a1a; }
.bewertung h2 { font-size: 1rem; margin: 0 0 .4rem; border: none; padding: 0;
                color: #b00020; }
.bewertung p { margin: 0 0 .45rem; }
.bewertung p:last-child { margin-bottom: 0; }
.bewertung table { margin: .5rem 0; font-size: .85rem; }
.bewertung th, .bewertung td { border-color: #d9b3ba; }
.bewertung th { background: #f6e7ea; }
.bewertung .zahl { font-variant-numeric: tabular-nums; font-weight: 700; }
.bewertung .fussnote { font-size: .8rem; color: #555; }
.bewertung .stoerung { background: #b00020; color: #fff; padding: .4rem .55rem;
                       margin: 0 0 .55rem; }
"""


# Überschriften, vor denen der Bewertungskasten NICHT stehen soll. Der Kasten
# gehört hinter die Zusammenfassung und vor den ersten Befundabschnitt — ein
# Leser soll erst lesen, was das Modell gefunden haben will, und dann, was
# davon stimmt.
_ZUSAMMENFASSUNG = re.compile(
    r"zusammenfassung|überblick|ueberblick|executive|kurzfassung|fazit|einleitung",
    re.IGNORECASE,
)


def punkt_komma(wert: float) -> str:
    """0.13 → „0,13". Der Bericht geht an einen deutschen Kunden.

    Eine Kleinigkeit mit Folgen: Steht im selben Satz „Score 0.13 von 1,00",
    zweifelt, wer das im Kundendokument liest, an allem anderen darin mit.
    """
    return f"{wert:.2f}".replace(".", ",")


def run_notes(state: TaskState, bewertung: dict[str, Any]) -> list[str]:
    """Was an diesem Lauf nicht normal war — in Sätzen für einen Kunden.

    **Was hier NICHT stehen kann, und warum.** Ein harter Abbruch (Zeitgrenze,
    Nachrichtengrenze, Netzwerkfehler) beendet den Datensatz, bevor dieser
    Schritt läuft — dann entsteht gar kein Bericht. Die Abwesenheit des PDF ist
    dort das Signal, und der Grund steht in der Ergebnisdatei unter
    `report.status` beziehungsweise als Fehler des Datensatzes. Hier stehen
    deshalb nur die Störungen, die einen Bericht **überleben**: Der Lauf ist
    weitergelaufen, hat aber unterwegs etwas verloren.

    Genau die sind auch die gefährlicheren. Ein abgebrochener Lauf fällt auf;
    ein Lauf, der neunmal seinen Kontext gekürzt hat und danach einen sauber
    formulierten Bericht abliefert, sieht aus wie ein normales Ergebnis.
    """
    notizen: list[str] = []

    ueberlaeufe = int(state.store.get(STORE_CONTEXT_OVERFLOWS, 0) or 0)
    if ueberlaeufe:
        notizen.append(
            f"Das Kontextfenster des Modells lief {ueberlaeufe}× über. Was der "
            "Agent danach noch prüfen konnte, hing davon ab, wie sein Gerüst "
            "damit umging; gewertet wurde die Befundliste, die er bis dahin "
            "geführt hatte."
        )

    kontext = state.store.get(STORE_CONTEXT, None)
    if kontext and kontext["window"] < MIN_CONTEXT:
        fenster = f"{kontext['window']:,}".replace(",", ".")
        minimum = f"{MIN_CONTEXT:,}".replace(",", ".")
        notizen.append(
            f"Das Kontextfenster des Modells hatte nur {fenster} Tokens, "
            f"weniger als die {minimum}, für die diese Prüfung ausgelegt ist. "
            "Der Platz für Beobachtungen war entsprechend knapp."
        )

    cli = state.store.get(STORE_CLI, None)
    if cli and cli.get("returncode"):
        notizen.append(
            f"Das Agent-Programm endete mit Rückgabewert {cli['returncode']}; "
            "die Prüfung wurde möglicherweise nicht zu Ende geführt."
        )
    if cli and cli.get("finished") is False:
        notizen.append(
            f"Das Agent-Programm wurde nach {TIME_LIMIT // 60} Minuten "
            "abgebrochen, bevor es fertig war."
        )

    # Eine nicht auswertbare Antwort bekommt keinen Bericht (`audit_report`),
    # deshalb steht sie hier nicht. Wohl aber der Fall, dass nur die
    # Befundliste auswertbar war: Dann hat der Agent nicht zu Ende geprüft, und
    # gewertet wurde ein Zwischenstand.
    if (
        bewertung.get("answer_source") == FROM_FILE
        and bewertung.get("final_answer_error")
    ):
        notizen.append(
            "Der Agent hat keine auswertbare Schlussantwort abgegeben. Gewertet "
            "wurde der letzte Stand seiner laufend geführten Befundliste — die "
            "Prüfung war womöglich nicht abgeschlossen."
        )
    if bewertung["discarded"]:
        notizen.append(
            f"{len(bewertung['discarded'])} Meldungen wurden verworfen, weil sie "
            "keine gültige Seite oder keinen gültigen Fehlertyp nannten."
        )

    # Die Nachrichtengrenze beendet den Datensatz, bevor dieser Schritt läuft.
    # Knapp darunter heißt aber: Der Agent hatte kaum noch Züge übrig und hat
    # die Prüfung womöglich abgekürzt.
    if MESSAGE_LIMIT and len(state.messages) >= MESSAGE_LIMIT * 0.9:
        notizen.append(
            f"Der Agent hat {len(state.messages)} von höchstens {MESSAGE_LIMIT} "
            "Zügen gebraucht und war damit nahe an der Grenze."
        )
    return notizen


def assessment_html(
    bewertung: dict[str, Any], klassen: dict[str, str], notizen: list[str] | None = None
) -> str:
    """Der rot umrandete Kasten: Was ist dieser Bericht wert?

    Er kommt vom Benchmark und nicht vom Modell, und genau das muss er sagen —
    sonst liest ihn jemand als Selbsteinschätzung des Modells. Ein Kundenbericht
    aus einem Lauf mit Score 0,13 sieht genauso sauber aus wie einer aus einem
    Lauf mit 0,96: Dieselbe Gliederung, dieselbe Sprache, dieselben
    Empfehlungen — nur stimmt bei dem einen fast nichts davon. Ohne diesen
    Kasten wäre das PDF ein hübsches Dokument ohne Wahrheitswert.

    Die drei Zahlen sind bewusst in dieser Reihenfolge: wie viel stimmt
    (Precision — was ein Leser als Erstes wissen will, bevor er etwas umsetzt),
    wie viel fehlt (Recall), und erst dann der Gesamtscore.
    """
    verdict = bewertung["verdict"]
    gemeldet = verdict.tp + verdict.fp
    falsch = sorted(f"{seite}/{issue}" for seite, issue in
                    bewertung["reported"] - bewertung["reference"])

    zeilen = []
    for name in CAPABILITY_CLASSES:
        eintrag = bewertung["by_capability"].get(name) or {}
        if not eintrag.get("total"):
            continue
        zeilen.append(
            f"<tr><td><code>{name}</code></td>"
            f"<td>{eintrag['found']} von {eintrag['total']}</td>"
            f"<td>{bewertung['false_by_capability'].get(name, 0)}</td></tr>"
        )

    # Ein Satz, der die Zahlen in eine Aussage übersetzt. Wer nur Zahlen
    # hinstellt, überlässt die Einordnung dem Leser — und die ist der Zweck.
    if gemeldet == 0:
        urteil = "Dieser Bericht nennt keine Befunde."
    elif verdict.fp == 0:
        urteil = (
            f"Alles, was in diesem Bericht steht, trifft zu — aber "
            f"{verdict.fn} von {verdict.tp + verdict.fn} tatsächlich "
            "vorhandenen Problemen fehlen darin."
        )
    else:
        urteil = (
            f"Von {gemeldet} gemeldeten Problemen gibt es "
            f"<strong>{verdict.fp} nicht</strong>; {verdict.fn} tatsächlich "
            "vorhandene fehlen im Bericht."
        )

    stoerung = (
        '<p class="stoerung"><strong>Dieser Lauf war gestört.</strong> '
        + " ".join(notizen or [])
        + "</p>\n"
        if notizen
        else ""
    )
    return (
        '<div class="bewertung">\n'
        "<h2>Bewertung dieses Berichts durch den Benchmark</h2>\n"
        # Die Störung steht VOR den Zahlen: Sie entscheidet, wie die Zahlen zu
        # lesen sind. Ein niedriger Score nach neun Kürzungen sagt etwas über
        # das Kontextfenster und nicht über das Modell.
        + stoerung
        + f"<p>{urteil}</p>\n"
        f'<p><span class="zahl">{verdict.precision:.0%}</span> der Meldungen '
        f'sind richtig · <span class="zahl">{verdict.recall:.0%}</span> der '
        f'vorhandenen Probleme sind gefunden · Score '
        f'<span class="zahl">{punkt_komma(bewertung["score"])}</span> '
        "von 1,00</p>\n"
        + (
            "<table><tr><th>Fähigkeit</th><th>gefunden</th>"
            "<th>falsch gemeldet</th></tr>\n" + "\n".join(zeilen) + "</table>\n"
            if zeilen
            else ""
        )
        + (
            f'<p class="fussnote">Frei erfunden: {", ".join(falsch[:8])}'
            + (" …" if len(falsch) > 8 else "")
            + "</p>\n"
            if falsch
            else ""
        )
        + '<p class="fussnote">Dieser Kasten stammt nicht vom Modell, sondern '
        "vom Benchmark: Er vergleicht den Bericht mit einer eingefrorenen "
        "Referenz, die aus einem Render-Lauf gerechnet wurde. Der Score "
        "gewichtet jede der fünf geprüften Fähigkeiten gleich.</p>\n"
        + '<p class="fussnote">Was die Fähigkeiten bedeuten — <code>source</code> '
        "(aus den ausgelieferten Bytes lesbar), <code>network</code>, "
        "<code>render</code>, <code>request</code> (Anfrage unter besonderer "
        "Bedingung), <code>second_load</code> (nur über zwei Ladevorgänge "
        "sichtbar) — und wie der Score daraus entsteht, steht in "
        f"<code>{REPORT_DOCS}</code>.</p>\n"
        "</div>"
    )


def insert_assessment(rumpf: str, kasten: str) -> str:
    """Den Kasten hinter die Zusammenfassung und vor den ersten Befund setzen.

    Gesucht wird die erste Zwischenüberschrift, deren Text nicht nach
    Zusammenfassung klingt. Warum nicht einfach „vor der Überschrift
    ‚Befunde nach Dringlichkeit‘": Weil nur ein Teil der Modelle die
    Formulierung aus dem Prompt übernimmt. Vier gemessene Berichte, vier
    verschiedene Überschriften:

        „Befunde nach Dringlichkeit"              (wörtlich aus dem Prompt)
        „Kritische Befunde: Indexierung und Crawling"
        „Indexierung und Crawling (Höchste Priorität)"
        „Kritisch – Indexierung und Crawling"

    Findet sich keine Überschrift, steht der Kasten am Anfang — lieber zu früh
    als gar nicht, denn ohne ihn ist der Bericht ohne Wahrheitswert.
    """
    for treffer in re.finditer(r"<h([2-3])[^>]*>(.*?)</h\1>", rumpf, re.DOTALL):
        if _ZUSAMMENFASSUNG.search(re.sub(r"<[^>]+>", " ", treffer.group(2))):
            continue
        return rumpf[: treffer.start()] + kasten + "\n" + rumpf[treffer.start() :]
    return kasten + "\n" + rumpf


def report_html(markdown: str, model: str, bewertung: dict[str, Any] | None = None,
                klassen: dict[str, str] | None = None,
                notizen: list[str] | None = None) -> str:
    """Markdown → druckfertiges HTML-Dokument.

    Umgewandelt wird mit `markdown-it-py` im CommonMark-Modus plus Tabellen.
    Warum eine Bibliothek und nicht ein eigener kleiner Umwandler: Der Bericht
    ist Beiwerk, und ein selbstgebauter Markdown-Teilparser wäre genau die Art
    Code, die bei der ersten unerwarteten Verschachtelung still falsch rendert.
    Der Auditor in `reference/audit.py` kommt aus dem umgekehrten Grund ohne
    Fremdparser aus — dort entsteht die Referenz, hier eine Zugabe.

    `html=False` ist Absicht: Was das Modell schreibt, wird als Text behandelt
    und nicht als Markup. Der Bericht kommt aus einem Modell, das die geprüfte
    Website gelesen hat — ein `<script>` aus deren Quelltext soll hier nicht
    zum Leben kommen.
    """
    from markdown_it import MarkdownIt

    rumpf = MarkdownIt("commonmark", {"html": False}).enable(
        ["table", "strikethrough"]
    ).render(markdown)
    if bewertung is not None:
        rumpf = insert_assessment(
            rumpf, assessment_html(bewertung, klassen or {}, notizen)
        )
    stand = datetime.now().strftime("%d.%m.%Y")
    return (
        "<!doctype html>\n<html lang=\"de\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Technisches SEO-Audit</title>\n"
        f"<style>{REPORT_CSS}</style>\n</head>\n<body>\n"
        f"<p class=\"kopf\">Technisches SEO-Audit · {SITE_URL} · {stand} · "
        f"erstellt mit {model}</p>\n"
        f"{rumpf}\n</body>\n</html>\n"
    )


def report_dir() -> Path:
    """Wohin das PDF auf dem Host geschrieben wird."""
    return Path(os.environ.get(REPORT_DIR_ENV) or REPORT_DIR_DEFAULT)


@solver
def audit_report() -> Solver:
    """Lässt das Modell aus seiner eigenen Antwort einen Kundenbericht bauen.

    Reihenfolge: Antwort sichern → Bericht erzeugen → HTML in den Container →
    mit Chromium drucken → PDF auf den Host holen → Antwort wiederherstellen.
    Jeder Schritt kann schiefgehen, keiner darf den Lauf kosten.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # Die Antwort, die gewertet wird. Sie ist ab hier unantastbar: Der
        # Modellaufruf unten setzt `state.output` NICHT, aber ein späterer
        # Umbau könnte es — deshalb wird sie zurückgeschrieben statt darauf
        # vertraut.
        antwort = state.output
        vermerk: dict[str, Any] = {"status": "nicht versucht"}
        # Die Dauer gehört in den Vermerk, weil die Zugabe sonst in der
        # Gesamtzeit des Datensatzes verschwindet. Bei einem lokalen Modell
        # kann der Bericht ein Viertel der Laufzeit ausmachen, und wer die Laufzeit eines Modells
        # zitiert, muss es abziehen können. `sample.total_time` im Log kennt
        # den Unterschied nicht.
        begonnen = perf_counter()
        try:
            # Die Bewertung wird HIER gerechnet und nicht abgewartet: Der
            # Scorer läuft erst nach diesem Schritt. Gerechnet wird mit
            # derselben Funktion, die auch er benutzt (`graded_answer`) —
            # sonst stünde im PDF eine andere Zahl als im Log, oder ein Bericht
            # über eine andere Antwort als die gewertete.
            bewertung = await graded_answer(state)
            # Ohne auswertbare Antwort gibt es nichts zu berichten. Ein Modell
            # schriebe sonst einen Bericht über eine Fehlermeldung — das
            # kostet bei einem lokalen Modell eine Viertelstunde und ergibt
            # ein PDF ohne Inhalt.
            if bewertung["parse_error"]:
                vermerk = {
                    "status": "Antwort nicht auswertbar",
                    "error": bewertung["parse_error"],
                }
                return state
            findings = bewertung["answer"].strip()

            # Ein eigener Aufruf mit kurzem Kontext — NICHT `generate()`, das
            # führte das ganze Audit-Gespräch fort. Die Nachrichten landen
            # bewusst nicht in `state.messages`: Der Scorer liest dort das
            # Verhalten ab (geklickt, gescrollt, Seiten gesehen), und ein
            # Bericht, der Seitennamen aufzählt, verfälschte die Abdeckung.
            ausgabe = await get_model().generate(
                [ChatMessageUser(content=report_prompt(findings, state.metadata["pages"]))]
            )
            markdown = extract_markdown(ausgabe.completion)
            verbrauch = ausgabe.usage.total_tokens if ausgabe.usage else None
            if not markdown:
                vermerk = {
                    "status": "kein Bericht in der Antwort",
                    "tokens": verbrauch,
                    "answer_head": (ausgabe.completion or "")[:300],
                }
                return state
            # Das Layout kommt von hier, nicht aus dem Modell — siehe
            # `report_html`. Der Modellname steht im Kopf des Berichts: Wer
            # später drei Berichte nebeneinanderlegt, muss nicht raten, welcher
            # von welchem Modell ist.
            html = report_html(
                markdown,
                str(get_model().name),
                bewertung,
                state.metadata["capability_by_issue"],
                run_notes(state, bewertung),
            )

            await sandbox().write_file(REPORT_HTML, html)
            # Der Druck. Chromium meldet auf stderr auch dann etwas (fehlender
            # D-Bus im Container), wenn alles geklappt hat — deshalb
            # entscheidet die DATEI über den Erfolg und nicht der Rückgabewert.
            await sandbox().exec(
                cmd=[
                    CHROME_PATH,
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-pdf-header-footer",
                    # Wartet auf Layout und Schriften, ohne an der echten Uhr
                    # zu hängen — sonst hinge das Ergebnis an der Auslastung
                    # des Rechners.
                    "--virtual-time-budget=5000",
                    f"--print-to-pdf={REPORT_PDF}",
                    f"file://{REPORT_HTML}",
                ],
                timeout=REPORT_RENDER_TIMEOUT,
            )
            pdf = await sandbox().read_file(REPORT_PDF, text=False)
            if not pdf.startswith(b"%PDF"):
                vermerk = {"status": "Chromium hat kein PDF erzeugt", "tokens": verbrauch}
                return state

            ziel = report_dir()
            ziel.mkdir(parents=True, exist_ok=True)
            name = f"a01-{state.sample_id}-{datetime.now():%Y%m%d-%H%M%S}"
            (ziel / f"{name}.pdf").write_bytes(pdf)
            # Das Markdown daneben, und zwar das ROHE: Wer den Bericht
            # nachbessern, zitieren oder zwei Modelle vergleichen will, braucht
            # die Antwort des Modells — nicht unser Layout darum und nicht den
            # Druck davon.
            (ziel / f"{name}.md").write_text(markdown, encoding="utf-8")
            vermerk = {
                "status": "erstellt",
                "pdf": str(ziel / f"{name}.pdf"),
                "markdown": str(ziel / f"{name}.md"),
                "bytes": len(pdf),
                "tokens": verbrauch,
            }
        except Exception as fehler:  # noqa: BLE001 — siehe Docstring
            # Absichtlich alles: Der Bericht ist Beiwerk. Ein Lauf, der wegen
            # einer Zugabe stirbt, hat seine Messung verloren.
            vermerk = {"status": "Fehler", "error": f"{type(fehler).__name__}: {fehler}"}
        finally:
            vermerk["seconds"] = round(perf_counter() - begonnen, 1)
            store().set(STORE_REPORT, vermerk)
            state.output = antwort
        return state

    return solve


@scorer(
    metrics=[
        {
            # DER Score: F1 je Fähigkeitsklasse, gemittelt über die belegten
            # Klassen. Warum nicht Micro-F1 über alle Paare, steht in
            # `capability_f1`. Es gibt bewusst genau EINE Kennzahl — alles
            # andere hier ist Diagnose dazu.
            "score": [mean(), stderr()],
            # Precision und Recall daneben, weil F1 die beiden Fehlerarten
            # verrechnet: 0.6 aus lauter Falschmeldungen und 0.6 aus lauter
            # Lücken sind verschiedene Befunde — und nur der erste ist durch
            # einen strengeren Prompt zu heilen.
            "precision": [mean()],
            "recall": [mean()],
            # KEINE Leistungsspalten, sondern Verhalten: Hat der Agent geklickt,
            # hat er zu scrollen versucht? Genau diese zwei Fähigkeiten trennen
            # hier einen Agenten von einem Renderer — `interaction_blocked`
            # braucht einen Klick, `infinite_scroll_no_pagination` braucht
            # Scrollen. Stehen sie auf 0 und `recall` ist niedrig, ist das keine
            # Verständnisfrage: Zwei der 31 Befunde waren dann nicht
            # verpasst, sondern nie gesucht.
            "clicked": [mean()],
            "scrolled": [mean()],
            # Dritte Verhaltensspalte, für `cloaked_content`: Hat
            # sich der Agent je als Crawler ausgegeben? `fetch()` KANN den
            # User-Agent nicht setzen — er steht in der Fetch-API auf der Liste
            # der verbotenen Header. Wer sein Audit über `fetch(location.href)`
            # baut, kann diesen Befund also nicht finden, und das ist keine
            # Entscheidung dieser Aufgabe, sondern eine Eigenschaft der
            # Plattform. Steht die Spalte auf 0 und der Cloaking-Befund fehlt,
            # war er nicht verpasst, sondern nie gesucht.
            "ua_switched": [mean()],
            # Abdeckung: Anteil der Seiten, die der Agent überhaupt angesehen
            # hat. Sie trennt „schlecht geprüft" von „nicht hingesehen" — zwei
            # Aussagen, die in `recall` zusammenfallen und verschiedene
            # Ursachen haben. Eine 0.00 bei einer von 26 geprüften Seiten
            # sagt über die Prüfkunst des Modells nichts.
            "coverage": [mean()],
            # Vierte Verhaltensspalte, zu `cloaked_content`: Hat der Agent die
            # Seite unvermittelt erneut geholt? Die Prüflandschaft sendet auf
            # der gecloakten Adresse absichtlich kein `Vary` — wer nach dem
            # UA-Wechsel aus dem eigenen Cache liest, sieht die alte Fassung.
            # `ua_switched` 1 und `cache_bypassed` 0 bei fehlendem Befund heißt
            # deshalb: Der Agent hat es versucht und ist in die Falle gelaufen
            # — ein anderer Befund als "kam nicht auf die Idee".
            "cache_bypassed": [mean()],
            # Recall je Fähigkeitsklasse — die eigentliche Auskunft dieser
            # Aufgabe: Zwei Kandidaten mit demselben Gesamtwert können völlig
            # verschieden gearbeitet haben. `score` sagt, wie
            # viel gefunden wurde; diese fünf sagen, WOMIT.
            **{f"recall_{name}": [mean()] for name in CAPABILITY_CLASSES},
        }
    ]
)
def audit_scorer() -> Scorer:
    """F1 je Fähigkeitsklasse der gemeldeten Paare gegen die Referenz (`capability_f1`).

    Sieht Eingabe und Referenz — anders als `prompt()`. `parse_findings` bekommt
    bewusst nur Seitenliste und Katalog: Was gültig ist, steht im Prompt, und
    eine Prüfung, die dafür die Referenz heranzieht, könnte eine Meldung
    verwerfen, WEIL sie falsch ist.
    """

    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata
        # Dieselbe Antwort und dieselbe Rechnung, die auch der Bericht benutzt
        # — siehe `graded_answer`. Zwei Fassungen liefen auseinander, und im
        # PDF stünde eine andere Zahl als im Log.
        bewertung = await graded_answer(state)
        reported = bewertung["reported"]
        discarded, error = bewertung["discarded"], bewertung["parse_error"]
        verdict = bewertung["verdict"]

        # Wie gearbeitet wurde, aus dem Gesprächsverlauf. Ohne diese Auskunft
        # schließt man aus einem gemeldeten Scroll-Befund, dass der Agent
        # gescrollt hat — und liegt falsch.
        tools = tool_usage(state.messages)
        acted = behaviour(state.messages)
        seen = pages_seen(state.messages, meta["pages"])
        coverage = len(seen) / len(meta["pages"]) if meta["pages"] else 0.0

        by_capability = bewertung["by_capability"]
        false_by_capability = bewertung["false_by_capability"]
        recalls = bewertung["recalls"]
        score_value = bewertung["score"]
        f1_by_capability = bewertung["f1_by_capability"]

        cli = state.store.get(STORE_CLI, None)
        kontext = state.store.get(STORE_CONTEXT, None)
        source = bewertung["answer_source"]
        report = state.store.get(STORE_REPORT, None)

        return Score(
            value={
                "score": score_value,
                "precision": verdict.precision,
                "recall": verdict.recall,
                "clicked": int(acted["clicked"]),
                "scrolled": int(acted["scrolled"]),
                "ua_switched": int(acted["ua_switched"]),
                "cache_bypassed": int(acted["cache_bypassed"]),
                "coverage": coverage,
                **{f"recall_{name}": wert for name, wert in recalls.items()},
            },
            answer=", ".join(f"{page}/{issue}" for page, issue in sorted(reported)),
            explanation=(
                (f"{error} — " if error else "")
                + f"Score {score_value:.2f} (Micro-F1 {verdict.f1:.2f}, "
                f"P {verdict.precision:.2f}, "
                f"R {verdict.recall:.2f}): {verdict.tp} richtig, "
                f"{verdict.fp} falsch gemeldet, {verdict.fn} verpasst"
                + (f"; verpasst: {verdict.missed}" if verdict.missed else "")
                + (f"; falsch: {verdict.false}" if verdict.false else "")
                + (f"; verworfen: {discarded[:5]}" if discarded else "")
                + "; geklickt: "
                + ("ja" if acted["clicked"] else "nein")
                + ", gescrollt: "
                + ("ja" if acted["scrolled"] else "nein")
                + ", User-Agent gewechselt: "
                + ("ja" if acted["ua_switched"] else "nein")
                + ", Cache umgangen: "
                + ("ja" if acted["cache_bypassed"] else "nein")
                + "; je Klasse: "
                + ", ".join(
                    f"{name} {by_capability[name]['found']}/{by_capability[name]['total']}"
                    for name in CAPABILITY_CLASSES
                    if by_capability.get(name)
                )
                + f"; angesehen: {len(seen)} von {len(meta['pages'])} Seiten"
                + (
                    f" (nicht angesehen: {sorted(set(meta['pages']) - seen)[:6]})"
                    if len(seen) < len(meta["pages"])
                    else ""
                )
                + (f"; Antwort aus {source}" if source else "")
                # Das Fenster steht in der Begründung, weil es eine
                # Laufbedingung ist: Derselbe Agent kommt mit 128k weiter als
                # mit 64k, und ein angenommenes Fenster heißt, dass goose mit
                # einer falschen Grenze gerechnet hat.
                + (
                    f"; Kontextfenster {kontext['window']} ({kontext['source']})"
                    if kontext
                    else ""
                )
                # Der Bericht steht in der Begründung, weil er sonst niemandem
                # auffiele: Er geht nicht in die Wertung ein, und eine Zahl,
                # die ihn verrät, gibt es folgerichtig nicht.
                + (
                    f"; Bericht: {report['status']} ({report['seconds']:.0f} s)"
                    if report
                    else ""
                )
                + (
                    f"; CLI rc={cli['returncode']}"
                    if cli and cli.get("returncode")
                    else ""
                )
                + (
                    "; Werkzeuge: "
                    + ", ".join(
                        f"{name} {count}×"
                        for name, count in sorted(
                            tools.items(), key=lambda item: -item[1]
                        )[:6]
                    )
                    if tools
                    else ""
                )
            ),
            metadata={
                "reported": sorted(f"{page}/{issue}" for page, issue in reported),
                "tp": verdict.tp,
                "fp": verdict.fp,
                "fn": verdict.fn,
                "missed": verdict.missed,
                "false": verdict.false,
                # Verworfene Einträge gehören ins Log und nicht nur in die
                # Begründung: Ein Agent, der den geschlossenen Katalog
                # ignoriert, ist ein anderer Fall als einer, der ihn einhält
                # und daneben liegt.
                "discarded": discarded,
                "parse_error": error,
                # Der ganze Werkzeugverlauf als Histogramm — die Grundlage für
                # die Frage, ob ein Agent untersucht oder nur lädt.
                "tool_calls": tools,
                # Woher die gewertete Antwort kam: `findings_file` (die
                # Befundliste) oder `final_answer`. Steht die Datei hier und
                # `final_answer_error` ist gesetzt, ist ein Zwischenstand
                # gewertet worden — der Lauf ging vorzeitig zu Ende.
                "answer_source": source,
                "final_answer_error": bewertung["final_answer_error"],
                # Was das CLI gemeldet hat. Leer im react-Pfad, dort gibt es
                # kein CLI.
                "cli": cli,
                # Fenster, Herkunft und die Schwellen, die daraus folgten
                # (`ContextBudget`). Leer im Oracle-Lauf.
                "context": kontext,
                # Wo der Kundenbericht liegt und was er gekostet hat. KEIN
                # Wertungsfeld — es gibt bewusst keine Spalte dazu, sonst wäre
                # das Beiwerk am Ende doch eine Note. `tokens` steht hier,
                # damit man den Berichtsanteil aus dem Gesamtverbrauch des
                # Datensatzes herausrechnen kann.
                "report": report,
                # Wie oft der Kontext übergelaufen ist. Eine Laufbedingung,
                # keine Modelleigenschaft — ein Lauf mit Kürzungen ist mit
                # einem ohne nicht vergleichbar.
                "context_overflows": state.store.get(STORE_CONTEXT_OVERFLOWS, 0),
                # Namentlich und nicht nur als Anteil: Welche Seiten ein Agent
                # auslässt, ist die eigentliche Auskunft — lässt er die
                # schweren aus oder bricht er nach der ersten ab?
                # Je Klasse gefunden/vorhanden — die Zahl hinter dem Recall,
                # damit man in einem Log nicht rückwärts rechnen muss.
                "by_capability": by_capability,
                # Je Klasse das F1 und die Falschmeldungen — die Zahlen, aus
                # denen der Score entsteht, damit man sie im Log nicht
                # rückwärts rechnen muss. `None` heißt: Diese Klasse hat in der
                # Referenz keine Befunde und zählt nicht mit.
                "f1_by_capability": f1_by_capability,
                "false_by_capability": false_by_capability,
                # Micro-F1 über alle Paare — NICHT der Score, sondern Diagnose:
                # Ein großer Abstand zwischen beiden heißt, dass die Leistung
                # über die Fähigkeiten sehr ungleich verteilt ist.
                "micro_f1": verdict.f1,
                "pages_seen": sorted(seen),
                "pages_missed": sorted(set(meta["pages"]) - seen),
                **acted,
            },
        )

    return score


@task
def a01_browser_audit(
    oracle: bool = False, agent: str = AGENT_REACT, report: bool = True
) -> Task:
    """Browser-Audit einer JS-Site. Braucht Docker.

    Args:
        oracle: Statt eines Agenten die Musterlösung einsetzen. Muss 1.00
            ergeben — sonst ist die Wertung kaputt und jede Zahl aus dieser
            Aufgabe wertlos. Der Oracle-Lauf fährt **keinen** Container: Er
            braucht weder Browser noch Prüflandschaft, und deshalb läuft er
            (und damit `uv run pytest`) auch auf einem Rechner ohne Docker.
        report: Nach dem Audit zusätzlich einen Kundenbericht als PDF erzeugen
            lassen (`reports/`, Ziel über `GERMAN_SEO_BENCH_REPORT_DIR`). Geht **nicht**
            in die Wertung ein und kann nichts kaputt machen — Fehler landen
            im Log statt im Score. `-T report=false` schaltet ihn ab, etwa
            wenn nur die Zahlen gebraucht werden oder das Kontextbudget eines
            kleinen Modells knapp ist.
        agent: Über welches Gerüst das Audit läuft — `react` (Inspects eigener
            Werkzeug-Loop, Vorgabe), `goose` oder `pi` (Agent-CLIs im
            Container, über `sandbox_agent_bridge` an den Modell-Provider des
            Laufs gehängt). Prompt, Wertung und Referenz sind in allen Fällen
            dieselben; die einzige geänderte Größe ist das Framework.
            `-T agent=goose`, `-T agent=pi`.
    """
    if agent not in AGENTS:
        raise ValueError(
            f"Unbekannter Agent {agent!r} — möglich sind: {', '.join(AGENTS)}. "
            "Ein Tippfehler hier ergäbe sonst einen Lauf über das Vorgabegerüst "
            "unter falschem Namen."
        )
    return Task(
        dataset=load_dataset(DATA, prompt),
        # Die Musterlösung ist die Referenz selbst, als JSON. Kein zweites Feld
        # daneben, das dasselbe nochmal enthält — zwei Fassungen derselben
        # Wahrheit driften auseinander, und die Wertung glaubte dann der
        # falschen.
        solver=oracle_solver(field="findings")
        if oracle
        else [
            record_endpoint(),
            as_solver(audit_agent() if agent == AGENT_REACT else cli_agent(agent)),
            # Der Bericht steht NACH dem Agenten und VOR `record_server()`:
            # Er ist ein weiterer Modellaufruf, und `record_server()` soll
            # festhalten, was der Server nach der LETZTEN Anfrage geladen
            # hatte. Im Oracle-Pfad gibt es ihn nicht — dort gibt es weder
            # Modell noch Container.
            *([audit_report()] if report else []),
            record_server(),
        ],
        scorer=audit_scorer(),
        sandbox=None if oracle else ("docker", data_path(COMPOSE)),
        config=run_config(
            attempt_timeout=MODEL_CALL_TIMEOUT, max_tokens=ANSWER_MAX_TOKENS
        ),
        version=dataset_version(DATA),
        display_name="A01 — Browser-Audit einer JS-Site",
        # Nur der react-Pfad zählt Nachrichten: Ein CLI führt sein eigenes
        # Gespräch, und `MESSAGE_LIMIT` träfe dort nicht den Agenten, sondern
        # die Bridge. Bei goose gilt `--max-turns` (siehe `goose_command`), pi
        # kennt keine solche Grenze.
        message_limit=None if oracle or agent != AGENT_REACT else MESSAGE_LIMIT,
        time_limit=None if oracle else TIME_LIMIT,
        headline_metric=HeadlineMetric(score="score", metric="mean"),
    )
