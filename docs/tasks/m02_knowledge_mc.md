# M02 — Deutsches Weltwissen, Multiple Choice

## Was die Aufgabe misst

Wissensfragen aus der Welt, die für SEO im deutschsprachigen Raum und für
dessen Kunden zählt. Die Frage dahinter: Wie gut kennt ein Modell ohne
Websuche diese Welt? Je Frage zehn Antworten zur Auswahl, von denen genau eine
stimmt. Das Modell nennt den Buchstaben; nur der zählt. Eine Frage je Anfrage,
ohne Kontext, kein RAG, keine Suche.

| Thema (`topic`) | Worum es geht |
|---|---|
| `reisen` | Flughäfen (Codes, Namensgeber), Ferienorte, Nationalparks, Fahrgast- und Fluggastrechte, Tickets, Klassifizierung von Unterkünften |
| `shop` | was in Online-Shops verkauft und beschrieben wird: Normen und Kennzeichnung (Papier- und Umschlagformate, Reifen- und Kleidergrößen, Textil- und Pflegeetiketten, Energielabel, Schutzarten, Lampensockel, EAN), Register für Verpackung und Energielabel, Lebensmittelkennzeichnung, Porto, Technik, Materialien |
| `recht` | deutsches und EU-Recht mit E-Commerce-Bezug: Preisangaben, Widerruf und Gewährleistung, Verzug, Steuer- und Aufbewahrungsfristen, Datenschutz, Stichtage (BFSG, GPSR, DSA, AI Act, E-Rechnung), Arbeits- und Mietrecht |
| `at` | Österreich: Begriffe, Institutionen, Recht, Rechnungsformate, Domains |
| `ch` | Schweiz: Institutionen, Volksrechte, Steuern, Zahlungsverkehr, Begriffe |
| `web` | technisches SEO: robots.txt, Sitemaps, hreflang, Statuscodes, Core Web Vitals, Algorithmus-Updates, Domain-Registries |
| `geo_de`, `geo_welt` | Geografie |
| `bildung` | Hochschulen |
| `geschichte`, `natwiss`, `marken` | Geschichte, Naturwissenschaft, Marken und Alltag |

Wo eine Jahreszahl zu leicht zu raten ist, wird das Datum verlangt
(Zollverein, Postleitzahlen, Gründung von Google, Stichtage von EU-Verordnungen).

**Wie die Fragen ausgewählt sind.** Der Benchmark soll auch die besten lokalen
Modelle fordern; eine Frage, die schon kleine Modelle immer lösen, trennt
nicht. Deshalb ist herausgenommen, was qwen3.5-4b und qwen3.5-9b (ohne
Denkmodus) in je drei Läufen ausnahmslos richtig beantwortet haben. Die
Auswahl ist damit gegen diese beiden Modelle gerichtet: Ihre Werte liegen
niedriger, als sie auf einer zufälligen Fragenauswahl lägen. In weiteren
Schritten ist dasselbe mit gemma4-26b-a4b und gemma4-31b geschehen: Was alle
beteiligten Modelle in allen Läufen lösten, ist ebenfalls herausgenommen. Für
den Vergleich anderer Modelle untereinander spielt das keine Rolle.

Nicht herausgenommen ist, was nur die stärkeren Modelle immer lösen. Das
schnitte den Datensatz auf die Lücken genau dieser Modelle zu, und eine andere
Modellfamilie mit anderen Lücken stünde dann unverdient gut da. Schwerer wird
der Datensatz deshalb über neue Fragen, nicht über Streichen.

**Geografie bleibt unter einem Viertel.** Schwere Geografiefragen sind leicht
zu finden — Flussmündungen, Kfz-Kennzeichen, Vorwahlen —, und ein Datensatz,
der nur nach Trennschärfe ausgewählt wird, kippt deshalb dorthin. Gefragt ist
aber die Welt, mit der SEOs und ihre Kunden arbeiten, also auch Normen,
Kennzeichnungspflichten und Handelsrecht. Gruppen, die dasselbe mehrfach
abfragen (Skiorte, Inseln, Nationalparks, IATA-Codes), sind deshalb
ausgedünnt.

Nach dieser Auswahl liegen kaum noch Fragen auf Stufe 1 und 2. `level` bleibt
als Einschätzung des Autors stehen; die gemessene Schwierigkeit zeigt
`itemcheck` (siehe [Aufbau und Methodik](../architecture.md)).

**Warum Multiple Choice.** Freie Antworten lassen sich nur über Wortlisten
werten, und die sind an den Rändern unscharf: „Adenauer war der
erste, Erhard der zweite Bundeskanzler" ist auf die Frage nach dem zweiten
richtig und fällt durch, weil „Adenauer" auf der Liste der falschen Antworten
steht. Umgekehrt kommt eine Antwort mit einer richtigen und einer falschen
Angabe durch, wenn die falsche auf keiner Liste steht. Bei Multiple Choice gibt
es diesen Ermessensspielraum nicht. Wiedererkennen ist leichter als Abrufen;
das gleichen schwerere Fragen und nahe Verwechslungen unter den Optionen aus.
Raten bringt 0.10.

## Wie eine Frage aufgebaut ist

```json
{
  "id": "reisen-np-hainich",
  "input": {
    "question": "In welchem Bundesland liegt der Nationalpark Hainich?",
    "choices": ["Bayern", "Nordrhein-Westfalen", "Niedersachsen", "Baden-Württemberg", "Sachsen-Anhalt",
                "Hessen", "Rheinland-Pfalz", "Brandenburg", "Thüringen", "Sachsen"]
  },
  "reference": {
    "level": 4,
    "topic": "reisen",
    "answer": "Thüringen",
    "source": "https://de.wikipedia.org/wiki/Nationalpark_Hainich"
  }
}
```

Das Modell sieht Frage und Optionen, als `A)` bis `J)` untereinander, und die
Anweisung, nur mit dem Buchstaben zu antworten. `answer` ist der Text der
richtigen Option, nicht ihr Buchstabe; den leitet die Aufgabe aus der
Reihenfolge ab. Ein Buchstabe daneben könnte nach einer Umstellung der Optionen
auf die falsche zeigen.

`topic` ist das Thema und steht zugleich vorn in der ID (`reisen-…`). `source`
ist die Quelle, gegen die Antwort und kritische falsche Optionen geprüft sind.
Neue Fragen bekommen immer eine; bei seltenen Tatsachen reicht das Wissen des
Autors nicht, und eine falsche Referenz ist schlimmer als keine Frage.

**Zehn Optionen, und die falschen sind nah dran.** Unter den neun falschen
stehen absichtlich naheliegende Verwechslungen: Zahlendreher und Nachbarjahre
(1834, 1843, 1833), Nachbarorte und Nebenflüsse (die Singold mündet in die
Wertach, nicht umgekehrt), frühere Hauptstädte (Zomba für Malawi), andere
Bedeutungen derselben Abkürzung (Kommando Spezialkräfte für KSK) und
erfundene, ähnlich klingende Namen (Großer Arbersberg). Wer die Antwort nur
ungefähr kennt, soll nicht durch Ausschließen ans Ziel kommen.

**Keine falsche Option darf vertretbar richtig sein.** Das ist beim Schreiben
neuer Fragen die eigentliche Arbeit. Beispiele für Fallen, die vermieden sind:
Die Weißeritz fließt ebenfalls durch Dresden, „Wohnungseigentümergemeinschaft"
ist ebenfalls eine gängige Bedeutung von WEG, und 5 Jahre ist die
Gewährleistungsfrist für Bauwerke. Keine davon steht unter den Optionen.

**Die Reihenfolge steht fest in der Datendatei.** Gemischt je Lauf wäre sie
eine zweite Zufallsquelle neben dem Sampling, und der schlechteste Lauf je
Frage maße dann auch, wie oft eine Option zufällig an einer ungünstigen Stelle
stand.

**Was der Test prüft** (`uv run pytest`, `validate_item` in
`checks/choice.py`):

- je Frage genau zehn Optionen;
- die richtige Antwort genau einmal darunter;
- keine zwei Optionen, die nach der Faltung gleich sind;
- keine Option, die wie ein Lösungsbuchstabe aussieht;
- keine zeitrelative Frage („aktuell", „derzeit", „nächste");
- die Antwort steht nicht schon in der Frage;
- die ID beginnt mit dem Thema;
- eine Quelle ist eine https-Adresse.

Über den ganzen Datensatz kommt hinzu:

- Jede Frage hat eine Quelle, ausgenommen ein von Hand geprüfter Altbestand, der
  im Test einzeln aufgeführt ist. Die Liste darf nur schrumpfen.
- Jeder Buchstabe ist einmal die Lösung, keiner mehr als doppelt so oft wie im
  Mittel. Sonst punktet ein Modell, das immer denselben Buchstaben nennt, über
  dem Rateniveau.
- Die richtige Option ist höchstens in einem Fünftel der Fragen die längste.
  Sonst punktet „nimm die ausführlichste".

## Wie gewertet wird

Ein Buchstabe zählt, wenn er die Antwort ist oder sie eröffnet:

- `C`
- `C)`
- `**C**`
- `C) Donau`
- `C – Donau`
- `Antwort: C`
- `Die richtige Antwort ist C.`

Steht statt des Buchstabens genau der Text einer Option da (`Donau`), zählt
diese Option. Passen Buchstabe und Text nicht zusammen (`A) Donau`), zählt der
Buchstabe, denn den verlangt die Aufgabe.

Alles andere ist keine Antwort:

- zwei Buchstaben (`A oder B`, `A, B`, `A/B`)
- ein Buchstabe mitten im Satz (`Ich denke, es ist C`)
- eine Begründung ohne Festlegung

Ein Leser, der aus einem Fließtext „die gemeinte" Option herausliest, entschiede
bei unentschlossenen Antworten selbst und schriebe dem Modell seine Deutung gut.

**Zwei Zahlen:**

```
score/mean     Anteil richtig beantworteter Fragen
invalid/mean   Anteil Antworten ohne eindeutigen Buchstaben
```

Beide Fälle zählen null Punkte. `invalid` trennt aber „hat sich nicht
festgelegt" (Formatverhalten) von „hat falsch gewählt" (Wissen). Dazu kommt
`score` nach Stufe (`german_seo_bench/by_level_1…5`) und nach Thema
(`german_seo_bench/by_topic_reisen` usw.). Die Themenspalten zeigen, in
welchem Feld ein Modell Lücken hat.

Rateniveau ist 0.10. Ein Modell um diesen Wert hat nichts gewusst, auch wenn
die Zahl über null liegt.

## Starten

```bash
uv run inspect eval german_seo_bench/m02_knowledge_mc --model local/<modell>
uv run inspect eval german_seo_bench/m02_knowledge_mc -T oracle=true --model none/none
```

Der Oracle-Lauf setzt je Frage den Lösungsbuchstaben ein und muss 1.00 ergeben.
