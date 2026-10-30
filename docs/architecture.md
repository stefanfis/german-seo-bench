# Aufbau und Methodik

## Aufgabenarten

`M…` steht für *model-only* — eine Anfrage, eine Antwort, kein Werkzeug. Genau
so wird ein Modell aus Screaming Frog oder einem Skript heraus gerufen.

`A…` sind Agent-Aufgaben, bei denen ein Modell über MCP-Server Werkzeuge
bedient. Sie bringen die einzige zusätzliche Voraussetzung mit: Docker. Inspect
setzt das selbst um: `Task(sandbox=("docker", …))` und eine `compose.yaml`
neben den Daten, mehr braucht es nicht. Aufruf, Log und Wertung bleiben
identisch; nur der Solver wechselt von `generate()` zu einem Agenten mit
Werkzeugen (`inspect_ai.agent.react` plus `mcp_server_sandbox`).

In den M-Aufgaben wird **je Datensatz einzeln** gefragt, nicht in Stapeln. Das
entspricht der Praxis, und es schließt einen Messfehler aus: In einem Stapel
beeinflussen sich die Datensätze gegenseitig — eine unveränderte Frage kann
kippen, nur weil eine andere daneben umformuliert wurde.

## Die Suchregel: eine Phrase, an Wortgrenzen, wörtlich

`phrase_hit` ist die Suchregel für erzeugten Text in M01 und M10 — für
Pflichtphrasen (`required`, `subject`, `context`) und für Sperrlisten
(`forbidden`) gleichermaßen. M02 und M03 benutzen eine tolerantere Fassung
(`word_hit`: Wortgrenzen, aber Umlaute aufgelöst und Schreibweise egal). Drei
Eigenschaften:

| | |
|---|---|
| **Eine Ebene, ODER** | Die Liste nennt die Formen, die zählen. Eine davon genügt. Wer mehr verlangen will, schreibt die längere Phrase hinein: `"Birnen ernten"`. |
| **Wortgrenzen** | „Birnenernten" trifft `"Birnenernte"` nicht, „Glühbirne" nicht `"Birne"`. Ein Bindestrich ist eine Grenze, „Cookie-Einwilligung" trifft also `"Cookie"`. |
| **Schreibweise zählt** | „birnenernte" und „BIRNENERNTE" treffen `"Birnenernte"` nicht. Im Deutschen trägt die Groß-/Kleinschreibung Bedeutung — „buchen" und „Buchen" sind verschiedene Dinge —, und ein durchgehend groß geschriebener Titel ist auch als SEO-Titel keine gute Wahl. |

Nicht toleriert werden damit: Teilwörter, geratene Flexionsendungen und
aufgelöste Umlaute („Kaese" trifft „Käse" nicht). Wer eine Form zulassen will,
schreibt sie in die Liste.

**Eine Ausnahme: Motiv und Zusammenhang in M10** (`head_hit`). Dort trifft ein
Listenwort auch als Grundwort eines Kompositums — „Holzhütte" trifft `Hütte`,
„Kürbisfeld" `Kürbis` aber nicht. Bildbeschreibungen bestehen fast nur aus
Komposita, und deren Bestimmungswörter lassen sich nicht vorher aufzählen.
Sperrlisten und M01 suchen weiter streng. Einzelheiten in
[M10](tasks/m10_alt_text.md).

**Warum so streng.** Einzelne vertretbare Titel fallen dadurch durch. Das ist
der Handel, und er ist bewusst so gewählt: Ein Benchmark, der nachvollziehbar
sein soll, gewinnt mehr durch **viele Aufgaben unter einer Regel, die jeder
nachrechnen kann**, als durch eine Regel, die in jedem Einzelfall fair sein will
und die am Ende niemand mehr überblickt. Teilworttreffer, geöffnete
Flexionsendungen, Umlautfaltung und mehrstufige Referenzen ergeben zusammen
Toleranzen, deren Zusammenwirken man einer Zahl nicht mehr ansieht. Zuwachs an
Aussagekraft kommt über die Zahl der Datensätze, nicht über Sonderbehandlungen
im Einzelfall.

Dass die Sperrliste dadurch **weicher** wird (`"Filiale"` fängt „Filialen"
nicht), ist in Kauf genommen. Die Aufgabe ist nicht, zu entscheiden, was auf
einer Website online gehen darf, sondern die groben Unterschiede zwischen
Modellen zu zeigen.

## Warum der Prompt strenger ist als die Prüfung

Eine Regel, die für jede Aufgabe gilt, die in ihrer Anweisung eine **Zahl**
nennt: Der Korridor im Prompt ist **enger** als der, gegen den gewertet wird.

```python
LEN_MIN, LEN_MAX = 30, 125                 # was die Wertung durchgehen lässt
LEN_MIN_PROMPT, LEN_MAX_PROMPT = 40, 110   # was die Anweisung verlangt
```

Der Grund: **Ein Sprachmodell zählt keine Zeichen.** Es arbeitet in Tokens und
schätzt die Zeichenzahl. Eine Zeichengrenze, die als harte Prüfung wirkt, misst
deshalb zu einem guten Teil diese Schätzfähigkeit mit — eine Eigenschaft des
Tokenizers, nicht der Aufgabe. Unter einer UND-Wertung schlägt diese
Nebenmessung voll auf die Leitzahl durch.

Ein Modell, das in M10 jedes Bild richtig erfasst, aber ein paar Antworten
einige Zeichen über die Grenze schreibt, verlöre sonst gegen ein Modell, das
Motive verfehlt, aber kürzer schreibt — die Leitzahl empföhle das schwächere
Modell. Mit dem Abstand verschwindet der Effekt.

**Das ist keine stille Toleranz.** Modelle, denen eine engere Grenze genannt
wird, senken ihre Längen messbar, folgen der Anweisung also. Aufgefangen wird
nur die Ungenauigkeit dabei. Der Abstand muss deshalb mindestens so groß sein
wie der typische Überhang eines Modells — beobachtet wurden bis zu 15 bis 17
Zeichen, unabhängig von der genannten Zahl.

Drei Zusicherungen halten die Regel zusammen (`tests/conftest.py`,
`assert_prompt_corridor`): Der Prompt-Korridor liegt **im** Prüfkorridor;
es gibt überhaupt einen Abstand; und die **Musterlösungen halten den engeren
Korridor ein** — sonst verlangt die Aufgabe etwas, das ihre eigene Referenz
nicht einhält.

**In M01 liegt der Abstand nach oben in der Prüfung** (Prompt 40–60, Prüfung
35–66), und die dritte Zusicherung ist der Grund: Die 29 Musterlösungen liegen
zwischen 47 und 60 Zeichen. Eine Prompt-Obergrenze unter 60 ließe einige aus
der eigenen Anweisung fallen. Ohne Abstand nach oben bekämen Modelle, die bis
an die genannte Grenze schreiben, für bis zu ein Drittel ihrer Titel Abzug
wegen weniger Zeichen zu viel. Jenseits von 66 zieht M01 gestaffelt ab und
lässt erst ab 97 Zeichen scheitern ([Abzüge](tasks/m01_titles.md#abzüge)).

Zwei Dinge, die dazugehören:

- **Eine Prompt-Änderung ist eine Versionsänderung.** Auch wenn kein Datensatz
  und keine Prüffunktion angefasst wurde. Sonst tragen zwei verschiedene
  Aufgaben dieselbe Nummer.
- **Die Prüfung wird dadurch stumpf.** `length` steht in M10 bei sorgfältigen
  Modellen auf 1.00 — erst recht, seit Überlänge dort bis 155 Zeichen nur
  abzieht; wie oft ein Modell überzieht, zeigt `length_ok`. Unter der UND-Wertung kostet das nichts, und als
  Rückfallsicherung gegen ein Modell, das Aufsätze schreibt, bleibt sie
  sinnvoll — wie `german` und `meaning` in M01. Nur darf niemand `length 1.00`
  als „die Modelle können Zeichen zählen" lesen.

## Eine eigene Aufgabe bauen

Zwei Dateien: die Daten und die Aufgabe.

```
src/german_seo_bench/data/m04_my_task.json     # {"task", "title", "version", "samples"}
src/german_seo_bench/tasks/m04_my_task.py      # prompt(), @scorer, @task
```

```json
{
  "task": "M04",
  "title": "Meine Aufgabe",
  "version": 1,
  "samples": [
    { "id": "…", "input": { … }, "reference": { …, "oracle": "…" } }
  ]
}
```

Danach in `tasks/__init__.py` und `_registry.py` eintragen und den Oracle-Lauf
starten. Zeigt er nicht 1.00, stimmt die Prüfung nicht.

Eine Falle beim Bauen von Datensätzen: **Kein Sperrwort darf so kurz sein, dass
es mit Flexionsendung ein Allerweltswort trifft**: `ei` + `n` ergibt `ein`, und
damit scheiterte fast jeder Titel. Geprüft wird das nicht über eine Mindestlänge
— „LED" und „GTI" sind brauchbare Sperrwörter —, sondern gegen die **Anweisung
des Prompts**: Die ist gewöhnliche deutsche Prosa, und was dort anschlägt, ist
für eine Sperrliste untauglich
(`test_sperrwort_trifft_die_anweisung_nicht`, in M01 und M10).

**Bilder** statt Text: `prompt()` darf statt einer Zeichenkette eine fertige
Nachrichtenliste liefern. Den Dateipfad löst Inspect vor der Anfrage selbst zu
einem Data-URI auf; `media_path()` macht daraus einen Pfad ins
Medienverzeichnis, der auch über `uvx` ohne Checkout stimmt. Neue Bilder
gehören mit Quelle und Prüfsumme in `data/media.json`, nicht ins Paket.

```python
def prompt(inputs):
    return [ChatMessageUser(content=[
        ContentText(text=INSTRUCTIONS),
        ContentImage(image=media_path(inputs["image"])),
    ])]
```

Die Regel, an der alles hängt: **`prompt()` bekommt nur `input`.** Die Referenz
erreicht es gar nicht — nicht aus Disziplin, sondern weil `load_dataset()` sie
nicht übergibt. Was in `reference` steht, sieht das Modell nie; es landet in
`Sample.metadata` und damit nur im Log und beim Scorer.

`version` wird hochgezählt, sobald sich Prompt oder Prüfung ändern. Sie steht
in jedem Log — sonst vergleicht man später Zahlen aus zwei verschiedenen
Aufgaben unter einem Namen.

## Aufbau

```
src/german_seo_bench/
    checks/        Prüflogik als reine Funktionen — ohne Inspect, einzeln testbar
        text.py        Suchregel `phrase_hit` (und `head_hit` für M10), Titelprüfungen, `fold`, `word_hit`
        language.py    Sprachprüfung über py3langid (M01, M10)
        choice.py      Buchstaben auslesen, Wertung und Validator (M02)
        facts.py       Feldvergleiche (M03)
        alt_text.py    Floskel-Vorspann am Textanfang (M10)
        audit.py       Meldung einlesen, Precision/Recall/F1, Verhalten (A01)
        judge.py       Prompt und Auslesen des Titel-Judges (M01)
        json_answer.py JSON aus einer Modellantwort holen (M03, A01)
    data/          die eingefrorenen Datensätze, die Prüflandschaft aus A01
                   (Seiten, nginx-Regeln, die compose-Dateien, `render.js`) und
                   `media.json`, das Manifest der Bilder aus M10
    media/         `python -m german_seo_bench.media`: holt die Bilder, die das
                   Repo nicht mitliefert, und prüft ihre Prüfsummen
    reference/     rechnet die Referenz von A01 aus einem Render-Capture
        audit.py       die Regeln — sie ERZEUGEN die Referenz, sie prüfen nicht
        __main__.py    `python -m german_seo_bench.reference`: rendern und vergleichen
    tasks/         je Aufgabe eine Datei: prompt(), @scorer, @task
    config.py      Laufvorgaben: seriell, mit Zeitgrenze, wenige Versuche
    dataset.py     lädt Datensätze, hält die Referenz vom Prompt fern, `data_path`
    export_titles.py  `python -m …export_titles`: M01-Titel als CSV zum Bewerten
    help.py        `uv run german-seo-bench`: die Aufrufe und alle Aufgaben
    itemcheck.py   Trennschärfe je Datensatz über mehrere Läufe hinweg
    judge.py       `python -m …judge`: einen fertigen M01-Lauf nachträglich werten
    metrics.py     `profile()` und die Kalibrierungsmetriken des Judges
    oracle.py      der Solver, der die Musterlösung statt eines Modells einsetzt
    providers.py   `local/…` mit Voreinstellung localhost:8080
    report.py      `python -m …report`: viele Läufe zu Tabellen zusammenfassen
    server.py      hält die Laufbedingung fest: die Zieladresse vor der ersten
                   Anfrage, den geladenen Upstream nach der Antwort
    testsite.py    `python -m …testsite`: die Prüflandschaft von A01 lokal
                   ausliefern, zum Ansehen und Crawlen — nicht zum Messen
docs/
    tasks/         je Aufgabe Aufbau und Begründungen
    A01.md         Anleitung und Auflösung zu A01 — für Menschen, die
                   mitspielen wollen (siehe `tests/test_a01_doku.py`)
tests/             Zusicherungen und Gegenproben, einschließlich Oracle-Lauf
```

Die Sandbox von A01 liegt bei den **Daten** und nicht beim Aufgabenmodul:
`site/` ist Teil des Datensatzes, und `compose.yaml` montiert es relativ zu sich
selbst. Über `data_path()` findet Inspect die Datei auch dann, wenn das Paket per
`uvx` ohne Checkout läuft.

`fold` und `word_hit` stehen in `text.py` und nicht bei einer Aufgabe: Beide
sind Normalisierung, beide brauchen M02 und M03. Ein zweiter
Normalisierer neben `normalize` wäre der Weg, auf dem die Referenzen
auseinanderdriften.

Die Trennung von `checks/` und `tasks/` hat einen Grund: Eine Prüfung, die nur
über einen kompletten Lauf zu testen ist, wird nicht getestet. `uv run pytest`
läuft in Sekunden, ohne Modell und ohne Netz.

Zu jeder Zusicherung gehört dort die Gegenprobe — der Fall, in dem sie
anschlagen muss. Eine Prüfung, die nicht anschlägt, wenn man sie bricht, ist
keine. Das gilt auch für den Oracle-Lauf selbst:
`test_gegenprobe_scorer_unterscheidet` schickt eine absichtlich untaugliche Antwort durch denselben Scorer und
verlangt, dass sie *nicht* 1.00 ergibt.

## Misst ein Datensatz noch etwas?

Der Oracle-Lauf beweist, dass die **Wertung** funktioniert. Er beweist nie, dass
ein Datensatz etwas **misst**. Ein Item, das jedes Modell löst, hebt den Wert um
einen festen Betrag und sieht in jedem einzelnen Lauf wie Können aus; eines, das
keines löst, sieht wie ein schweres Item aus und ist womöglich eine falsch
notierte Referenz. Sichtbar wird beides erst über mehrere Modelle hinweg:

```bash
uv run python -m german_seo_bench.itemcheck logs/*.eval
uv run python -m german_seo_bench.itemcheck logs/*.eval --task m03_facts
```

Vier Fragen, vier Befunde je Datensatz:

```
Referenz?   kein Modell trifft je      -> von Hand prüfen, bevor man "schwer" sagt
stumpf      jedes Modell trifft immer  -> Streichkandidat, misst nichts mehr
wackelig    dasselbe Modell schwankt   -> die Einzelmessung ist wertlos
Vorschlag   Stufe aus der Messung      -> Grundlage der Nachkalibrierung
```

Jedes Modell zählt gleich schwer, egal mit wie vielen Läufen es vertreten ist —
sonst kippt ein Modell mit fünf Läufen die Statistik gegen eines mit einem.
Oracle-Läufe zählen gar nicht mit; sie sind per Konstruktion 1.00 und meldeten
jeden Datensatz als stumpf.

**Unter drei Modellen spricht das Werkzeug kein Urteil aus.** Ein Datensatz, den
ein einzelnes Modell nie löst, ist ein schweres Item und kein Referenzfehler —
diese Verwechslung wäre die teuerste Fehlbedienung, weil sie dazu führt, eine
richtige Referenz zu „korrigieren".

## Zuverlässigkeit statt Schwierigkeit

Alles oben misst, ob ein Modell eine Aufgabe lösen **kann**. Die andere Frage
ist, ob es das **verlässlich** tut — und die beantwortet erst die Wiederholung:

```bash
uv run inspect eval german_seo_bench/m01_titles --model local/… --epochs 5,mean
uv run inspect eval german_seo_bench/m01_titles --model local/… --epochs 5,pass_k_5
```

Weil `score` binär ist, lesen sich die beiden Reducer direkt:

```
5,mean       in wie vielen von fünf Anläufen war dieser Datensatz richtig
5,pass_k_5   Wahrscheinlichkeit, dass ALLE fünf Anläufe sitzen
```

`pass_k_5` ist die Zahl für „kann ich das unbeaufsichtigt über 10.000 Seiten
laufen lassen". Ein Modell mit `mean` 0.80 und `pass_k_5` 0.35 kann die Aufgabe
und ist trotzdem nicht automatisierbar.

Der Bericht (`python -m german_seo_bench.report`) rechnet genau so, über
getrennte Läufe wie über Epochen: Je Datensatz zählt der schlechteste Score,
siehe [„So wird gerechnet“](running.md#läufe-zusammenfassen). Das ist der
einzige Score, den er ausweist.

Das setzt **Temperatur über 0** voraus. `config.py` setzt sie bewusst nicht; sie
kommt aus dem llama-swap-Eintrag, und bei greedy liefern fünf Epochen fünfmal
dieselbe Antwort — eine Streuung von exakt null, die wie Stabilität aussieht und
keine ist. Für Zuverlässigkeitsläufe also einen Alias
mit Temperatur > 0 wählen; was tatsächlich galt, steht danach als
`metadata.server` im Log. Einheitlich geregelt ist die Temperatur noch nicht:
Den agentischen Aufgaben lassen sich Sampling-Parameter nicht allgemein
mitgeben, deshalb kommen sie aus der Konfiguration des Servers.


## Sprache im Code

Bezeichner, JSON-Schlüssel und Dateinamen sind englisch. Kommentare, Hilfetexte
und Meldungen sind deutsch — sie erklären Entscheidungen, und das tun sie in
der Sprache, in der hier gearbeitet wird.
