# M01 — Deutsche SEO-Seitentitel

## Was im Seitentext steht

Der Seitentext ist **echter Seiteninhalt**, nicht dessen Zusammenfassung: 1500
bis 2100 Zeichen, mehrere Absätze, HTML-Auszeichnung mit Überschriften und
Listen, Abschweifungen — und in jedem Datensatz ein Teaser auf einen **anderen**
Artikel:

```html
<div class="teaser"><p>Neu im Gartenteil: Hühner im eigenen Garten halten.
Wie groß der Stall sein muss …</p><p><a href="huehner-halten.html">Weiterlesen…</a></p></div>
```

Das simuliert den tatsächlichen Aufruf aus Screaming Frog heraus: Was dort ankommt,
ist Seiteninhalt mit Navigation, Markup und Querverweisen, nicht ein sauber
destillierter Absatz.

Ein kurzer, thematisch geglätteter Text wäre bereits die halbe Antwort — das
Modell müsste nur noch kürzen. Gemessen werden soll aber, ob es das Thema
**findet**. Deshalb steht es nicht im ersten Satz, und die `<h1>` nennt eine
Rubrik („Aus der Küche", „Sport ausprobieren"), keinen Seitentitel.

Der Teaser ist die realistischste Falle: Wer daraus titelt, scheitert je nach
Teaser an `keyword` (fremdes Thema) oder an `meaning` (das Vokabular der
falschen Bedeutung). Und wer Markup in den Titel übernimmt, scheitert an
`format`.

Die Prompts sind damit rund 700 Tokens groß statt 250. Das Kontextfenster des
Servers sollte mit `--ctx-size` entsprechend bemessen sein.

Der Längenkorridor, den der Prompt nennt, ist **enger** als der, gegen den
gewertet wird (40–60 statt 35–66, darüber gestaffelter [Abzug](#abzüge)) — warum, steht unter [„Warum der Prompt
strenger ist als die Prüfung"](../architecture.md#warum-der-prompt-strenger-ist-als-die-prüfung).

## Kein Keyword im Prompt

Das Zielkeyword steht **nur in der Referenz**, nicht in der Aufgabe. Das Modell
muss das Thema aus dem Seiteninhalt selbst erschließen — genau das, wofür man es
aus Screaming Frog heraus aufruft.

Geprüft wird gegen eine Liste von Phrasen, von denen **eine** vorkommen muss:

```json
"required": ["Birne", "Birnen"]
```

Gesucht wird nach [der Suchregel](../architecture.md#die-suchregel-eine-phrase-an-wortgrenzen-wörtlich):
eine Phrase, an Wortgrenzen, wörtlich. Zwei Zusicherungen halten die
Datensätze gesund: Mindestens eine Pflichtphrase **muss im Seitentext stehen**
(sonst ist die Aufgabe nicht schwer, sondern unlösbar — das Keyword steht ja
nicht im Prompt), und **keine Pflichtphrase darf ein Sperrwort treffen** (sonst
laufen `keyword` und `meaning` gegeneinander und der Datensatz ist nicht zu
bestehen).

**Die Liste ist großzügig.** Gemessen wird, ob das Modell einen guten Titel zum
Thema schreibt, nicht, ob es das eine Wort errät, das gemeint war. In die Liste
gehört deshalb jede Form, unter der ein guter Titel das Thema benennt:
Zusammensetzungen („Krebsvorsorge“, „Poolpflege“) und gängige Synonyme
(„Regenbogenhaut“ für `Iris`, „Teppich“ für `Läufer`). Nicht hinein gehört die
Rubrik aus der `<h1>` („Kleine Reparaturen im Bad“): Wer sie übernimmt, hat das
Thema gerade nicht gefunden.

**Woran sich der Zuschnitt der Datensätze orientiert** — nicht am Gefühl,
sondern an der Bestehensquote je Prüfung. Eine Prüfung, die jedes Modell
besteht, hebt den Score um einen festen Betrag und sagt über das Modell nichts.
Diese Quote gehört deshalb vor jede Erweiterung eines Datensatzes, nicht
danach.

## Was die Aufgabe ausgibt

```
score/mean         Anteil vollständig brauchbarer Titel, abzüglich Abzüge
length/mean        Länge zwischen 35 und 96 Zeichen (darüber scheitert der Titel)
keyword/mean       eine Pflichtphrase steht im Titel
german/mean        Titel ist deutsch
meaning/mean       Homonym richtig aufgelöst
format/mean        nur der Titel, kein Beiwerk
style/mean         kein Stilverstoß (über alle Titel, geht nicht ins Tor ein)
keyword_front/mean Pflichtphrase steht im Titel und beginnt spätestens bei Zeichen 33
length_ok/mean     Länge im Korridor 35–66, ohne Abzug
german_seo_bench/by_level_1 … _5   dasselbe je Schwierigkeitsstufe
```

**`score` ist alles oder nichts.** 1, wenn alle fünf Prüfungen bestehen, sonst
0 — also: Wie viele Titel kann ich ungeprüft in den Title-Tag übernehmen? Ein
Titel mit vier von fünf bestandenen Prüfungen ist nicht zu 80 % brauchbar; er
muss angefasst werden. Die einzige Ausnahme nach unten sind die
[Abzüge](#abzüge): Ein bestandener Titel kann statt 1 auch 0.9 oder 0.8
bekommen.

Warum nicht der gewichtete Anteil bestandener Prüfungen: `german` und `meaning`
schlagen selten an und schenkten damit jedem Modell einen festen Sockel.
Naheliegend wäre, die beiden geringer zu gewichten — nur ändert das nichts.
Jede Gewichtung ist eine affine Abbildung des Scores ohne diese Prüfungen:
gleiche Rangfolge, gleiche relativen Abstände, nur eine gedehnte Skala. Die
UND-Wertung ist dagegen nicht affin und kann die Rangfolge zweier Modelle
tatsächlich umstellen.

`german` und `meaning` bleiben trotzdem Prüfungen. Unter der UND-Wertung kosten
sie nichts, und sie melden weiterhin, wenn ein Modell ins Englische abgleitet
oder das Homonym verfehlt.

Erst die Aufschlüsselung macht die Zahl brauchbar, und sie ist der einzige Ort,
an dem Teilerfolge stehen: Zwei Modelle mit `score` 0.35 sind völlig
verschiedene Fälle, je nachdem ob sie an `format` scheitern (mit einem
Post-Processing-Schritt reparierbar) oder an `meaning` (nicht reparierbar — das
Modell hat die Seite nicht verstanden).

## Abzüge

Kleine Modelle liefern Titel, die alle fünf Prüfungen bestehen und trotzdem
sofort falsch aussehen, das Keyword verstecken oder etwas zu lang sind. Sieben
Regeln ziehen dafür ab, **nur bei Titeln, die alle Prüfungen bestanden
haben**. Jede Regel zieht höchstens einmal ab. Die ersten fünf sind
Stilregeln (`style_violations` in `checks/text.py`), dann folgen die Stellung
des Keywords und die Überlänge:

| Regel | Abzug | trifft |
|-------|-------|--------|
| `spaces` | 0.2 | weniger als zwei Leerzeichen: „Sommer-Mosquito-Stich-Vorbeugung“ |
| `capital` | 0.2 | beginnt weder mit Großbuchstabe noch mit Ziffer |
| `title_case` | 0.1 | ein Abschnitt aus drei oder mehr großgeschriebenen Wörtern: „Igel Richtig Füttern“ |
| `charset` | 0.1 | Zeichen außer Buchstaben, Ziffern und `- – — \| : , . & / ( ) ! ? + % ' ’ € °` |
| `sentence_end` | 0.1 | endet auf einen Punkt, ist also ein Satz und keine Überschrift |
| spätes Keyword | 0.2 | die früheste Pflichtphrase beginnt erst nach Zeichen 33 (`LEN_MAX // 2`): „Kleine Reparaturen im Bad: Dichtung wechseln & Wasserhahn fixen“ |
| Überlänge | 0.1 / 0.3 / 0.5 | 67–76 / 77–86 / 87–96 Zeichen (`OVERLENGTH_PENALTY`); ab 97 scheitert der Titel an `length` |

**Warum Abzug und nicht Null.** Die Regeln sind grob und treffen einzelne
richtig geschriebene Titel; „Geschmortes Rindfleisch Rezept“ gilt für
`title_case` als Verstoß. Ein Fehlalarm darf einen sonst einwandfreien Titel
nicht ganz kosten. Wie viele Abzüge auch zusammenkommen, ein bestandener
Titel bekommt mindestens 0.1 (`SCORE_FLOOR`) und liegt damit immer über
einem, der eine Prüfung verfehlt.

**Warum Überlänge nur abzieht.** Ein guter Titel, der ein paar Zeichen zu
lang ist, verliert bei Google ein Wort und bleibt brauchbar. Als 0 gewertet,
wöge ein Zeichen zu viel so schwer wie ein Titel zum falschen Thema — und im
Bericht, der je Datensatz den schlechtesten Lauf zählt, drückte ein einziger
solcher Titel ein sonst starkes Modell für diesen Datensatz auf 0. Gestaffelt
wird ab `LEN_MAX` (66), nicht ab der Prompt-Vorgabe (60): Der Abstand dazwischen
ist der Spielraum fürs Zeichenschätzen. Erst über 96 Zeichen ist ein Titel kein
Title-Tag mehr, sondern ein Satz.

**Warum das Keyword vorne stehen soll.** Google kürzt lange Titel, und was
hinten steht, liest man zuletzt oder gar nicht. Gezählt wird ab der Stelle, an
der die **früheste** Pflichtphrase beginnt (`phrase_position`), nach derselben
Suchregel wie `keyword`. Die Grenze ist absolut, nicht relativ zur Titellänge:
Sie lässt Platz für ein, zwei Wörter vor dem Keyword („Kabellose Maus …“),
aber nicht für eine vorangestellte Rubrik aus der `<h1>`. Das ist fast immer
der Grund, wenn das Keyword zu spät kommt.

**Warum `title_case` Abschnitte prüft.** „Mindestens ein kleingeschriebenes
Wort“ wäre die naheliegende Regel. Sie träfe aber richtig geschriebene Titel aus
lauter Substantiven wie „Mandelentzündung: Symptome, Ursachen & Behandlung“.
Deshalb wird an `: – — | , & / ( ) ! ?` und „ - “ in Abschnitte getrennt, und
erst ein Abschnitt aus drei oder mehr Wörtern, die alle groß beginnen, zählt als
Verstoß. Ein kleingeschriebenes Wort im Abschnitt genügt. Eine Regel über
„drei Großgeschriebene in Folge“ träfe die Höflichkeitsform („Wann bekommen
Sie Ihr Geld zurück“).

Emojis und Markdown stehen nicht unter den Stilregeln: An ihnen scheitert
schon `format`.

`style/mean` ist der Anteil der Titel ohne Stilverstoß, gerechnet über alle
Titel. `keyword_front/mean` ist der Anteil, in dem das Keyword vorkommt und
vorne steht; `keyword − keyword_front` ist also der Anteil mit spätem Keyword.
Welche Abzüge ein Titel bekommt, steht in der Begründung, dazu in den
Metadaten `style_violations` und `keyword_pos`. `itemcheck` zählt einen Datensatz nur
mit vollem Punkt als bestanden. Ein Titel mit Abzug gilt dort also als nicht
vollständig gelöst.

## Die Stufen

Jeder der 29 Datensätze trägt eine Stufe von 1 bis 5 (je sechs, die höchste
fünf). Sie ist **gemessen und nicht geschätzt**: Grundlage ist der Anteil der
Läufe, in denen ein Datensatz vollständig bestanden wurde, über mehrere Läufe
von vier lokalen Modellen. Die Bänder sind gleich groß, weil eine Stufe mit zwei
Datensätzen keine Aussage trägt.

Damit ist die Einteilung die Schwierigkeit **für diese Modelle**. Kommen
stärkere dazu, verschiebt sie sich; nachgerechnet wird mit `itemcheck` (siehe
[„Misst ein Datensatz noch etwas?"](../architecture.md#misst-ein-datensatz-noch-etwas)),
nicht nach Gefühl.

## Der Titel-Judge

Ob ein Titel so klingt, wie ihn ein deutscher Muttersprachler schreiben würde,
können die fünf Prüfungen nicht feststellen. Das übernimmt ein zweites Modell
als Judge, und zwar **nach** dem Lauf, getrennt von ihm:

```bash
uv run python -m german_seo_bench.judge logs/….eval --judge local/gemma-4-12b \
    --judge-base-url http://192.168.1.50:8080/v1
uv run python -m german_seo_bench.judge logs/….eval --judge anthropic/claude-opus-5
```

**Warum getrennt.** Hinge der Judge als Option am Lauf, ließe sich ein Lauf
nicht später mit einem neuen Judge werten. Kandidat und Judge müssten
gleichzeitig geladen sein, und zwei lokale Modelle passen oft nicht zusammen
auf eine Maschine. Und mehrere Judges gäbe es nur über mehrere Läufe. So liest
der Judge die Titel aus dem fertigen Log. Wer viele Logs mit mehreren Judges
wertet, geht am besten Judge für Judge vor, sodass llama-swap jedes
Judge-Modell nur einmal lädt.

**Wohin die Urteile gehen.** In eine Datei neben dem Log, `X.eval` →
`X.judges.json`. Das Log selbst bleibt unverändert. Jeder Judge ist dort ein
eigener Eintrag unter `judges`, mit Laufbedingungen (`endpoint`, `server`),
Zusammenfassung (`summary`) und jedem Urteil samt Rohantwort (`samples`). Wird
derselbe Judge erneut aufgerufen, ersetzt das seinen Eintrag. Ein anderer Judge
kommt als weiterer Eintrag dazu. Gehört eine vorhandene Datei zu einem anderen
Lauf (andere `eval_id`), wird abgebrochen. `report.py` und `export_titles`
lesen nur `.eval` und übergehen die Datei.

Inspects eigenes `inspect score` ist fürs Nachwerten gebaut, schreibt aber
immer ein vollständiges `.eval`. Das ergäbe entweder eine Kopie, die
`report.py` doppelt zählt, oder eine nachträglich geänderte Messdatei.

**Was der Judge bewertet.** Ob ein Muttersprachler den Titel so schreiben
würde. Er antwortet mit einem Wort: **brauchbar** oder **unbrauchbar**. Dazu
gehören Grammatik, natürliche Wortwahl und **ob der Titel in sich stimmig
ist**. Auch ein schlichter, allgemeiner Titel ist brauchbar, aber **kein
Titel** ist unbrauchbar: ein ganzer Satz statt einer Überschrift oder ein
Fragment, das allein nicht als Überschrift taugt. Zwei Klassen statt einer
Note: Gebraucht wird nur die Grenze „unbrauchbar oder nicht“; zwischen 3, 4 und
5 ist schon das menschliche Urteil unsicher, und Judges weichen auf einer Skala
gern nach oben aus. Sprache allein genügt nicht: „… Frostschutz, Vlies und was
der Frost braucht“ ist sprachlich sauber, aber Unsinn. Der Judge sieht nur den
Titel und keinen Seitentext. Länge,
Keyword, Thema und Format prüft der Code exakt. Prompt und Auslesen stehen in
`checks/judge.py`. Das Beispiel im Prompt ist frei erfunden; ein Test sichert
zu, dass es in keinem bewerteten Titel vorkommt. Sonst verriete der Prompt bei
der Kalibrierung die Antwort.

**Was er nicht tut: in `score` eingehen.** `score` bleibt der Leitwert, weil
er sich nachrechnen lässt. Die Zusammenfassung je Judge ist
`summary.unusable_rate`, der Anteil der Titel, die er als unbrauchbar
einstuft. Eine Antwort ohne lesbares Urteil ist `null` und zählt weder als
brauchbar noch als unbrauchbar.
`summary.parsed` sagt, wie oft das vorkam.

**Richter ≠ Kandidat.** Ist der Judge dasselbe Modell wie der Kandidat, wird
der Lauf übersprungen (Rückgabewert 3, damit ein Stapelskript ihn gesondert
aufführen kann).
Ein Modell bewertet eigene Formulierungen zu gut.

**Sampling.** Der Judge bekommt dieselben Laufvorgaben wie jede Aufgabe, aber
keine Sampling-Werte. Wer ihn deterministisch will, stellt das im
llama-swap-Eintrag des Judge-Modells ein (siehe [„Wie ein Lauf eingestellt
ist“](../running.md#wie-ein-lauf-eingestellt-ist)).

**Kalibrierung.** Ob ein Modell als Judge taugt, misst die Aufgabe
`m01_judge`. Sie stellt wörtlich denselben Prompt und vergleicht das Urteil mit
menschlichen Bewertungen. Das geht in drei Schritten:

```bash
# 1. Titel aus allen M01-Logs als CSV, nur was format und german bestanden hat,
#    dedupliziert und gemischt; überschreibt keine vorhandene Datei
uv run python -m german_seo_bench.export_titles logs --out titel.csv

# 2. Spalte human_score von Hand füllen: 1 = unbrauchbar, 2 = vielleicht,
#    3 = brauchbar. Dann nach src/german_seo_bench/data/m01_judge.csv legen
#    (oder unten mit -T data=titel.csv)

# 3. Ein Judge-Kandidat gegen die menschlichen Bewertungen
uv run inspect eval german_seo_bench/m01_judge --model local/gemma4-31b --epochs 3
```

Leitzahl ist „schlecht erkannt“ (`balanced_bad`) = (Fangquote + (1 −
Fehlalarm)) / 2. Die Fangquote ist der Anteil der vom Menschen als unbrauchbar
eingestuften Titel, die der Judge verwirft; der Fehlalarm der Anteil der
brauchbaren, die er verwirft. „Vielleicht“ ist Grauzone und zählt nicht. Wer
nie oder immer verwirft, landet bei 0.5. Daneben stehen Fangquote und
Fehlalarm einzeln, der Anteil verworfener Titel insgesamt (`unusable_rate`,
zum Vergleich mit dem menschlichen Anteil) und der Anteil lesbarer Urteile.
Zeilen ohne Bewertung gehören nicht zum Datensatz. Ein Wert außerhalb von
1 bis 3 oder ein doppelter Titel bricht dagegen ab. Die Aufgabenversion ist
wie bei jeder Aufgabe eine ganze Zahl (`VERSION` in `tasks/m01_judge.py`). Sie
steigt, sobald sich der Judge-Prompt (`JUDGE_VERSION`) oder die Rechnung der
Kalibrierung ändert.

Mit `--epochs 3` urteilt der Judge dreimal je Titel. Verworfen ist ein Titel,
wenn die Mehrheit der lesbaren Urteile „unbrauchbar“ sagt; bei Gleichstand
nicht (Reducer `verdict_share`). Inspects Standard würde den Platzhalter für
„kein Urteil“ mitmitteln. Wie stabil das Urteil ist, zeigt der Vergleich der
Epochen im Log.
