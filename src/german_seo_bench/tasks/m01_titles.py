"""M01 — Deutsche SEO-Seitentitel, deterministisch bewertet.

Das Szenario: Ein Modell wird aus Screaming Frog oder einem Skript heraus
gerufen und soll je Seite einen Title-Tag schreiben. Eine Anfrage, eine
Antwort, keine Historie, kein Werkzeug.

Gemessen wird NICHT "schöner Titel" — das bräuchte ein Urteil, und ein Urteil
kann man nicht nachrechnen. Gemessen werden fünf Eigenschaften, die sich allein
aus der Zeichenkette ergeben:

    length    nicht zu kurz und nicht weit zu lang (bis LEN_HARD_MAX)
    keyword   eine Phrase aus `required` kommt vor
    german    der Titel ist deutsch, kein Abgleiten ins Englische
    meaning   kein Wort aus `forbidden` — das Homonym ist richtig aufgelöst
    format    nur der Titel: eine Zeile, kein Markdown, keine Anführungszeichen

`keyword` und `meaning` benutzen dieselbe Suchregel (`phrase_hit`): eine Phrase
aus einer Liste, an Wortgrenzen, buchstabengetreu, Groß- und Kleinschreibung
zählt. Die Liste nennt die Formen, die gelten sollen — "Birne" und "Birnen"
sind zwei Einträge, nicht ein Eintrag plus eine Flexionsregel.

Das ist mit Absicht streng und einfach zugleich. Einzelne vertretbare Titel
fallen dadurch durch ("Glühbirne" trifft "Birne" nicht, "BIRNENERNTE"
nicht "Birnenernte"). Das ist der bessere Handel: Ein Benchmark, der
nachvollziehbar sein soll, gewinnt mehr durch viele Aufgaben unter einer Regel,
die jeder nachrechnen kann, als durch eine Regel, die in jedem Einzelfall fair
sein will. Zuwachs kommt über die Zahl der Datensätze, nicht über
Sonderbehandlungen.

`score` je Datensatz ist **0, wenn eine der fünf Prüfungen scheitert**. Die
Prüfungen sind UND-verknüpft: Ein Titel, an dem eine scheitert, ist nicht zu
80 Prozent brauchbar. Der Wert beantwortet damit die Frage, die ein Praktiker
stellt — wie viele Titel kann ich ungeprüft in den Title-Tag übernehmen?

Hinter diesem Tor stehen Stilabzüge (`style_violations`, `STYLE_PENALTY`): Ein
Titel, der alle fünf Prüfungen besteht, aber auf den ersten Blick falsch
aussieht ("Igel Richtig Füttern", "Sommer-Mosquito-Stich-Vorbeugung"), bekommt
statt 1 etwas weniger. Abzug statt Null, weil die Regeln grob sind und einzelne
richtig geschriebene Titel treffen; ein Fehlalarm darf einen sonst
einwandfreien Titel nicht ganz kosten.

Ebenso hinter dem Tor: ein Abzug, wenn das Keyword zu weit hinten steht
(`KEYWORD_FRONT_MAX`, `KEYWORD_LATE_PENALTY`). Der Titel ist dann brauchbar,
aber er verschenkt die Stelle, die in der Trefferliste am meisten zählt. Und
ein gestaffelter Abzug für Titel über `LEN_MAX` (`OVERLENGTH_PENALTY`): Erst
wer mehr als 30 Zeichen überzieht, scheitert an `length`.

Die fünf Einzelwerte stehen daneben im selben Score und bekommen eigene
Metriken. Sie sind der einzige Ort, an dem Teilerfolge sichtbar sind: Zwei
Modelle mit `score` 0.35 sind völlig verschiedene Fälle, je nachdem ob sie an
`format` scheitern (mit einem Nachbearbeitungsschritt reparierbar) oder an
`meaning` (nicht reparierbar — das Modell hat die Seite nicht verstanden).

Gefragt wird je Datensatz einzeln, nicht in Stapeln. Das entspricht der Praxis
und schließt einen Messfehler aus: In einem Stapel beeinflussen sich die
Datensätze gegenseitig — eine unveränderte Frage kann kippen, nur weil eine
andere daneben umformuliert wurde.
"""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import HeadlineMetric
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState, generate, system_message

from german_seo_bench.checks import (
    format_ok,
    is_german,
    overlength_penalty,
    phrase_hit,
    phrase_position,
    style_violations,
)
from german_seo_bench.config import run_config
from german_seo_bench.dataset import dataset_version, load_dataset
from german_seo_bench.metrics import profile
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.server import record_endpoint, record_server

DATA = "m01_titles.json"

SYSTEM = "Du bist erfahrener SEO-Redakteur und schreibst auf Deutsch."

# Der Korridor, gegen den GEWERTET wird: die Länge, bei der ein Title-Tag in
# der Praxis brauchbar ist. Oben etwas über den rund 60 Zeichen, ab denen
# Google kürzt — ein Titel, der dort ein Wort verliert, ist noch brauchbar.
LEN_MIN = 35
LEN_MAX = 66

# Überlänge zieht ab, statt den Titel scheitern zu lassen: je Stufe
# (höchstens so viele Zeichen über LEN_MAX, Abzug). Ein guter Titel, der ein
# paar Zeichen zu lang ist, verliert bei Google ein Wort und bleibt brauchbar.
# Als Null gewertet, drückte er im Bericht, der je Datensatz den schlechtesten
# Lauf zählt, ein sonst starkes Modell bei diesem Datensatz auf 0 — ein
# Zeichen wöge so schwer wie ein Titel zum falschen Thema. Erst jenseits der
# letzten Stufe ist der Titel kein Title-Tag mehr, sondern ein Satz, und
# scheitert an `length`.
OVERLENGTH_PENALTY = ((10, 0.1), (20, 0.3), (30, 0.5))
LEN_HARD_MAX = LEN_MAX + OVERLENGTH_PENALTY[-1][0]

# Der Korridor, den die ANWEISUNG nennt — enger als der Prüfkorridor, und das
# ist Absicht. Die Regel dazu steht in `docs/architecture.md` ("Warum der
# Prompt strenger ist als die Prüfung"); der Grund in einem Satz: Ein Sprachmodell
# zählt keine Zeichen, es schätzt sie aus Tokens. Eine Zeichengrenze, die als
# harte Prüfung wirkt, misst deshalb die Schätzgüte mit — und unter der
# UND-Wertung schlägt diese Nebenmessung voll auf `score` durch.
#
# Der Abstand gilt in beide Richtungen. Oben wird er durch eine weitere
# Prüfgrenze erreicht, nicht durch eine engere Anweisung: Die Musterlösungen
# liegen zwischen 47 und 60 Zeichen, eine Prompt-Obergrenze unter 60 ließe
# einige aus der eigenen Anweisung fallen. Und er ist nötig — Modelle, die bis
# an die genannte Grenze schreiben, landen sonst mit jedem dritten Titel ein
# paar Zeichen darüber und bekommen Abzug, obwohl der Titel gut ist.
#
# Nach unten lohnt er sich ebenso: Zu kurze Titel sind die andere häufige
# Fehlerseite.
LEN_MIN_PROMPT = 40
LEN_MAX_PROMPT = 60

# Eine Quelle für Reihenfolge und Bestand der Prüfungen — sonst laufen
# Metrikliste und Wertung bei der nächsten Änderung auseinander. Alle fünf
# zählen gleich, weil `score` sie ohnehin nur mit UND verknüpft: Ein Titel, an
# dem eine Prüfung scheitert, ist nicht zu 80 Prozent brauchbar.
CHECKS = ("length", "keyword", "german", "meaning", "format")

# Abzug je verletzter Stilregel, auf Titel, die alle CHECKS bestehen. Jede
# Regel zieht höchstens einmal ab. Schwer wiegt, was jeder Leser sofort sieht
# und was nie richtig ist: eine Bindestrich-Kette statt Wörtern, ein
# kleingeschriebener Anfang. Leicht wiegt, was Fehlalarme kennt — die
# Title-Case-Regel trifft gelegentlich ein Adjektiv-Substantiv-Paar.
STYLE_PENALTY = {
    "spaces": 0.2,
    "capital": 0.2,
    "title_case": 0.1,
    "charset": 0.1,
    "sentence_end": 0.1,
}

# Wie weit hinten das Keyword spätestens beginnen darf, gezählt ab 0. Die
# Grenze ist absolut und nicht relativ zur Titellänge: Google kürzt lange
# Titel nach einer festen Breite, und was hinten steht, liest man zuletzt oder
# gar nicht. Die halbe Prüfobergrenze lässt vor dem Keyword Platz für ein,
# zwei Wörter ("Kabellose Maus …", "5 Tipps für den Flügel …") — nicht aber
# für eine ganze vorangestellte Rubrik ("Kleine Reparaturen im Bad: … Hahn").
#
# Ein Abzug und keine Prüfung in CHECKS: Ein Titel mit spätem Keyword ist
# brauchbar, nur schwächer. Er wiegt so schwer wie die schweren Stilregeln.
KEYWORD_FRONT_MAX = LEN_MAX // 2
KEYWORD_LATE_PENALTY = 0.2

# Weniger bekommt ein Titel, der alle CHECKS besteht, nie — wie viele Abzüge
# auch zusammenkommen. Er bleibt damit immer über einem, der eine Prüfung
# verfehlt. Ohne Untergrenze landete ein Titel mit Überlänge, spätem Keyword
# und zwei Stilverstößen bei 0 und sähe aus wie einer zum falschen Thema.
SCORE_FLOOR = 0.1

# Wonach `score` zusätzlich aufgeschlüsselt wird. Ohne das Stufenprofil
# verschwindet Sättigung im Mittel: Eine Stufe, die alle Modelle bestehen,
# hebt den Wert um einen festen Betrag und sieht aus wie Können.
GROUPS = ("level",)


def prompt(inputs: dict[str, Any]) -> str:
    """Was das Modell sieht. Bekommt NUR den Eingabe-Teil des Datensatzes.

    Ohne Keyword. Es vorzugeben hieße, die halbe Antwort mitzuliefern — das
    Modell setzt es dann schlicht vor eine Paraphrase der Überschrift. Das Thema
    muss es aus dem Seiteninhalt erschließen, so wie es ein Aufruf aus Screaming
    Frog heraus auch verlangt.

    Die Regel zur Suchabsicht bleibt als Anweisung stehen, obwohl keine Prüfung
    ihr entspricht: Sie hält die Titel praxisnah. Gewertet wird davon nur, was
    sich nachrechnen lässt — dass das Thema im Titel steht.
    """
    return (
        "Schreibe einen deutschen SEO-Seitentitel (Title-Tag) für diese Seite.\n\n"
        f"Seiteninhalt:\n{inputs['text']}\n\n"
        "Regeln:\n"
        f"- Länge zwischen {LEN_MIN_PROMPT} und {LEN_MAX_PROMPT} Zeichen.\n"
        "- Der Titel muss das Hauptthema der Seite benennen und erkennbar "
        "machen, wonach jemand sucht, der auf dieser Seite landen soll.\n"
        "- Sprache: Deutsch.\n"
        "- Ignoriere Navigation, Auszeichnung und Hinweise auf andere "
        "Artikel.\n"
        "- Antworte NUR mit dem Titel selbst: eine Zeile, keine "
        "Anführungszeichen, kein Markdown, keine Einleitung."
    )


@scorer(
    metrics=[
        {
            "score": [mean(), stderr(), *(profile(group) for group in GROUPS)],
            # Je Prüfung die Bestehensquote. Erst sie macht eine Zahl
            # diagnostisch: Sie sagt, WORAN ein Modell scheitert.
            **{check: [mean()] for check in CHECKS},
            # Anteil der Titel ohne Stilverstoß, über alle Titel — auch die,
            # die an einer Prüfung scheitern. Keine Prüfung in CHECKS, weil
            # sie nicht ins Tor eingeht, sondern nur dahinter abzieht.
            "style": [mean()],
            # Anteil der Titel, in denen das Keyword vorkommt UND vorne
            # steht. `keyword − keyword_front` ist der Anteil mit spätem
            # Keyword. Aus demselben Grund wie `style` nicht in CHECKS.
            "keyword_front": [mean()],
            # Anteil der Titel im Korridor bis LEN_MAX. `length` meldet nur
            # das Scheitern jenseits von LEN_HARD_MAX; wie oft ein Modell
            # überzieht und dafür Abzug bekommt, steht hier.
            "length_ok": [mean()],
        }
    ]
)
def title_scorer() -> Scorer:
    """Die fünf Prüfungen. Sieht Eingabe und Referenz — anders als `prompt()`.

    Die Schranke zwischen Prompt und Referenz ist einseitig, und das ist nötig:
    Der Längenkorridor steht im Prompt UND ist Prüfmaßstab. Ihn zu verdoppeln
    hieße, dass Aufforderung und Wertung auseinanderlaufen können.
    """

    async def score(state: TaskState, target: Target) -> Score:
        title = state.output.completion.strip()
        meta = state.metadata

        # Dieselbe Suchregel für beide Listen — einmal soll sie treffen, einmal
        # nicht. Zurück kommt die getroffene Phrase, damit die Begründung sagen
        # kann, WAS getroffen hat.
        keyword_phrase = phrase_hit(title, meta["required"])
        forbidden_word = phrase_hit(title, meta["forbidden"])

        # Je Prüfung ein nackter Wahrheitswert. Er wandert unverändert in den
        # Score, damit `german/mean` die Bestehensquote zeigt und nicht ein
        # Gewicht — die Spalte, die sagen soll WORAN es scheitert, muss als
        # Quote lesbar bleiben.
        passed = {
            "length": LEN_MIN <= len(title) <= LEN_HARD_MAX,
            "keyword": keyword_phrase is not None,
            "german": is_german(title),
            "meaning": forbidden_word is None,
            "format": format_ok(title),
        }
        failed = [name for name, ok in passed.items() if not ok]
        style = style_violations(title)
        # Die früheste Stelle über ALLE Pflichtphrasen, nicht die der Phrase,
        # die `phrase_hit` meldet — die ist nur die erste der Liste.
        keyword_pos = phrase_position(title, meta["required"])
        late = keyword_pos is not None and keyword_pos > KEYWORD_FRONT_MAX
        overlength = max(0, len(title) - LEN_MAX)
        penalty = (
            sum(STYLE_PENALTY[rule] for rule in style)
            + (KEYWORD_LATE_PENALTY if late else 0)
            + overlength_penalty(overlength, OVERLENGTH_PENALTY)
        )
        # Gerundet, damit 1 − 0.2 − 0.1 als 0.7 im Log steht und nicht als
        # 0.7000000000000001.
        points = max(SCORE_FLOOR, round(1 - penalty, 2)) if not failed else 0
        deductions = (
            ([f"{overlength} Zeichen zu lang"] if overlength else [])
            + style
            + ([f"Keyword erst ab Zeichen {keyword_pos}"] if late else [])
        )

        # Eine an `max_tokens` abgeschnittene Antwort scheitert an denselben
        # Prüfungen wie ein schlechter Titel und sähe in der Tabelle genauso
        # aus. Der Unterschied gehört benannt: Das Modell hat hier nicht
        # schlecht formuliert, sondern gar nicht aufgehört.
        truncated = state.output.stop_reason == "max_tokens"

        # Woher das verbotene Wort kam, macht für die Wertung keinen
        # Unterschied — ein Titel mit themenfremdem Vokabular ist in beiden
        # Fällen schlecht. Für die Deutung macht es einen erheblichen:
        #
        #   nicht im Seitentext -> das Modell hat die Bedeutung erfunden,
        #                          also das Homonym falsch aufgelöst
        #   im Seitentext       -> das Modell hat ein Wort aus dem Text
        #                          übernommen, ohne seinen Bezug zu prüfen
        #
        # Ohne diese Unterscheidung läse sich der zweite Fall wie der erste,
        # und man schriebe einem Modell Verständnisprobleme zu, das in
        # Wahrheit abgeschrieben hat.
        echoed = forbidden_word is not None and phrase_hit(meta["text"], [forbidden_word]) is not None

        return Score(
            value={
                # `not failed` und nicht `all(passed)`, damit Wertung und
                # Begründung dieselbe Liste benutzen — sonst kann ein Titel
                # mit 0 Punkten dastehen, dessen Begründung "alle Prüfungen
                # bestanden" lautet.
                "score": points,
                **{name: int(ok) for name, ok in passed.items()},
                "style": int(not style),
                "keyword_front": int(keyword_pos is not None and not late),
                "length_ok": int(LEN_MIN <= len(title) <= LEN_MAX),
            },
            answer=title,
            explanation=(
                "alle Prüfungen bestanden"
                + (f", Abzug: {', '.join(deductions)}" if deductions else "")
                if not failed
                else ("bei max_tokens abgeschnitten — " if truncated else "")
                + f"gescheitert an: {', '.join(failed)}"
                + (
                    f" (verbotenes Wort: {forbidden_word}"
                    + (" — aus dem Seitentext übernommen)" if echoed else ")")
                    if forbidden_word
                    else ""
                )
            ),
            metadata={
                "chars": len(title),
                "overlength": overlength,
                "keyword_hit": keyword_phrase,
                "keyword_pos": keyword_pos,
                "forbidden_hit": forbidden_word,
                "forbidden_echoed": echoed,
                "truncated": truncated,
                "style_violations": style,
            },
        )

    return score


@task
def m01_titles(oracle: bool = False) -> Task:
    """Deutsche SEO-Seitentitel.

    Args:
        oracle: Statt eines Modells die Musterlösung einsetzen. Muss 1.00
            ergeben — sonst ist die Wertung kaputt und jede Zahl aus dieser
            Aufgabe wertlos.
    """
    return Task(
        dataset=load_dataset(DATA, prompt),
        # `record_endpoint()` steht DAVOR und `record_server()` dahinter.
        # Die Reihenfolge ist in beide Richtungen begründet: Die Zieladresse
        # muss aufgezeichnet sein, bevor etwas scheitern kann — sonst fehlt
        # sie genau im Fehlerfall. Was der Server geladen hat, weiß man
        # umgekehrt erst hinterher: Erst danach hat
        # llama-swap das Modell geladen und `/props` meldet das richtige.
        solver=oracle_solver()
        if oracle
        else [
            record_endpoint(),
            system_message(SYSTEM),
            generate(),
            record_server(),
        ],
        # Kein Judge hier: Er läuft getrennt über das fertige Log
        # (`python -m german_seo_bench.judge`) und schreibt in eine Datei daneben. So
        # lässt sich ein Lauf später mit einem anderen Judge werten, und der
        # Judge muss nicht gleichzeitig mit dem Kandidaten geladen sein.
        scorer=title_scorer(),
        # Vorsichtige Laufvorgaben statt Inspects Anbieter-Vorgaben — siehe
        # config.py. Die Kommandozeile überschreibt sie.
        config=run_config(),
        version=dataset_version(DATA),
        display_name="M01 — Deutsche SEO-Seitentitel",
        # Die eine Zahl, die einen Lauf zusammenfasst. Ohne die Angabe
        # nähme Inspect die erste Metrik des ersten Scores — das wäre hier
        # zufällig richtig und bei der nächsten Umsortierung falsch.
        headline_metric=HeadlineMetric(score="score", metric="mean"),
    )
