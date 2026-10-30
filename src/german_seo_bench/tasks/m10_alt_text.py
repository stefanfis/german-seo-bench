"""M10 — Deutsche ALT-Texte für Bilder, deterministisch bewertet.

Das Szenario: Ein Shop oder ein Magazin hat ein paar tausend Bilder ohne
ALT-Attribut. Ein lokales Modell soll sie füllen — die Bilder verlassen dabei
das Haus nicht, und das ist der eigentliche Grund, warum die Aufgabe lokal
interessant ist: Produktfotos, Kundenbilder und Screenshots aus internen
Systemen sind genau die Datei­sorte, die man nicht an eine fremde API schickt.

Die Aufgabe verlangt ein **bildfähiges** Modell, unter den kleinen lokalen etwa
`gemma-4-e4b`; ein reines Textmodell scheitert hier nicht mit
schlechten Werten, sondern mit einem Serverfehler — und das ist eine Aussage
über den Aufbau, nicht über das Modell (siehe unten).

Gemessen wird NICHT "schöne Bildbeschreibung" — das bräuchte ein Urteil, und
ein Urteil kann man nicht nachrechnen. Gemessen werden sieben Eigenschaften,
die sich aus der Zeichenkette und einer eingefrorenen Referenz zum Bild ergeben:

    length    nicht zu kurz und nicht weit zu lang (bis LEN_HARD_MAX)
    subject   das Hauptmotiv ist benannt
    context   der Bildzusammenhang ist benannt (Ort, Umgebung, Situation)
    german    der Text ist deutsch, kein Abgleiten ins Englische
    grounded  nichts erfunden, was im Bild nicht zu sehen ist
    format    eine Zeile, kein Markdown, keine Anführungszeichen
    opening   kein Floskel-Vorspann ("Bild von …", "Das Foto zeigt …")

`score` je Datensatz ist **alles oder nichts**: 1, wenn alle sieben Prüfungen
bestehen, sonst 0 — wie in M01 und M03, und aus demselben Grund. Die Frage des
Praktikers lautet: Wie viele ALT-Attribute kann ich ungeprüft übernehmen? Ein
ALT-Text, der das Motiv trifft und mit "Bild von" anfängt, muss angefasst
werden.

Einzige Ausnahme ist die Überlänge: Ein ALT-Text, der alle Prüfungen besteht,
aber über `LEN_MAX` liegt, bekommt gestaffelt Abzug (`OVERLENGTH_PENALTY`,
dieselbe Staffel wie M01). Erst wer mehr als 30 Zeichen überzieht, scheitert
an `length`.

**`grounded` ist die Prüfung, um die es hier eigentlich geht.** Ein Modell, das
ein Motiv verfehlt, liefert einen unbrauchbaren ALT-Text — ärgerlich, aber
sichtbar. Ein Modell, das plausibel danebenerfindet, liefert einen ALT-Text, der
sich gut liest und falsch ist, und der landet ungeprüft in zehntausend Seiten.
Die Sperrlisten sind deshalb nicht beliebig, sondern nach einer Regel gebaut:
Ein Wort kommt nur hinein, wenn es (a) im Bild nachweislich **nicht** vorkommt
und (b) das Klischee zum Motiv ist. Pinguine stehen hier auf einem sonnigen
Felsstrand — die Sperrliste heißt Schnee, Eisscholle, Antarktis. Die weißen
Parabolschüsseln stehen auf einer Wiese unter blauem Himmel — sie heißt Windrad,
Nachthimmel, Rakete.

**Die Suchregel ist die aus M01, mit einer Lockerung** (`head_hit`):
`subject` und `context` sind Listen von Phrasen, von denen eine trifft — an
Wortgrenzen, buchstabengetreu, Groß-/Kleinschreibung zählt. Dazu trifft ein
Listenwort auch als Grundwort eines Kompositums: "Holzhütte" trifft `Hütte`,
"Landstraße" trifft `Straße`, "Herbstkürbisse" trifft `Kürbisse`. Deutsche
Bildbeschreibungen bestehen fast nur aus Komposita, und das Bestimmungswort
davor lässt sich nicht vorher aufzählen. Ohne die Lockerung fiele ein großer
Teil richtiger ALT-Texte durch, und M10 mäße die Wortlisten statt des Sehens.

Endungen werden weiterhin nicht geraten: "Kürbissen" trifft `Kürbis` nicht,
die gängigen Formen stehen in der Liste. Mehrere Phrasen je Liste sind
ohnehin nötig, "Sofa" und "Couch" sind dasselbe Möbelstück, "Parabolantenne"
und "Satellitenschüssel" dieselbe Schüssel.

`forbidden` sucht dagegen streng mit `phrase_hit`. Eine Sperrliste mit
Grundwort-Regel sperrte "Auto" auch in "Elektroauto-Ladestation" und müsste
jedes harmlose Kompositum mitbedenken. Andersherum gilt: Wo ein Kompositum über
die Lockerung fälschlich als Motiv trifft ("Seilbahn" trifft `Bahn`), steht es
in der Sperrliste.

**Ein Motiv je Bild, nicht zwei.** `subject` ist eine einzige Liste. Wo ein Bild
zwei gleichrangige Motive hat, steht das unterscheidende in `subject` und das
andere im `context`, wo es inhaltlich hingehört: beim Obstregal der Supermarkt,
beim rauschenden Fernseher die Frau davor. Dafür gilt überall dieselbe Regel.

Bei der Erdfunkstelle geht die Gruppe bewusst bis "Teleskop": Ob eine
Parabolschüssel zu einer Satelliten-Bodenstation oder zu einem Radioteleskop
gehört, ist **am Bild allein nicht entscheidbar**. Geprüft wird deshalb, ob das
Modell Form und Art des Objekts benennt, nicht ob es die Anlage identifiziert.
Alles andere wäre eine Wissensfrage unter dem Deckmantel einer Sehprüfung — und
für Wissensfragen gibt es M02.

**`opening` ist eine eigene Prüfung und nicht Teil von `format`.** Beides ist
reparierbar, aber an verschiedenen Stellen: eine Verpackung in Anführungszeichen
mit einem `strip()`, ein "Das Bild zeigt …" nur, indem man das Modell anders
fragt. Sie zusammenzuwerfen hieße, die Spalte unlesbar zu machen, die sagt, ob
sich ein Nachbearbeitungsschritt lohnt. Die Floskel ist auch keine Stilfrage:
Screenreader kündigen ein Bild über die Rolle des Elements bereits an, der
Vorspann verdoppelt also eine Information, die schon da war.

**Was der Prompt sagt und was nicht.** Er nennt den Längenkorridor, die
Sprache, das Format und die Floskel-Regel — alles Dinge, die ein Aufrufer auch
in der Praxis vorgibt und die ohne Blick ins Bild erfüllbar sind. Er nennt
**nicht**, was auf dem Bild zu sehen ist, und er nennt die Sperrlisten nicht.
Wie in M03: Wer die schwierige Hälfte in den Prompt schreibt,
misst hinterher Anweisungsbefolgung und nennt es Extraktion.

**Der Bestand: 32 Bilder, absichtlich ungleich ergiebig.** Neben den Motiven mit
einem eindeutigen Hauptgegenstand (Schaf, Porsche, Reichstag) stehen Szenen, bei
denen strittig ist, was überhaupt das Hauptmotiv ist: eine Menschenmenge auf dem
Oktoberfest, ein schwimmender Markt, ein volles Obstregal. Das ist keine
Nachlässigkeit beim Auswählen, sondern der Praxisfall — ein Bilderbestand aus
einem Shop oder Magazin ist genau so gemischt, und ein Modell, das nur
freigestellte Einzelgegenstände beschreiben kann, hilft dort nicht.

Die Bilder stammen von Pixabay und liegen nicht im Repo;
`german_seo_bench.media` sagt, warum, und holt sie. Gemessen wird gegen die
Fassung mit 640 Pixeln an der langen Seite, festgehalten über ihre Prüfsumme
in `data/media.json`.

Die Referenzen tragen dem Rechnung: Wo das Motiv eindeutig ist, steht eine enge
Liste; wo es das nicht ist, eine breitere. Was dabei NICHT passiert ist:
die Wertung je Bild lockern, bis alles durchgeht. Die Grenze zieht `oracle` —
jede Musterlösung muss alle sieben Prüfungen bestehen, und sie ist der
Nachweis, dass die Aufgabe lösbar formuliert ist.

**Warum es kein Stufenprofil gibt.** M01 und M03 schlüsseln `score` nach `level`
auf. 32 Bilder trügen fünf Stufen ohne Weiteres — was fehlt, ist die Messung.
Eine Stufe ist ein Befund aus `python -m german_seo_bench.itemcheck` über
mindestens drei Modelle, keine Einschätzung beim Anlegen des Datensatzes.

**Ein Serverfehler ist hier kein Modellergebnis.** Schickt man ein Bild an ein
Modell ohne Vision-Projektor, antwortet llama-server mit einem Fehler, und der
Lauf bricht ab statt schlecht abzuschneiden. Das ist gewollt: Eine 0.00 aus
einem textblinden Modell sähe in der Tabelle aus wie "kann keine ALT-Texte
schreiben" und hieße "wurde nie gefragt".
"""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.log import HeadlineMetric
from inspect_ai.model import ChatMessage, ChatMessageUser, ContentImage, ContentText
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState, generate, system_message

from german_seo_bench.checks import (
    boilerplate_hit,
    format_ok,
    head_hit,
    is_german,
    overlength_penalty,
    phrase_hit,
)
from german_seo_bench.config import run_config
from german_seo_bench.dataset import dataset_version, load_dataset
from german_seo_bench.media import ENV_DIR, media_dir, media_path, missing
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.server import record_endpoint, record_server

DATA = "m10_alt_text.json"

SYSTEM = (
    "Du beschreibst Bilder für die Barrierefreiheit und schreibst auf Deutsch."
)

# Der Korridor, gegen den GEWERTET wird. Die Obergrenze ist die überall
# zitierte Faustregel für ALT-Texte: Ältere Screenreader-Fassungen (JAWS)
# schneiden bei rund 125 Zeichen ab, und was abgeschnitten wird, ist für den
# Zweck der Angabe verloren. Die Untergrenze ist kein Stilurteil, sondern die
# Bedingung, unter der die inhaltlichen Prüfungen überhaupt tragen:
# "Pinguine." erfüllt `subject` und sagt nichts.
LEN_MIN = 30
LEN_MAX = 125

# Überlänge zieht ab, statt den ALT-Text scheitern zu lassen: je Stufe
# (höchstens so viele Zeichen über LEN_MAX, Abzug). Ein Screenreader, der bei
# 125 Zeichen abschneidet, verliert bei ein paar Zeichen Überhang ein paar
# Wörter am Ende; die Beschreibung davor bleibt brauchbar. Als Null gewertet,
# drückte ein einziger solcher Text im Bericht, der je Datensatz den
# schlechtesten Lauf zählt, ein Modell, das das Bild richtig erfasst hat, bei
# diesem Bild auf 0. Dieselbe Staffel wie M01: Die Abstände messen, wie weit
# ein Modell die Grenze verfehlt, nicht wie lang der Text an sich ist.
#
# Mehr als ein Abzug kommt in M10 nicht zusammen, eine Untergrenze wie in M01
# braucht es deshalb nicht: Der schlechteste bestandene Text bekommt 0.5.
OVERLENGTH_PENALTY = ((10, 0.1), (20, 0.3), (30, 0.5))
LEN_HARD_MAX = LEN_MAX + OVERLENGTH_PENALTY[-1][0]

# Der Korridor, den die ANWEISUNG nennt — enger als der Prüfkorridor. Die
# Begründung steht in `docs/architecture.md` ("Warum der Prompt strenger ist
# als die Prüfung").
#
# In einem Satz: Ein Sprachmodell zählt keine Zeichen, es schätzt sie aus
# Tokens. Eine Zeichengrenze als harte Prüfung misst deshalb die Schätzgüte
# mit, und unter der UND-Wertung schlägt diese Nebenmessung voll auf `score`
# durch — ein Modell, das jedes Bild richtig erfasst, verlöre Punkte an ein
# paar Zeichen Überhang.
#
# Die 15 Zeichen Abstand nach oben sind kein runder Wert, sondern der
# beobachtete Überhang: Modelle überschreiten eine genannte Grenze um bis zu
# 15 bis 17 Zeichen, unabhängig davon, welche Zahl genannt ist. Der Abstand
# muss diesen Überhang abdecken.
#
# Das ist KEINE stille Toleranz: Ein Modell, dem eine engere Grenze genannt
# wird, senkt seine Längen messbar, folgt der Anweisung also. Aufgefangen wird
# nur die Ungenauigkeit dabei.
LEN_MIN_PROMPT = 40
LEN_MAX_PROMPT = 110


# Eine Quelle für Reihenfolge und Bestand der Prüfungen — sonst laufen
# Metrikliste und Wertung bei der nächsten Änderung auseinander. Alle sieben
# zählen gleich, weil `score` sie ohnehin nur mit UND verknüpft.
CHECKS = ("length", "subject", "context", "german", "grounded", "format", "opening")

INSTRUCTIONS = (
    "Schreibe einen deutschen ALT-Text für dieses Bild.\n\n"
    "Regeln:\n"
    f"- Länge zwischen {LEN_MIN_PROMPT} und {LEN_MAX_PROMPT} Zeichen.\n"
    "- Benenne das Hauptmotiv und den erkennbaren Zusammenhang — wo es "
    "aufgenommen ist, was drumherum zu sehen ist.\n"
    "- Beschreibe ausschließlich, was tatsächlich im Bild zu sehen ist. "
    "Ergänze nichts, was du dort nicht siehst.\n"
    "- Sprache: Deutsch.\n"
    "- Fang NICHT mit einer Floskel an: kein \"Bild von\", kein \"Das Foto "
    "zeigt\", kein \"Abbildung einer\". Screenreader kündigen das Bild bereits "
    "an.\n"
    "- Antworte NUR mit dem ALT-Text selbst: eine Zeile, keine "
    "Anführungszeichen, kein Markdown, keine Einleitung."
)


def prompt(inputs: dict[str, Any]) -> list[ChatMessage]:
    """Was das Modell sieht. Bekommt NUR den Eingabe-Teil des Datensatzes.

    Gibt eine Nachrichtenliste zurück statt eines Textes: Ein Bild geht nur als
    `ContentImage` an das Modell. Den Dateipfad löst Inspect vor der Anfrage
    selbst zu einem Data-URI auf (`_eval/task/images.py`).

    Der Text steht VOR dem Bild. Das ist kein Zufall und auch nicht gleichgültig:
    Bei llama.cpp-Modellen hängt das Bild-Token an einer Stelle in der Sequenz,
    und eine Anweisung dahinter wird zuverlässiger befolgt als eine davor. Wer
    die Reihenfolge ändert, ändert die Messbedingung.
    """
    return [
        ChatMessageUser(
            content=[
                ContentText(text=INSTRUCTIONS),
                ContentImage(image=media_path(inputs["image"])),
            ]
        )
    ]


def oracle_prompt(inputs: dict[str, Any]) -> list[ChatMessage]:
    """Die Anweisung ohne das Bild — nur für den Oracle-Lauf."""
    return [ChatMessageUser(content=INSTRUCTIONS)]


@scorer(
    metrics=[
        {
            # Kein `profile(...)` daneben: M10 hat keine kalibrierten Stufen.
            # Siehe Modul-Docstring.
            "score": [mean(), stderr()],
            # Je Prüfung die Bestehensquote. Erst sie macht eine Zahl
            # diagnostisch: Sie sagt, WORAN ein Modell scheitert — und hier
            # besonders, ob es am Sehen liegt (`subject`, `grounded`) oder an
            # der Form (`length`, `format`, `opening`).
            **{check: [mean()] for check in CHECKS},
            # Anteil der Texte im Korridor bis LEN_MAX. `length` meldet nur
            # das Scheitern jenseits von LEN_HARD_MAX; wie oft ein Modell
            # überzieht und dafür Abzug bekommt, steht hier. Keine Prüfung in
            # CHECKS, weil sie nicht ins Tor eingeht.
            "length_ok": [mean()],
        }
    ]
)
def alt_text_scorer() -> Scorer:
    """Die sieben Prüfungen. Sieht Eingabe und Referenz — anders als `prompt()`."""

    async def score(state: TaskState, target: Target) -> Score:
        text = state.output.completion.strip()
        meta = state.metadata

        # Motiv und Zusammenhang treffen auch als Grundwort eines Kompositums,
        # die Sperrliste nur wörtlich — Begründung im Modul-Docstring.
        motif = head_hit(text, meta["subject"])
        situation = head_hit(text, meta["context"])
        invented = phrase_hit(text, meta["forbidden"])
        opener = boilerplate_hit(text)

        # Je Prüfung ein nackter Wahrheitswert; er wandert unverändert in den
        # Score, damit jede Diagnosespalte eine Bestehensquote bleibt.
        passed = {
            "length": LEN_MIN <= len(text) <= LEN_HARD_MAX,
            "subject": motif is not None,
            "context": situation is not None,
            "german": is_german(text),
            "grounded": invented is None,
            # `format_ok` stammt aus M01 und wird hier unverändert
            # weiterverwendet: Markdown, Anführungszeichen, Emoji und
            # Zeilenumbrüche sind in einem ALT-Attribut genauso falsch wie in
            # einem Title-Tag. Eine zweite Fassung daneben wäre der Weg, auf
            # dem die beiden Prüfungen auseinanderdriften.
            "format": format_ok(text),
            "opening": opener is None,
        }
        failed = [name for name, ok in passed.items() if not ok]
        overlength = max(0, len(text) - LEN_MAX)
        # Gerundet, damit 1 − 0.3 als 0.7 im Log steht.
        points = round(1 - overlength_penalty(overlength, OVERLENGTH_PENALTY), 2) if not failed else 0

        # Eine an `max_tokens` abgeschnittene Antwort scheitert an `length` und
        # sähe in der Tabelle aus wie ein zu langer ALT-Text. Der Unterschied
        # gehört benannt: Das Modell hat hier nicht zu viel geschrieben,
        # sondern gar nicht aufgehört.
        truncated = state.output.stop_reason == "max_tokens"

        return Score(
            value={
                # Alles oder nichts, bis auf den Längenabzug. `not failed`
                # und nicht `all(passed)`, damit Wertung und Begründung
                # dieselbe Liste benutzen.
                "score": points,
                **{name: int(ok) for name, ok in passed.items()},
                "length_ok": int(LEN_MIN <= len(text) <= LEN_MAX),
            },
            answer=text,
            explanation=(
                "alle Prüfungen bestanden"
                + (f", Abzug: {overlength} Zeichen zu lang" if overlength else "")
                if not failed
                else ("bei max_tokens abgeschnitten — " if truncated else "")
                + f"gescheitert an: {', '.join(failed)}"
                + (f" (erfunden: {invented})" if invented else "")
                + (f" (Floskel: {opener!r})" if opener else "")
            ),
            metadata={
                "chars": len(text),
                "overlength": overlength,
                # Der Dateiname gehört in die Wertung, nicht nur in die
                # Eingabe. Inspect ersetzt den Bildpfad vor der Anfrage durch
                # ein Data-URI und legt dieses im Log als Anhang ab; in der
                # Eingabe steht danach `attachment://<hash>`. Die Bytes sind
                # damit erhalten (`read_eval_log(..., resolve_attachments=True)`
                # liefert das Bild zurück, nachgemessen bitgleich zur
                # Quelldatei) — der DATEINAME ist es nicht. Ohne diese Zeile
                # müsste man ein Bild über seine Bytes im Repo wiederfinden,
                # um einer Wertung anzusehen, welches Motiv sie betrifft.
                "image": meta["image"],
                "subject_hit": motif,
                "context_hit": situation,
                "invented": invented,
                "boilerplate": opener,
                "truncated": truncated,
            },
        )

    return score


@task
def m10_alt_text(oracle: bool = False) -> Task:
    """Deutsche ALT-Texte für Bilder. Braucht ein bildfähiges Modell.

    Args:
        oracle: Statt eines Modells die Musterlösung einsetzen. Muss 1.00
            ergeben — sonst ist die Wertung kaputt und jede Zahl aus dieser
            Aufgabe wertlos. Der Oracle-Lauf sieht kein Bild an und braucht
            deshalb auch kein bildfähiges Modell.
    """
    if oracle:
        # Der Oracle-Lauf schickt die Anweisung ohne Bild. Er prüft die
        # Wertung, und die liest das Bild nie — so läuft er auch auf einem
        # frischen Checkout, bevor jemand die Bilder geholt hat.
        dataset = load_dataset(DATA, oracle_prompt)
    else:
        dataset = load_dataset(DATA, prompt)
        absent = missing([sample.metadata["image"] for sample in dataset])
        if absent:
            # Hier und nicht erst beim Datensatz: Inspect löst den Bildpfad
            # erst kurz vor der Anfrage auf, und eine fehlende Datei sähe dann
            # im Log aus wie ein Datensatz, an dem das Modell gescheitert ist.
            # ValueError, damit die Hilfe die Aufgabe mit Grund listet.
            raise ValueError(
                f"Bilder fehlen ({len(absent)} von {len(dataset)}) — einmalig holen mit "
                "`uv run python -m german_seo_bench.media` (braucht einen kostenlosen "
                f"Pixabay-API-Schlüssel). Gesucht in {media_dir()} — liegen sie schon "
                f"anderswo, aus jenem Ordner starten oder {ENV_DIR} darauf setzen."
            )
    return Task(
        dataset=dataset,
        # `record_endpoint()` steht DAVOR und `record_server()` dahinter.
        # Die Reihenfolge ist in beide Richtungen begründet: Die Zieladresse
        # muss aufgezeichnet sein, bevor etwas scheitern kann — sonst fehlt
        # sie genau im Fehlerfall. Was der Server geladen hat, weiß man
        # umgekehrt erst hinterher: Erst danach hat llama-swap das Modell
        # geladen und `/props` meldet das richtige. Hier hängt daran mehr als
        # sonst — ob überhaupt ein Vision-Projektor geladen war, steht nur in
        # der Kommandozeile des Upstreams (`--mmproj`).
        solver=oracle_solver()
        if oracle
        else [
            record_endpoint(),
            system_message(SYSTEM),
            generate(),
            record_server(),
        ],
        scorer=alt_text_scorer(),
        config=run_config(),
        version=dataset_version(DATA),
        display_name="M10 — Deutsche ALT-Texte",
        headline_metric=HeadlineMetric(score="score", metric="mean"),
    )
