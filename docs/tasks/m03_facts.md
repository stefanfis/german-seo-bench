# M03 — Fakten-Extraktion aus Produkttexten

## Was die Aufgabe misst

30 deutsche Produkttexte, ein Text je Anfrage. Aus jedem soll ein JSON-Objekt
mit sieben Feldern werden — das häufigste Datenprojekt im SEO-Alltag: Feed,
Schema.org-Auszeichnung, Abgleich gegen das PIM. Ein Modell, das das zuverlässig
kann, ersetzt einen Haufen Regex; eines, das es fast kann, erzeugt stille Fehler
in einer Datenbank.

Gemessen wird nicht „versteht das Modell den Text", sondern ob es die richtigen
Fakten **trennt**. Jeder Text trägt seine Fallen selbst:

```
Streichpreis         "Statt 249,00 € jetzt nur 199,95 €"
Versandkosten        "29,90 €. Versand 4,99 €."
Sternebewertung      "4,6 Sterne bei 312 Bewertungen"
Gewicht in Gramm     "Nur 1.199,00 g leicht"  — bei einem Preis von 1.049,00 €
deutsche Notation    "1.299,00" ist 1299.00, nicht 1.299
Präfix               "Art.-Nr. SW-4471"       -> nur "SW-4471"
fehlende Angabe      kein Hersteller genannt  -> null, nicht geraten
Grundpreis           "5,97 € (100 ml = 1,99 €)"
Ab-Preis             "alle Größen ab 449,00 €" neben dem Preis der gezeigten
zweites Produkt      "Passendes Zubehör: Ersatzkartusche, 12,90 €"
EAN neben SKU        "EAN 4012345678901, Art.-Nr. KS-7741"
Staffelpreis         "Einzelpreis 11,50 €, ab 10 Stück 8,90 €"
Steueranteil         "299,00 € inkl. 19 % MwSt. (Steueranteil 47,74 €)"
Farbe ist Material   "Farbe Titan, Gehäuse aus gebürstetem Aluminium"
Lager ohne Stichwort "Restposten, solange der Vorrat reicht"
```

Die unteren sieben Fallen liegen in den Stufen 4 und 5.

Die Gegenprobe zur Gewichtsfalle steht im Datensatz `aerosafe`: Dort **ist**
1.199,00 € der Preis. Ein Modell, das sich die Regel „vierstellig heißt Gramm"
gebaut hat, fällt genau dort durch.

Zwölf der 30 Texte nennen keinen Hersteller, fünf keine Farbe, fünf kein
Material. `null` ist dort die richtige Antwort und der schwierigere Fall: Das
Modell kann nichts abschreiben, sondern muss eine Lücke erkennen statt zu raten.

**Je Feld eine eigene Vergleichsregel**, und zwar die, die das Feld verdient:

| Feld | Regel | |
|---|---|---|
| `name`, `material` | Enthaltensein als Wort | „Pfanne TitanCook" enthält den Modellnamen |
| `brand`, `color`, `sku` | zeichengleich | streng, mit Absicht — siehe unten |
| `price_eur` | Zahl, ein Cent Toleranz | `true` im Preisfeld zählt nicht als 1,00 € |
| `in_stock` | echtes `true`/`false` | `1` oder `"ja"` zählt nicht |

**Drei Felddefinitionen sind ausdrücklich eindeutig gefasst.** Eine
mehrdeutige Definition mäße die Eindeutigkeit unserer Formulierung und nicht
die Leistung des Modells:

```
sku       zeigt ein Beispiel: "Art.-Nr. SW-4471" -> "SW-4471"
          ("nur die Nummer selbst" wäre als "nur die Ziffern" lesbar —
           aus "UG-1717" würde "1717")
brand     nennt der Text nur EINEN Eigennamen, ist das der Produktname
material  der Werkstoff des Produkts, nicht der einer Beschichtung
```

**Die Zahlen:**

```
score/mean                    Anteil VOLLSTÄNDIG richtiger Datensätze
name/mean, brand/mean, …      je Feld einzeln, damit man sieht, WELCHES Faktum fehlt
parsed/mean                   überhaupt ein JSON-Objekt zu gewinnen
complete/mean                 alle sieben Schlüssel vorhanden
german_seo_bench/by_level_1 … dazu dasselbe je Stufe
```

**`score` ist alles oder nichts**, wie in M01: 1, wenn alle sieben Felder
stimmen, sonst 0. Ein Produktdatensatz mit sechs von sieben richtigen Feldern
geht nicht zu sechs Siebteln in den Feed — er muss angefasst werden, genau wie
einer mit null richtigen Feldern.

Der Anteil richtiger Felder trüge einen hohen Sockel: Einige Felder trifft
fast jedes Modell, das gültiges JSON liefert, und unterschieden würde nur über
die übrigen.

`parsed` und `complete` stehen daneben, weil sie etwas anderes messen als die
Felder: Ein Modell ohne JSON hat sieben Nullen, genau wie eines, das den Text
nicht lesen kann. Ob ein Post-Processing-Schritt hilft, entscheidet sich an
dieser Unterscheidung.

**Der Prompt nennt die Fallen nicht.** Ein Satz wie „Streichpreis,
Versandkosten, Sternebewertung … sind NICHT der Preis" gäbe die schwierige
Hälfte weg: `price_eur` mäße dann, ob ein Modell eine Regel anwenden kann, die
vollständig im Prompt steht. Der Prompt sagt nur, WAS gesucht wird. Das
Unterscheiden ist die Aufgabe.

**Das Format ist eindeutig.** Der Prompt verlangt genau ein JSON-Objekt mit den
sieben Schlüsseln auf oberster Ebene. „Ein einziges JSON-Objekt" allein ließe
`{"products": [{...}]}` zu — formal ein Objekt, aber ohne einen der sieben
Schlüssel oben, also null Punkte für eine richtige Extraktion. Der Scorer packt
solche Hüllen bewusst nicht aus: Das Format bleibt eine Anforderung, und die
Vorgabe dafür steht vollständig im Prompt.

**Die Stufen sind nicht kalibriert.** Je sechs Datensätze auf fünf Stufen,
aber die Einteilung ist nicht belegt — und das steht hier, weil eine Spalte
namens `by_level_…` sonst wie ein Schwierigkeitsprofil gelesen wird. Bei
kleinen Modellen hängt das Einzelergebnis eines Datensatzes schon an kleinen
Prompt-Änderungen; wo es so wenig am Datensatz hängt, trägt keine daraus
abgeleitete Stufe.

Neu geschnitten wird mit `itemcheck`, sobald mindestens drei Modelle gelaufen
sind. Bis dahin ist `score` zusammen mit den sieben Feldspalten der
auswertbare Teil.

**Weitere Bauentscheidungen.** Ein Produkt je Anfrage, kein Stapel — im
Stapel beeinflussen sich die Datensätze gegenseitig, und ein Array falscher
Länge verwürfe mehrere Produkte auf einen Schlag. Englische Feldnamen, weil JSON-Schlüssel hier englisch sind und weil Schema.org `brand`,
`sku` und `color` heißt — der deutsche Text bleibt deutsch, nur das Zielschema
ist englisch, und genau diese Mischung liegt in der Praxis vor. Und ein
fehlender Schlüssel kostet sein Feld statt des ganzen Datensatzes; `complete`
sagt, wie oft der Vertrag überhaupt eingehalten wurde.

Die strenge Zeichengleichheit für `brand`, `color` und `sku` ist mit Absicht
**nicht** gelockert, obwohl „anthrazit" statt „Anthrazit" hart bestraft wird:
Eine Lockerung mit unbekannter Wirkung ließe sich später nicht mehr von der
Modellleistung trennen. Ob Ausfälle von der Schreibweise kommen oder davon,
dass ein Hersteller erfunden wurde, wo keiner genannt war, lässt sich aus den
Logs beantworten — jede Antwort steht dort im Wortlaut.
