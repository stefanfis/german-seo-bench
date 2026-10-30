"""Prüflogik für das Browser-Audit (A01) — ohne Inspect, einzeln testbar.

Zwei Dinge stehen hier, und beide sind reine Mengenlehre über Zeichenketten:

`parse_findings` macht aus der Antwort des Agenten eine Menge von
(Seite, Problem)-Paaren. Sie sieht die Referenz **nicht** — geprüft wird nur
gegen die Seitenliste und den Fehlerkatalog, und beides steht ohnehin im Prompt.
Was nicht hineinpasst, wird verworfen und benannt statt stillschweigend
weggelassen: Ein Agent, der `produkt.html/kaputte_ressource` meldet, hat etwas
anderes getan als verlangt, und das soll man der Wertung ansehen.

`tool_usage` und `behaviour` lesen aus dem Gesprächsverlauf ab, WIE der Agent
gearbeitet hat. Bei einer Agent-Aufgabe ist das genauso diagnostisch wie das
Ergebnis: Ein Modell, das Seiten nur lädt und Snapshots liest, kann strukturell
nur einen Teil der Befunde finden — und ohne diese Auskunft schreibt man ihm
Verständnisprobleme zu, wo es schlicht nie geklickt hat.

`grade_audit` rechnet Precision, Recall und F1 über diese Paare; den Score der
Aufgabe bildet daraus `capability_f1` in `tasks/a01_browser_audit.py`. Warum
nicht alles-oder-nichts wie in M01, M03 und M10: Dort ist ein Datensatz eine
Frage mit einer Antwort, hier ist ein Datensatz ein Auftrag über viele Seiten
mit vielen Befunden. Eine UND-Wertung wäre eine Münze — sie unterschiede nicht
zwischen einem Agenten, der fast alles findet, und einem, der nichts findet. F1
hält zugleich beide Fehlerarten auseinander, und das ist hier der Kern: Wer
jeden Katalogeintrag auf jeder Seite meldet, hat perfekten Recall und ist
nutzlos.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from german_seo_bench.checks.json_answer import candidates, first_json_value

# Die Schlüssel, die ein Befund tragen muss. Englisch wie alle JSON-Schlüssel im
# Repo, und dieselben Wörter, die der Prompt verlangt.
PAGE_KEY = "page"
ISSUE_KEY = "issue"

Finding = tuple[str, str]


def parse_findings(
    text: str, pages: Iterable[str], issues: Iterable[str]
) -> tuple[set[Finding], list[str], str | None]:
    """Antwort → (Paarmenge, verworfene Einträge, Fehlermeldung).

    Die Fehlermeldung ist gesetzt, wenn gar kein JSON-Array zu finden war. Das
    ist ein anderer Fall als ein leeres Array: "hat nichts gemeldet" ist eine
    Aussage über die Site, "hat kein JSON geliefert" eine über das Modell, und
    beide ergäben sonst dieselbe Null.

    Mehrfach gemeldete Paare zählen einmal — es ist eine Menge. Ein Agent, der
    denselben Befund dreimal aufführt, ist nicht dreimal so gut.
    """
    if not text.strip():
        return set(), [], "leere Antwort"

    known_pages, known_issues = set(pages), set(issues)
    for candidate in candidates(text):
        value, error = first_json_value(candidate)
        if value is None:
            continue
        if isinstance(value, dict):
            # Ein Objekt, das genau ein Array enthält, wird ausgepackt
            # (`{"findings": [...]}`). Dieselbe Toleranz wie in M03, aus
            # demselben Grund: Die Verpackung ist nicht, was gemessen wird —
            # gemessen wird, ob der Agent die Fehler gefunden hat. Die Einträge
            # selbst werden unverändert streng gegen Seitenliste und Katalog
            # geprüft, ein falsch gebautes Objekt fällt also trotzdem auf.
            arrays = [item for item in value.values() if isinstance(item, list)]
            if len(arrays) != 1:
                return set(), [], "JSON ist ein Objekt und kein Array von Befunden"
            value = arrays[0]
        if not isinstance(value, list):
            return set(), [], f"JSON ist {type(value).__name__}, kein Array"

        found: set[Finding] = set()
        discarded: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                discarded.append(f"kein Objekt: {item!r:.40}")
                continue
            page = str(item.get(PAGE_KEY, "")).strip()
            issue = str(item.get(ISSUE_KEY, "")).strip()
            if page in known_pages and issue in known_issues:
                found.add((page, issue))
            elif page or issue:
                # Warum die Herkunft des Fehlers benannt wird und nicht nur
                # "verworfen": Eine unbekannte Seite heißt, der Agent hat sich
                # eine URL ausgedacht; ein unbekanntes Problem heißt, er hat den
                # geschlossenen Katalog ignoriert. Das sind zwei verschiedene
                # Schwächen.
                grund = []
                if page not in known_pages:
                    grund.append("Seite unbekannt")
                if issue not in known_issues:
                    grund.append("Problem nicht im Katalog")
                discarded.append(f"{page}/{issue} ({', '.join(grund)})")
            else:
                discarded.append("leerer Eintrag")
        return found, discarded, None
    return set(), [], error or "kein JSON in der Antwort"


@dataclass(frozen=True)
class AuditVerdict:
    """Das Urteil über eine Meldung, mit allem, was die Diagnose braucht.

    `precision` und `recall` stehen einzeln daneben, weil F1 allein die beiden
    Fehlerarten verrechnet: 0.6 aus lauter Falschmeldungen und 0.6 aus lauter
    Lücken sind verschiedene Befunde, und nur der erste ist durch einen
    strengeren Prompt zu heilen.
    """

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    missed: dict[str, list[str]]
    false: dict[str, list[str]]


def grade_audit(reported: set[Finding], reference: set[Finding]) -> AuditVerdict:
    """Micro-F1 der Paarmengen, aufgeschlüsselt nach Problemtyp.

    Die Aufschlüsselung ist der eigentliche Nutzen für die Auswertung: Sie sagt,
    WELCHE Fehlerklasse ein Agent nicht sieht. Ein Agent ohne Scrollen verliert
    `infinite_scroll_no_pagination`, einer ohne Klick `interaction_blocked` —
    beide kommen auf ähnliches F1 und können völlig Verschiedenes nicht.
    """
    tp = len(reported & reference)
    fp = len(reported - reference)
    fn = len(reference - reported)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    missed: dict[str, list[str]] = {}
    for page, issue in sorted(reference - reported):
        missed.setdefault(issue, []).append(page)
    false: dict[str, list[str]] = {}
    for page, issue in sorted(reported - reference):
        false.setdefault(issue, []).append(page)

    return AuditVerdict(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        missed=missed,
        false=false,
    )


# Werkzeugnamen von chrome-devtools-mcp (1.10.1), soweit sie für A01 etwas
# entscheiden. Ein Klick ist als Werkzeug erkennbar; Scrollen ist es NICHT — der
# Server hat dafür kein eigenes Werkzeug, es passiert über `evaluate_script`
# (`window.scrollTo`) oder eine Taste. Deshalb wird dort in den Argumenten
# gesucht.
CLICK_TOOLS = frozenset({"click", "click_at"})
# Werkzeuge, deren `url`-Argument eine Seite tatsächlich aufruft.
NAVIGATION_TOOLS = frozenset({"new_page", "navigate_page"})
SCRIPT_TOOLS = frozenset({"evaluate_script"})
KEY_TOOLS = frozenset({"press_key"})
SCROLL_KEYS = frozenset({"End", "PageDown", "Page_Down", "ArrowDown", "Space", " "})

# Ein Agent-CLI hängt seinen Werkzeugen den Namen der Erweiterung voran
# (`chrome-devtools__click`, seltener `chrome-devtools.click`). Über Inspects
# `react()`-Loop heißt dasselbe Werkzeug schlicht `click`. Verglichen wird
# deshalb der letzte Namensteil — sonst wäre `clicked` auf dem CLI-Pfad blind
# und meldete "nicht geklickt" für einen Agenten, der geklickt hat.
_NAME_TRENNER = re.compile(r".*(?:__|\.|/|:)")


def tool_base_name(name: str) -> str:
    """`chrome-devtools__click` → `click`; `click` bleibt `click`."""
    return _NAME_TRENNER.sub("", str(name or "")).strip()

# Dieselbe Handlung, im Skript ausgeführt. Ein Agent kann `btn.click()` in
# einem `evaluate_script` aufrufen und die Blockade mit `performance.now()`
# selbst messen. Eine Spalte, die nur das Werkzeug `click` zählt, meldete dann
# "nicht geklickt" — und aus dem gefundenen Befund würde man ein undichtes Item
# schließen, das es nicht gibt.
#
# `\.click\(` und nicht bloß "click": Sonst schlägt jedes Skript an, das
# `onclick`-Attribute LIEST — und genau das tut ein Audit auf `scroll.html`.
_CLICK_IN_SCRIPT = re.compile(r"\.click\s*\(|MouseEvent\s*\(\s*['\"]click", re.IGNORECASE)

# Nur echte Scroll-Handlungen. `scrollHeight` zu LESEN ist keine — ein Skript,
# das die Seitenhöhe abfragt, hat nicht gescrollt.
_SCROLL_IN_SCRIPT = re.compile(
    r"scrollTo\s*\(|scrollBy\s*\(|scrollIntoView\s*\(|scrollTop\s*=", re.IGNORECASE
)

# Hat der Agent sich je als Crawler ausgegeben? Die Spalte gehört zu
# `cloaked_content`: Der Server liefert Suchmaschinen anderes HTML aus als
# Besuchern, und wer immer nur das anfordert, was er selbst bekommt, kann den
# Befund grundsätzlich nicht sehen. Ohne diese Auskunft wäre einem verpassten
# Cloaking-Befund nicht anzusehen, ob der Agent es versucht hat oder nicht auf
# die Idee kam — dieselbe Unterscheidung wie bei `clicked` und `scrolled`.
#
# Gesucht wird die SETZENDE Form, nicht das Wort: `userAgent:` / `"User-Agent":`
# in den Argumenten (`emulate`, `extraHttpHeaders`, ein `fetch` mit Kopfzeilen),
# `-A`/`--user-agent` an einem curl im Shell-Werkzeug eines CLI-Agenten, oder
# eine Crawler-Kennung im Klartext. Bewusst NICHT erkannt: `navigator.userAgent`
# auszulesen — das ist Inspektion, keine Handlung, genau wie `scrollHeight`.
_UA_SWITCH = re.compile(
    r"""user[-_ ]?agent["']?\s*[:=]        # userAgent: … / "User-Agent": …
        |--user-agent\b                    # curl --user-agent
        |\s-A\s+['"]?\w                    # curl -A "…"
        |googlebot|bingbot|adsbot           # eine Crawler-Kennung im Klartext
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Hat der Agent den Cache umgangen? Die Spalte gehört zu `cloaked_content` und
# zu jedem künftigen Item, bei dem dieselbe Adresse unter zwei Bedingungen
# verschieden antwortet. Ohne sie sind zwei ganz verschiedene Nullen nicht zu
# unterscheiden: "hat nie nachgefragt" und "hat nachgefragt und den eigenen
# Cache erwischt" — Letzteres ist der Fehler, den die Falle prüfen SOLL, und
# Ersteres sagt gar nichts über Cache-Bewusstsein.
#
# Erkannt werden die vier Wege, die es tatsächlich gibt: die Option am
# `fetch`, der Schalter am Navigationswerkzeug, eine Kopfzeile, und ein
# Cache-Buster in der Adresse.
_CACHE_BYPASS = re.compile(
    r"""cache["']?\s*:\s*["'](?:reload|no-store|no-cache)   # fetch(..., {cache: …})
        |ignore_?cache["']?\s*[:=]\s*true                    # navigate_page(ignoreCache)
        |(?:cache-control|pragma)["']?\s*:\s*["'][^"']*no-cache
        |[?&](?:nocache|cachebust|cb|_|ts)=                   # Cache-Buster in der URL
        |\?[^"'\s]*(?:Date\.now\(\)|Math\.random\(\))
    """,
    re.IGNORECASE | re.VERBOSE,
)


def tool_usage(messages: Iterable[Any]) -> dict[str, int]:
    """Welches Werkzeug wie oft — aus dem Gesprächsverlauf gezählt.

    Bewusst ohne Inspect-Import: Gelesen wird `message.tool_calls` und daran
    `function`, beides über `getattr`. Damit bleibt die Funktion mit
    Wegwerf-Objekten testbar und `checks/` frei von Inspect.
    """
    counts: dict[str, int] = {}
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            name = tool_base_name(getattr(call, "function", ""))
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def _arguments(call: Any) -> str:
    """Die Argumente eines Werkzeugaufrufs als durchsuchbare Zeichenkette."""
    arguments = getattr(call, "arguments", None)
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)


def behaviour(messages: Iterable[Any]) -> dict[str, bool]:
    """Hat der Agent geklickt, gescrollt, den User-Agent gewechselt?

    Genau diese zwei Fähigkeiten trennen in A01 einen Agenten von einem
    Renderer: `interaction_blocked` braucht einen Klick,
    `infinite_scroll_no_pagination` braucht Scrollen. Wer beides nicht tut, kann
    zwei der 31 Befunde nicht finden — und das soll in der Wertung stehen,
    nicht im Verdacht.

    Gemessen wird die **Handlung, egal auf welchem Weg** — nicht die Benutzung
    eines bestimmten Werkzeugs. Das ist der Kern: Ein starkes Modell führt beides
    über `evaluate_script` aus (`btn.click()`, `window.scrollTo(...)`) statt über
    die dafür gedachten Werkzeuge. Wer nur Werkzeugnamen zählt, hält so ein
    Modell für passiv und schließt aus seinen Treffern auf undichte Items.

    Erkannt wird der **Versuch**, nicht die Wirkung: `.click(` bzw. ein
    `MouseEvent('click'`, und für das Scrollen `scrollTo`/`scrollBy`/
    `scrollIntoView`/`scrollTop =` oder eine weiterblätternde Taste. Bewusst NICHT
    erkannt: das bloße Lesen von `scrollHeight` oder eines `onclick`-Attributs —
    das ist Inspektion, keine Handlung.

    `cache_bypassed` gehört dazu und beantwortet die Anschlussfrage: Hat der
    Agent die Adresse unvermittelt erneut geholt, oder hat er seinen eigenen
    Browser-Cache befragt? Die gecloakte Seite sendet absichtlich kein `Vary`
    (siehe `testsite.nginx.conf`) — wer erst als Besucher holt und danach als
    Crawler, bekommt die alte Fassung, ohne dass der Server gefragt wird. Ohne
    diese Spalte sähe das Ergebnis aus wie "hat es nicht versucht".

    `ua_switched` folgt derselben Regel für `cloaked_content`: Gezählt wird, wer
    den User-Agent SETZT, nicht wer ihn ausliest. Gesucht wird dafür in den
    Argumenten JEDES Werkzeugs und nicht nur in denen des Skript-Werkzeugs —
    über `emulate` geht es genauso wie über `extraHttpHeaders`, über ein `fetch`
    mit Kopfzeilen oder über ein `curl -A` im Shell-Werkzeug eines Agent-CLI.
    """
    clicked = False
    scrolled = False
    ua_switched = False
    cache_bypassed = False
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            name = tool_base_name(getattr(call, "function", ""))
            argumente = _arguments(call)
            if _UA_SWITCH.search(argumente):
                ua_switched = True
            if _CACHE_BYPASS.search(argumente):
                cache_bypassed = True
            if name in CLICK_TOOLS:
                clicked = True
            elif name in SCRIPT_TOOLS:
                clicked = clicked or bool(_CLICK_IN_SCRIPT.search(argumente))
                scrolled = scrolled or bool(_SCROLL_IN_SCRIPT.search(argumente))
            elif name in KEY_TOOLS and any(
                key in argumente for key in SCROLL_KEYS
            ):
                scrolled = True
    return {
        "clicked": clicked,
        "scrolled": scrolled,
        "ua_switched": ua_switched,
        "cache_bypassed": cache_bypassed,
    }


def pages_seen(messages: Iterable[Any], pages: Iterable[str]) -> set[str]:
    """Welche der genannten Seiten hat der Agent überhaupt angesehen?

    Die Abdeckungsspalte, und sie beantwortet eine Frage, die `recall` nicht
    beantworten kann: Ein Agent, der 25 von 26 Befunden verpasst, hat entweder
    schlecht geprüft **oder gar nicht hingesehen** — und das sind verschiedene
    Aussagen über verschiedene Fähigkeiten. Ein Modell, das eine einzige Seite
    prüft und danach abgibt, landet bei 0.00 — ohne diese Spalte sähe man das
    nur, wenn man den ganzen Verlauf durchgeht.

    Gezählt wird die **Handlung, egal auf welchem Weg** — wie bei `clicked` und
    `scrolled`: Eine Seite gilt als angesehen, sobald ihre Adresse in den
    Argumenten irgendeines Werkzeugaufrufs steht. Damit zählt die Navigation
    (`new_page`, `navigate_page`) genauso wie ein `fetch()` im Skript oder ein
    `curl` im Shell-Werkzeug eines Agent-CLI. Wer nur Navigationswerkzeuge
    zählte, hielte ein Modell für untätig, das sich sein Audit selbst
    programmiert.

    Bewusst NICHT gezählt wird die bloße ERWÄHNUNG einer Adresse: Ein Skript mit
    `l.href.endsWith('/liste-2.html')` lädt die Seite nicht. Wer nach
    `/<datei>` suchte und das als angesehen zählte, machte genau den Fehler,
    gegen den es die Abdeckungsspalte gibt.

    Gezählt wird deshalb nur, was einen Abruf bedeutet: die Zieladresse eines
    Navigationswerkzeugs, eine vollständige URL irgendwo in den Argumenten (so
    steht sie in `fetch("http://…")` und in `curl http://…`), oder ein `fetch`
    mit relativem Ziel.
    """
    known = list(pages)
    calls = [
        call
        for message in messages
        for call in (getattr(message, "tool_calls", None) or [])
    ]
    text = " ".join(_arguments(call) for call in calls)

    # Die Zieladresse der Navigationswerkzeuge, auch relativ angegeben.
    navigated: set[str] = set()
    for call in calls:
        if tool_base_name(getattr(call, "function", "")) not in NAVIGATION_TOOLS:
            continue
        arguments = getattr(call, "arguments", None)
        url = arguments.get("url", "") if isinstance(arguments, dict) else ""
        navigated.add(str(url).split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])

    # Ein Aufruf, der selbst abruft: Dort zählt auch ein Dateiname, der nur als
    # Zeichenkette dasteht — typisch für die Schleife über eine Seitenliste,
    # bei der die Adresse erst zusammengesetzt wird
    # (`for (const p of pages) fetch("http://testsite/" + p)`). Ohne diese
    # Regel zählte ein solcher Agent nur einen Teil der Seiten, die er
    # tatsächlich geholt hat.
    holt_selbst = bool(re.search(r"fetch\s*\(|curl\s", text))

    seen = set()
    for page in known:
        if page in navigated:
            seen.add(page)
        elif re.search(rf"https?://[^\s\"']*/{re.escape(page)}\b", text):
            seen.add(page)
        # Nur das FÜHRENDE Anführungszeichen verlangt: Die Argumente kommen als
        # JSON, dort steht hinter dem Namen ein maskiertes Zeichen
        # (`\"home.html\"`). Ein Pfadvergleich wie `endsWith('/liste-2.html')`
        # bleibt trotzdem draußen — davor steht ein Schrägstrich.
        elif holt_selbst and re.search(rf"[\"']{re.escape(page)}", text):
            seen.add(page)
    return seen


def as_findings(rows: Iterable[Any]) -> set[Finding]:
    """Referenzzeilen (`{"page": …, "issue": …}`) als Paarmenge.

    Eine Stelle für die Umwandlung, damit Referenz und Modellantwort dieselbe
    Form bekommen. Ohne das vergleicht man irgendwann Objekte mit Tupeln, und
    die Schnittmenge ist dann immer leer — ein Fehler, der wie ein völlig
    unfähiges Modell aussieht.
    """
    return {(str(row[PAGE_KEY]), str(row[ISSUE_KEY])) for row in rows}
