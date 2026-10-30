# M10 — Deutsche ALT-Texte

## Was die Aufgabe misst

32 Bilder, ein Bild je Anfrage, daraus je ein deutscher ALT-Text. Die Aufgabe
verlangt ein **bildfähiges** Modell, etwa `gemma-4-e4b`:

```bash
uv run inspect eval german_seo_bench/m10_alt_text --model local/gemma-4-e4b \
    --model-base-url http://192.168.1.50:8080/v1
```

Warum die Aufgabe für lokale Modelle interessanter ist als für Cloud-Modelle:
Produktfotos, Kundenbilder und Screenshots aus internen Systemen sind genau die
Dateisorte, die man nicht an eine fremde API schickt. Ein Shop mit ein paar
tausend leeren ALT-Attributen ist der Regelfall, nicht die Ausnahme.

## Die Bilder

Die Bilder liegen nicht im Repo; wie man sie holt, steht in der
[README](../../README.md#die-bilder-für-m10). Ein paar Dinge, die für die
Messung zählen:

- **Gemessen wird gegen die Fassung mit 640 Pixeln** an der langen Seite
  (`webformatURL` der Pixabay-API). Jede Datei hat in `data/media.json` ihre
  SHA-256-Prüfsumme. `python -m german_seo_bench.media` meldet eine Datei mit
  anderen Bytes als `ABWEICHEND` und endet mit Exit-Code 1 — etwa wenn Pixabay
  ein Bild neu kodiert hat. Die Aufgabe läuft dann trotzdem, misst aber nicht
  mehr auf denselben Bytes wie die Referenz.
- **Was das Modell gesehen hat, steht im Log.** Inspect legt jedes Bild als
  Anhang im Log ab; ein Lauf lässt sich also auch dann nachprüfen, wenn das
  Bild bei Pixabay verschwunden ist.
- **Der Oracle-Lauf braucht keine Bilder.** Er schickt die Anweisung ohne Bild,
  weil die Wertung das Bild nie liest. So läuft `uv run pytest` auch auf einem
  frischen Checkout ohne Netz.
- Verschwindet ein Bild bei Pixabay, meldet der Befehl es mit seiner
  Pixabay-Seite. Ersetzt wird es dann durch ein neues Bild mit neuer Referenz —
  und das ist eine neue Aufgabenversion.

Gemessen werden sieben Eigenschaften:

```
length     Länge zwischen 30 und 155 Zeichen (darüber scheitert der Text)
subject    das Hauptmotiv ist benannt
context    der Bildzusammenhang ist benannt (Ort, Umgebung, Situation)
german     der Text ist deutsch
grounded   nichts erfunden, was im Bild nicht zu sehen ist
format     eine Zeile, kein Markdown, keine Anführungszeichen
opening    kein Floskel-Vorspann ("Bild von …", "Das Foto zeigt …")
length_ok  Länge im Korridor 30–125, ohne Abzug (keine Prüfung, nur Diagnose)
```

`score` ist **alles oder nichts** wie in M01: 1, wenn alle sieben Prüfungen
bestehen, sonst 0. Die einzige Abstufung ist die **Überlänge**. Der Prompt
verlangt 40–110 Zeichen, geprüft wird gegen 30–125. Ein ALT-Text, der alle
Prüfungen besteht und trotzdem über 125 Zeichen liegt, bekommt gestaffelt
Abzug, mit derselben Staffel wie in M01:

| Länge | Score |
|-------|-------|
| bis 125 | 1 |
| 126–135 | 0.9 |
| 136–145 | 0.7 |
| 146–155 | 0.5 |
| ab 156 | 0, scheitert an `length` |

Ein Screenreader, der bei 125 Zeichen abschneidet, verliert bei ein paar
Zeichen Überhang nur die letzten Wörter. Als 0 gewertet, wöge das so schwer
wie ein falsches Motiv — und im Bericht, der je Datensatz den schlechtesten
Lauf zählt, drückte ein einziger solcher Text ein Modell, das das Bild richtig
erfasst hat, für dieses Bild auf 0. Die Begründung im Log nennt den Abzug
(„Abzug: 12 Zeichen zu lang“).

**`grounded` ist die Prüfung, um die es geht.** Ein Modell, das ein Motiv
verfehlt, liefert einen unbrauchbaren ALT-Text — ärgerlich, aber sichtbar. Ein
Modell, das plausibel danebenerfindet, liefert einen, der sich gut liest und
falsch ist, und der landet ungeprüft in zehntausend Seiten. Die Sperrlisten sind
deshalb nach einer Regel gebaut: Ein Wort kommt nur hinein, wenn es im Bild
nachweislich **nicht** vorkommt **und** das Klischee zum Motiv ist.

```
Pinguine auf sonnigem Felsstrand   ->  Schnee, Eisscholle, Antarktis, Gletscher
Parabolschüsseln auf einer Wiese   ->  Windrad, Nachthimmel, Rakete
Kürbishaufen                       ->  Halloween, Kerze, Laterne
Keramikladen in Marokko            ->  Teppich, Gewürze, Kamel
Bootshaus am See, kein Boot drin   ->  Boot, Segelboot, Berge
Friedensengel, kahle Bäume         ->  Schnee, Nacht, Wolken
```

Die letzten beiden zeigen, worauf es bei der Auswahl ankommt. Am Bootshaus ist
`Boot` gesperrt, obwohl das Haus so heißt — im Wasser liegt keins, und „Bootshaus
mit Booten" ist eine Erfindung. Beim Friedensengel laden kahle Winterbäume vor
tiefblauem Himmel zum „verschneit" ein; Schnee liegt keiner. Ein Klischee, das
nicht danebenliegt, ist als Sperrwort wertlos: Beim Weihnachtsmarkt **liegt**
Schnee auf den Dächern, dort steht er deshalb im Zusammenhang statt in der
Sperrliste.

**Auch Ortsangaben sind gesperrt**, wo das Bild sie entscheidet: „Berlin" bei den
drei Münchner Motiven, „München" bei den drei Berliner. Ohne sie bekäme ein
Modell, das den Münchner Olympiapark nach Berlin verlegt, die volle Punktzahl.
Verlangt wird dabei keine
Ortskenntnis: „über der Stadt" bleibt richtig, nur die erfundene Genauigkeit
nicht. Gesperrt sind immer beide Wortformen („München" **und** „Münchner") —
unter der Wortgrenzen-Regel ist das ohnehin Pflicht: Geraten wird keine Form.

**Die Suchregel aus M01, mit einer Lockerung.** `subject` und `context` sind
Listen von Phrasen, von denen eine trifft — an Wortgrenzen, buchstabengetreu,
Groß-/Kleinschreibung zählt (siehe [„Die Suchregel"](../architecture.md#die-suchregel-eine-phrase-an-wortgrenzen-wörtlich)).
Dazu trifft ein Listenwort auch als **Grundwort eines Kompositums**
(`head_hit`): „Holzhütte" trifft `Hütte`, „Landstraße" trifft `Straße`,
„Herbstkürbisse" trifft `Kürbisse`. Im Deutschen bestimmt das letzte Glied,
was etwas ist, und Bildbeschreibungen bestehen fast nur aus Komposita. Ohne
die Lockerung fiele ein großer Teil richtiger ALT-Texte durch, und M10 mäße die
Wortlisten statt des Sehens.

Was weiterhin nicht trifft:

| Text | Listenwort | warum nicht |
|---|---|---|
| „Kürbisfeld" | `Kürbis` | das Wort steht nicht am Ende — ein Feld ist kein Kürbis |
| „Kürbissen" | `Kürbis` | Endungen werden nicht geraten, die Form gehört in die Liste |
| „pinguine" | `Pinguine` | am Wortanfang zählt die Schreibweise |

Mehrere Phrasen je Liste bleiben nötig: „Sofa" und „Couch" sind dasselbe
Möbelstück, „Parabolantenne" und „Satellitenschüssel" dieselbe Schüssel.

**Die Sperrliste sucht streng** (`phrase_hit`). Mit Grundwort-Regel sperrte
`Auto` auch die „Elektroauto-Ladestation", und jede Sperrliste müsste jedes
harmlose Kompositum mitbedenken. Andersherum gilt: Trifft ein falsches
Kompositum über die Lockerung als Motiv — „Seilbahn" trifft `Bahn` —, gehört es
in die Sperrliste. Beim Zug im Alpental steht „Seilbahn" deshalb dort.

**Motiv und Zusammenhang überschneiden sich nicht**, auch nicht über ein
Grundwort: Stünde `Kameramann` im Motiv und `Mann` im Zusammenhang, erfüllte
das eine Wort beide Prüfungen. Der Test prüft das mit derselben Regel wie die
Wertung.

**Ein Motiv je Bild, nicht zwei.** `subject` ist eine einzige Liste. Wo ein Bild
zwei gleichrangige Motive hat, steht das unterscheidende in `subject` und das
andere im `context`, wo es inhaltlich hingehört — beim Obstregal der Supermarkt,
beim rauschenden Fernseher die Frau davor. Dafür gilt überall dieselbe Regel.

**Drei Bilder bilden absichtlich ein
Verwechslungs-Dreieck: eine Reihe bunter **Zapfpistolen**, zwei verrostete
**Zapfsäulen** im hohen Gras, und ein blauer **CHAdeMO-Ladestecker** in der
Halterung einer Ladesäule. Der Ladestecker sieht aus wie eine Zapfpistole, und
die beiden Tank-Motive unterscheiden sich nur in Zustand und Zeit. Die
Sperrlisten liegen deshalb über Kreuz: `ladestecker` verbietet Zapfpistole und
Tankstelle, die beiden anderen verbieten Ladesäule und Elektroauto. Ein
Modell, das Muster statt Zustände beschreibt, verliert hier `grounded`.

Bei der Erdfunkstelle reicht die Liste bis „Teleskop", und das ist Absicht: Ob
eine Parabolschüssel zu einer Satelliten-Bodenstation oder zu einem
Radioteleskop gehört, ist **am Bild allein nicht entscheidbar**. Geprüft wird,
ob das Modell Form und Art des Objekts benennt — nicht, ob es die Anlage
identifiziert. Alles andere wäre eine Wissensfrage unter dem Deckmantel einer
Sehprüfung, und dafür gibt es M02.

**`opening` ist eine eigene Prüfung und nicht Teil von `format`.** Beides ist
reparierbar, aber an verschiedenen Stellen: eine Verpackung in Anführungszeichen
mit einem `strip()`, ein „Das Bild zeigt …" nur, indem man das Modell anders
fragt. Zusammengeworfen wäre die Spalte unlesbar, die sagt, ob sich ein
Nachbearbeitungsschritt lohnt. Die Floskel ist dabei keine Stilfrage:
Screenreader kündigen ein Bild über die Rolle des Elements bereits an, der
Vorspann verdoppelt also eine Information, die schon da war.

**Der Bestand ist absichtlich ungleich ergiebig.** Neben Motiven mit einem
eindeutigen Hauptgegenstand (Schaf, Porsche, Reichstag) stehen Szenen, bei denen
strittig ist, was überhaupt das Hauptmotiv ist: eine Menschenmenge auf dem
Oktoberfest, ein schwimmender Markt, ein volles Obstregal. Das ist der
Praxisfall — ein Bilderbestand aus einem Shop oder Magazin ist genau so
gemischt, und ein Modell, das nur freigestellte Einzelgegenstände beschreiben
kann, hilft dort nicht.

Die Referenzen tragen dem Rechnung: Wo das Motiv eindeutig ist, steht eine enge
Liste; wo es das nicht ist, eine breitere. Was dabei **nicht** passiert: die
Wertung je Bild lockern, bis alles durchgeht. Die Grenze zieht der
Oracle-Lauf — jede Musterlösung muss alle sieben Prüfungen bestehen, und sie ist
der Nachweis, dass die Aufgabe lösbar formuliert ist.

**Der erste Lauf gegen ein echtes Modell ist ein Test der Referenz.** Ein
Ausfall kann richtig sein und nur nicht in der Referenz stehen („Pferdedroschke"
statt „Kutsche", „CRT-TV" statt „Fernseher"), und ein echter Fehler kann als
richtig durchgehen. Der Oracle-Lauf kann das
nicht finden: Er setzt die Musterlösung ein, die aus derselben Hand stammt wie
die Referenz, und geht deshalb notwendig durch. Er beweist, dass die Wertung
rechnet — nie, dass sie das Richtige verlangt.

Wer M10 um Bilder erweitert, liest den ersten Lauf deshalb Antwort für Antwort,
und zwar die bestandenen mit: Dort steckt der teurere Fehler.

**Es gibt kein Stufenprofil**, und das ist eine Enthaltung, keine Lücke. 32
Bilder trügen fünf Stufen ohne Weiteres; was fehlt, ist die Messung. Eine Stufe
ist hier ein Befund aus `itemcheck` über mindestens drei Modelle, keine
Einschätzung beim Anlegen des Datensatzes.

**Wie groß muss `--ctx-size` sein?** Ein Datensatz ist **eine** Anfrage mit
**einem** Bild, ohne Historie, und `max_connections` steht auf 1 — es läuft also
auch nur ein Datensatz zur Zeit. Das Kontextfenster muss ein Bild plus Anweisung
plus Antwort fassen, nicht den Bestand.

Wie viele Tokens ein Bild kostet, entscheidet der Vision-Encoder und nicht die
Datei: Er skaliert jedes Bild auf sein festes Raster, weshalb die 640×360 große
Datei (62 KB) und die 800×649 große (246 KB) hier dasselbe kosten dürften. Eine
Schätzung steht deshalb nicht in dieser Datei — die Zahl steht nach dem ersten
Lauf im Log, je Datensatz unter `model_usage` als `input_tokens`, und das
tatsächlich geladene Kontextfenster daneben als `n_ctx` in `metadata.server`.

**Ein Serverfehler ist hier kein Modellergebnis.** Schickt man ein Bild an ein
Modell ohne Vision-Projektor, antwortet llama-server mit einem Fehler, und der
Lauf bricht ab, statt schlecht abzuschneiden. Das ist gewollt: Eine 0.00 aus
einem textblinden Modell sähe in der Tabelle aus wie „kann keine ALT-Texte
schreiben" und hieße „wurde nie gefragt". Ob überhaupt ein Projektor geladen
war, steht nach dem Lauf in `metadata.server` — in der Kommandozeile des
Upstreams als `--mmproj`.

**Welches Bild war das?** Inspect ersetzt den Bildpfad vor der Anfrage durch ein
Data-URI und legt dieses im Log als Anhang ab; in der Eingabe steht danach
`attachment://<hash>`. Die Bytes bleiben erhalten —

```python
read_eval_log(pfad, resolve_attachments=True)   # liefert das Bild zurück
```

— der **Dateiname** nicht. Deshalb schreibt der Scorer ihn zusätzlich nach
`Score.metadata.image`. Ohne das müsste man ein Bild über seine Bytes im Repo
wiederfinden, um einer bestrittenen Wertung anzusehen, welches Motiv sie
betrifft.
