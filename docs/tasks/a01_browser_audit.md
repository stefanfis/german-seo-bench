# A01 — Browser-Audit einer JS-Site

```bash
uv run inspect eval german_seo_bench/a01_browser_audit --model anthropic/claude-sonnet-5
```

> **Zum Mitspielen: [`docs/A01.md`](../A01.md).** Dort steht, wie man die
> Prüflandschaft lokal startet (inklusive hosts-Eintrag und Port 80 unter
> Linux, macOS und Windows), wie sie aufgebaut ist, was die nginx-Regeln tun —
> und am Ende, hinter einer Spoilerwarnung, die Auflösung aller 31 Befunde.
> Dieses Dokument erklärt, **warum** die Aufgabe so gebaut ist; das andere
> erklärt einem SEO, **was** es zu finden gibt. `tests/test_a01_doku.py` hält
> jede Zahl und jedes Paar in `docs/A01.md` gegen den Datensatz, damit die
> zweite Stelle nicht lautlos veraltet.

## Was die Aufgabe misst

Das Modell bekommt hier **kein HTML in den Prompt**. Es fährt über MCP einen
echten Chrome und meldet Fehler aus einem geschlossenen Katalog — Dinge, die erst
sichtbar werden, wenn die Seite läuft:

| Was erst der Browser zeigt | Befund |
|---|---|
| DOM nach JavaScript | `title_changed_by_js`, `noindex_after_render`, `content_requires_js`, `canonical_only_in_render`, `h1_added_by_js` |
| Netzwerk-Waterfall | `broken_resource`, `script_loads_script` |
| Navigation | `client_side_redirect`, `redirect_chain` |
| Geometrie und Stabilität | `lazy_lcp_image`, `layout_shift_after_load` |
| Berechneter Stil (`getComputedStyle`) | `hidden_by_css` |
| **Scrollen** | `infinite_scroll_no_pagination` |
| **Klicken** | `interaction_blocked` |
| **Mehrere Seiten zusammen** | `duplicate_title`, `canonical_to_noindex`, `pagination_404`, `orphan_page_in_sitemap` |
| **Andere Kennung anfordern** | `cloaked_content` |
| **Zweiter Aufruf derselben Adresse** | `noindex_on_second_visit` |
| Quelltext (aber man muss ihn ansehen) | `blocking_script_before_css`, `link_without_href` |

Die fett gesetzten Zeilen sind der Grund, warum die Aufgabe einen Agenten
verlangt und nicht nur einen Renderer: Der Bestand auf `scroll.html` erscheint
erst beim Scrollen, die 700-ms-Blockade auf `suche.html` zeigt sich nur, wenn
jemand den Button drückt, die seitenübergreifenden Befunde gibt es auf keiner
einzelnen Seite, und die kontextabhängige Auslieferung sieht nur, wer sich als
Crawler ausgibt. Wer den Browser als Snapshot-Werkzeug benutzt, verliert alle.

Der Zuschnitt in Zahlen: 26 Adressen (sechs davon ohne eigene Datei), 22
Fehlertypen, 31 Befunde, 4 Kontrollseiten ohne Befund, 14 Fehlalarm-Fallen.

**Der Auftrag nennt nur die Startseite** (`START_PAGE`, `home.html`), keine
Seitenliste. So sieht ein echter Auftrag aus: Die Seiten findet der Prüfer
über die Links und über die Sitemap, die `robots.txt` angibt. Jede gewertete
Seite ist auf einem dieser Wege erreichbar
(`test_jede_seite_ist_von_der_startseite_erreichbar`); `presse.html` steht nur
in der Sitemap, und genau das ist der Befund `orphan_page_in_sitemap`. Die
Seitenliste bleibt im Datensatz, für die Wertung und den Bericht.

**Der Auftrag erklärt die Werkzeuge, nicht die Prüfschritte.** Ein Absatz
sagt, was `take_snapshot`, `evaluate_script`, die Netzwerk-Werkzeuge,
`emulate`, `performance_start_trace` und die übrigen leisten — so, wie man
einem Agenten in der Praxis eine Werkzeugübersicht mitgibt. Anlass waren die
Logs der ersten Läufe: `evaluate_script` war bei schwachen wie starken Modellen
das meistgenutzte Werkzeug, vor allem zum Lesen von `<head>` und Links, die der
Snapshot nicht zeigt; `click`, `get_network_request`, die Konsole und der
Performance-Trace kamen kaum vor. Der Absatz nennt keine Bedingung, unter der
eine Seite anders ausgeliefert wird — kein Googlebot, kein Cache, kein zweiter
Aufruf (`test_die_werkzeuge_verraten_keine_falle`). Das herauszufinden, bleibt
Teil der Aufgabe.

**Die Leitfrage beim Bau jedes Items: Was ist die billigste Strategie, die
diesen Punkt holt?** Lautet die Antwort „ein Skript pro Seite", ist das Item
wertlos. Ein mittelgroßes lokales Modell kann sich über `evaluate_script` ein
eigenes Messprogramm in jede Seite schreiben; eine Aufgabe, die so voll lösbar
ist, kann „lokal gegen Frontier" nicht beantworten. Daraus folgen die drei
Bausteine unten: Fehlalarm-Fallen, seitenübergreifende Befunde und Befunde, die
eine besondere Anfrage verlangen.

## Die Fehlalarm-Fallen

Ohne Fallen kostet ein pauschales „meldet alles, was nach Muster aussieht"
nichts, und Precision misst nichts. Jede Falle sieht aus wie ein Befund und ist
keiner. Zwölf davon sitzen auf Seiten, die **auch echte Befunde tragen** — eine
Falle auf einer sonst sauberen Seite bestraft nur den, der die Seite überhaupt
ansieht; die beiden übrigen sind selbst Kontrollseiten:

| Seite | Falle | sieht aus wie |
|---|---|---|
| `sale.html` | Layout-Verschiebung CLS 0,054 (gemessen) | `layout_shift_after_load` (Schwelle 0,1) |
| `liste.html` | Klick-Handler blockiert 150 ms | `interaction_blocked` (Schwelle 200 ms) |
| `poster.html` | lazy geladenes Bild **unterhalb** des ersten Viewports | `lazy_lcp_image` |
| `filter.html` | korrektes `noindex` und korrekter Canonical | zwei Indexierungsfehler |
| `preise.html` | cachebar ohne `Vary`, obwohl die Auslieferung nach Kennung variiert | eine Seite ohne Cloaking — wer nach dem UA-Wechsel aus dem eigenen Cache liest, sieht die Besucher-Fassung |
| `shop.html` | eine einzelne, richtige 301 | `client_side_redirect` / `redirect_chain` |
| `zubehoer.html` | derselbe Textblock mit derselben Klasse wie auf `technik.html`, aber sichtbar | `hidden_by_css` |
| `zubehoer.html` | das nach `width`-Attribut größte Bild (1200 x 600) ist **nicht** lazy — CSS schrumpft es auf 220 x 110 | kein `lazy_lcp_image` finden, obwohl es eines gibt |
| `galerie.html` | der Server setzt ein Cookie und liefert trotzdem immer dieselbe Datei | `noindex_on_second_visit` |
| `presse.html` | `window.location.replace()` in einem Zweig, der nie läuft | `client_side_redirect` |
| `galerie.html` | `document.title =` hinter `matchMedia('print')` | `title_changed_by_js` |
| `kategorie.html` | ein Platzhalterpfad, der 404 liefern würde — über einer leeren Knotenliste | `broken_resource` |
| `produkt.html` | ein `noindex` hinter einem Query-Parameter, den keine Adresse trägt | `noindex_after_render` |
| `scroll.html` | ein Skript-Nachladen hinter einem Feature-Schalter, der aus ist | `script_loads_script` |

Die beiden Zahlenfallen sind so gebaut, dass die Schwelle **rechnerisch** nicht
erreichbar ist, nicht bloß gemessen knapp darunter liegt: Der Einschub auf
`sale.html` ist 88 Pixel hoch, CLS ist Wirkungsanteil mal Wegstrecke, die
Wegstrecke ist 88/1280 = 0,069, und der Wirkungsanteil kann höchstens 1 werden.
Mehr als 0,069 kann dort also auf keiner Maschine herauskommen. Läge die Falle
bei 0,09, entschiede der Zufall eines Render-Laufs, ob dort ein Befund steht.

**Fünf der Fallen sind toter Code — gegen den billigsten Weg.** Ein starkes
Modell übersieht kaum etwas, aber es lässt sich täuschen. Jede dieser Fallen ist
ein Codepfad, der im Quelltext genau wie ein Befund aussieht und zur Laufzeit
nichts tut:

| Seite | im Quelltext steht | zur Laufzeit |
|---|---|---|
| `presse.html` | `window.location.replace('home.html')` | hinter `body.dataset.redaktion === 'aktiv'` — das Attribut gibt es nicht |
| `galerie.html` | `document.title = …` | hinter `matchMedia('print')` — beim Rendern nie wahr |
| `kategorie.html` | ein Bildpfad `/img/…`, der 404 liefern würde | über `querySelectorAll('img.motiv')` — die Liste ist leer |
| `produkt.html` | `content = 'noindex, follow'` | hinter `?vorschau=1` — keine Adresse der Aufgabe trägt einen Parameter |
| `scroll.html` | ein `script`-Element mit `src` | hinter `{ aktiv: false }`, und die Datei liegt nicht in `site/` |

Wer das gerenderte DOM und das Netzwerkprotokoll ansieht, sieht nichts und
meldet nichts. Wer den Quelltext nach Mustern durchsucht, sammelt fünf
Falschmeldungen. Ein Modell, das den Quelltext nicht *statt* zu messen liest,
sondern *zusätzlich*, und jede Vermutung nachprüft, tappt in keine davon —
genau das sollen die Fallen belohnen. Bei drei der fünf ist der Nachweis „kann
nicht greifen" im Quelltext selbst zu führen; die Tests führen ihn.

## Die seitenübergreifenden Befunde

Sie treffen Planung statt Skript. Ein Harness, das Seite für Seite läuft, findet
sie strukturell nicht — egal, wie gut es die einzelne Seite vermisst:

```
duplicate_title          poster.html und poster-a3.html tragen denselben <title>
canonical_to_noindex     sale.html zeigt auf filter.html, und die trägt noindex
pagination_404           liste.html und liste-2.html verlinken Seite 3 — die 404 liefert
orphan_page_in_sitemap   presse.html steht in der sitemap.xml und wird nirgends verlinkt
redirect_chain           umzug.html → umzug-zwischen.html → kategorie.html
```

## Befunde, die eine besondere Anfrage verlangen

**`cloaked_content` trifft die Skript-Strategie direkt** — ohne eine legitime
Technik zu bestrafen. Der Server liefert Suchmaschinen anderes HTML als
Besuchern (zwei Zeilen in `testsite.nginx.conf`, der SEO-Klassiker). `fetch()`
**kann** den User-Agent nicht setzen: Er steht in der Fetch-API auf der Liste
der verbotenen Header. Ein Modell, das sein Audit über `fetch(location.href)`
baut, kann diesen Befund also nicht finden — das ist keine Entscheidung dieser
Aufgabe, sondern eine Eigenschaft der Plattform. Über die Devtools-Schiene geht
es: `emulate` nimmt `userAgent`. Das Item **belohnt** den richtigen Weg, statt
den falschen zu sabotieren, und verlangt genau die Gewohnheit, die einen SEO
ausmacht: nicht ansehen, was man selbst bekommt, sondern was der Crawler
bekommt.

Es gibt zwei gecloakte Adressen mit verschiedener Cache-Vorgabe. `schulen.html`
sendet `no-store`; `preise.html` ist die **Cache-Falle**: kein
`Vary: User-Agent`. Wer sie erst als Besucher holt und danach als Crawler,
bekommt aus dem Browser-Cache noch einmal die Besucher-Fassung — die zweite
Anfrage erreicht den Server nie. Das ist kein Messartefakt, sondern der Fehler,
den ein technischer SEO in der Praxis macht. Damit die Falle nicht am Zufall
hängt, steht dort eine **feste** Frist (`max-age=7200`, so lang, wie ein Lauf
dauern darf) statt der heuristischen Frische, die der Browser sonst aus dem
Dateialter errechnet. Zu lesen ist das Ergebnis über zwei Spalten:

```
ua_switched 0                     kam nicht auf die Idee
ua_switched 1, cache_bypassed 0   hat es versucht und den eigenen Cache erwischt
ua_switched 1, cache_bypassed 1   hat richtig geprüft
```

Ausdrücklich nicht gebaut ist eine `Sec-Fetch`-Variante (der Server antwortet auf
`fetch` anders als auf eine Navigation). Sie bestrafte dieselbe Technik stumm —
falsche Befunde ohne Fehlermeldung —, und in der Tabelle sähe das aus wie ein
Modell, das schlecht analysiert.

**`noindex_on_second_visit` — der Zweitbesuch.** `tipps.html` und `archiv.html`
setzen beim ersten Abruf ein Cookie und liefern beim zweiten dieselbe Adresse mit
`noindex` aus. Die Regel steht in `testsite.nginx.conf` und damit in keiner
Datei, die ein Agent zu sehen bekommt; wer jede Adresse genau einmal anfasst —
und das tut jede `fetch`-Schleife —, kann den Befund nicht haben. Die
Cache-Vorgabe ist hier genau umgekehrt zur Cache-Falle: `no-store`, weil sonst
der Cache die Bedingung ersetzte und wieder nur die Abrufreihenfolge gemessen
würde.

Jede der beiden Adressen hat einen eigenen Cookie-Namen. Das ist keine Kosmetik:
Mit demselben Namen bekäme `tipps.html` schon beim ersten Aufruf die
Wiederkehrer-Fassung, sobald jemand `archiv.html` zuerst ansieht — der Befund
hinge dann an der Abrufreihenfolge statt an der Auslieferung.
`test_der_zweitbesuch_haengt_am_cookie_und_nicht_am_cache` hält das fest.

Eine Wechselwirkung, die man beim Lesen der Zahlen kennen muss: Wer Kennung
**und** Besuchszahl zugleich ändert, sieht anderes HTML und kann nicht sagen,
woran es lag — ein zusätzlich gemeldetes `cloaked_content` auf `tipps.html` ist
die Folge. Inhaltlich ist das fair (Variablen einzeln zu prüfen ist die
Analysearbeit), aber die Falschmeldung ist eine Eigenschaft der Prüflandschaft
und keine Modellschwäche.

Sechs Adressen der Prüflandschaft haben **keine Datei**: `preise.html`,
`schulen.html`, `tipps.html` und `archiv.html` werden in je zwei Fassungen
ausgeliefert (`-visitor` / `-bot` nach Kennung, `-first` / `-again` nach
Besuchszahl), `umzug.html` und `shop.html` sind Weiterleitungen. Was dort
passiert, entscheidet allein die nginx-Konfiguration — und
`tests/test_a01_browser_audit.py` hält fest, dass dort auch wirklich keine Datei
liegt: Eine Datei `preise.html` würde bei einem Tippfehler in der Konfiguration
ausgeliefert, und das Cloaking verschwände, ohne dass eine Zahl es verrät.

## Befunde, die erst das Rendern zeigt

Zwei Items folgen derselben Regel: Die Wahrheit steht nicht in einer
ausgelieferten Datei, sondern erst im berechneten Ergebnis.

`hidden_by_css` auf `technik.html` ist der SEO-Klassiker „Hidden Text" — ein
Textblock im DOM, den ein Besucher nicht sieht. Die Unsichtbarkeit steht in
keiner Datei, sondern entsteht aus drei Teilen:

```
style.css    .rand { visibility: hidden }        Container ausgeblendet
             .rand > * { visibility: visible }   Kinder holen sich die Sichtbarkeit zurück
layout.css   @media (min-width: 68.75em)         greift bei 1280 px
               .rand > .einschub { visibility: inherit }
                                                 der Textblock erbt wieder die Ausblendung
zubehoer.html  <div class="rand offen">          dieselbe Klasse, sichtbar
```

Jede Zeile für sich ist harmlos, und `zubehoer.html` trägt **dieselbe** Klasse
am selben Element. Wer aus dem Markup oder aus einer einzelnen CSS-Regel
schließt, meldet entweder beide Seiten (eine Falschmeldung) oder keine. Nur
`getComputedStyle` unterscheidet sie. Nebenbei ist das Item
viewport-abhängig: unter 1100 Pixeln ist der Block auf beiden Seiten sichtbar.

Der zweite `lazy_lcp_image`-Befund (`zubehoer.html`) dreht die Attribut-Heuristik
um. Zwei große Bilder im ersten Viewport, und welches der LCP-Kandidat ist,
entscheidet die **gerenderte** Größe:

| Bild | Attribute | per CSS gerendert | `loading` |
|---|---|---|---|
| `hero.svg` | 1200 x 600 | 220 x 110 (24 200 px²) | — |
| `foto.svg` | 300 x 200 | 900 x 600 (540 000 px²) | `lazy` |

Nach Attribut ist das große Bild nicht lazy — eine Attribut-Heuristik findet
nichts. Nach Geometrie ist das lazy geladene der Kandidat, und die
Mindestfläche von 40 000 px² schließt das geschrumpfte Bild aus. Das ist die
Gegenrichtung zur Falle auf `poster.html`, wo ein lazy geladenes Bild richtig
eingesetzt ist.

Mehrfach vorkommende Befunde sind Absicht: `noindex_after_render`,
`pagination_404`, `duplicate_title`, `lazy_lcp_image` und weitere kommen je
**zweimal** vor — wer je Katalogeintrag nach dem ersten Treffer aufhört,
verliert Recall. Und vier Seiten tragen gar keinen Befund, `ratgeber.html`
davon mit allen Warnsignalen: lazy geladene Bilder (aber unterhalb des Folds,
also richtig eingesetzt), einen Button (aber mit schnellem Handler), ein Skript
(aber `defer` und nach dem CSS). Wer Muster statt Zustände meldet, sammelt dort
Falschmeldungen.

## Die Fähigkeitsklassen

Jeder Fehlertyp trägt eine Klasse, und sie benennt die **billigste bekannte**
Strategie, nicht das Thema des Items:

| Klasse | genügt zum Finden | Befunde |
|---|---|---|
| `source` | die ausgelieferten Bytes lesen (HTML, JS, CSS, sitemap.xml) | 18 |
| `network` | die Antwortdaten statt des Rumpfs (Status, Weiterleitungskette) | 5 |
| `render` | gerendertes DOM, Geometrie, Layoutmessung | 4 |
| `request` | eine Anfrage unter besonderer Bedingung (Kennung, Cookie, Cache-Umgehung) | 2 |
| `second_load` | Zustand über zwei Ladevorgänge | 2 |

Deshalb steht `interaction_blocked` auf `source`, obwohl es „Interaktion" heißt:
Der Mechanismus steht im Skript, und wer JavaScript liest, braucht den Klick
nicht.

Zwei Modelle können denselben Gesamtwert auf völlig verschiedene Weise
erreichen — das eine bedient die Seiten (navigiert, klickt, scrollt), das
andere liest sie in einer `fetch`-Schleife und rendert nur für Geometrie. Die
achtzehn `source`-Befunde bekommt jeder geschenkt, der eine `fetch`-Schleife
über die gefundenen Seiten schreiben kann; für starke Kandidaten entscheiden die
wenigen Befunde in `render`, `request` und `second_load`. Der Scorer weist
deshalb `recall_<klasse>` einzeln aus.

**Die Klasse steht nicht im Prompt.** Sie liegt unter `reference`, nicht unter
`input`; stünde sie im Katalog, verriete die Aufgabe den Weg, den sie messen
will (`test_die_klasse_steht_nicht_im_prompt`).

## Die Wertung

`score` ist das **F1 je Fähigkeitsklasse, gemittelt über die belegten Klassen**
(`capability_f1` in `tasks/a01_browser_audit.py`). Damit wiegt jede Fähigkeit
gleich viel statt so viel wie ihr Anteil an den Paaren.

Warum nicht alles-oder-nichts wie in M01, M03 und M10: Dort ist ein Datensatz
eine Frage mit einer Antwort, hier ist er ein Auftrag über 26 Seiten mit 31
Befunden. Alles-oder-nichts wäre eine Münze und unterschiede fünfundzwanzig
gefundene Befunde nicht von keinem.

Warum nicht Micro-F1 über alle Paare: Die Verteilung über die Klassen ist
extrem schief (18 von 31 Befunden sind `source`). In Micro-F1 verschwände das
Verfehlen einer ganzen Fähigkeit im Rauschen. Vier Löser über dieselbe
Referenz, gerechnet im Oracle-Lauf:

| Löser | Score | Micro-F1 |
|---|---|---|
| alles korrekt | 1.000 | 1.000 |
| alles melden (jede Seite × jeder Fehlertyp) | 0.120 | 0.103 |
| alle Klassen außer `request` und `second_load` | **0.600** | 0.931 |
| nur `source` | **0.200** | 0.735 |

Die letzte Zeile ist der Punkt: Ein reiner Quelltextleser stünde in Micro-F1
bei 0.735. Micro-F1 steht trotzdem als Diagnose in `Score.metadata.micro_f1`,
weil ein großer Abstand zwischen beiden Zahlen genau die ungleiche Verteilung
über die Fähigkeiten anzeigt, um die es hier geht.

Zwei Feinheiten, die beide nötig sind. **Falschmeldungen zählen gegen die Klasse
des gemeldeten Fehlertyps** — sonst wäre der Score ein reines Recall-Maß, in dem
ein Agent, der alles meldet, eine Eins bekäme (Zeile zwei der Tabelle prüft
das). Und **eine Klasse ohne Befunde zählt nicht mit**, sie steht als `None` in
der Aufschlüsselung: Als 0.0 gezählt, drückte sie den Score auf einem Ausschnitt
der Aufgabe auch dann, wenn der Agent alles gefunden hat, was dort zu finden
war.

### Gewertet wird die Befundliste

Seit Aufgabenversion 2 führt der Agent seine Befunde laufend in der Datei
`/root/OUT.jsonl` im Agent-Container, ein JSON-Objekt je Zeile (JSON Lines).
Ob er Zeilen anhängt oder die Datei neu schreibt, ist ihm überlassen: goose
hängt per Shell bequem an, `react` hat nur `text_editor`, und der schreibt eine
Datei leicht ganz, hängt aber nicht an. Am Ende gibt der Agent alle Befunde
zusätzlich als JSON-Array in der Schlussantwort ab. Gewertet wird
(`choose_answer`):

1. die Datei, wenn sie mindestens einen Befund als JSON-Objekt enthält,
2. sonst die Schlussantwort.

Jede Zeile wird für sich gelesen (`findings_from_lines`). Eine kaputte Zeile,
etwa die halb geschriebene letzte eines abgebrochenen Laufs, kostet nur sich
selbst: Sie erscheint als verworfene Meldung. Doppelte Zeilen zählen einmal,
die Wertung arbeitet auf einer Menge. Toleriert werden wie bei der
Schlussantwort auch die Verpackungen, die nicht gemessen werden sollen: ein
Komma am Zeilenende, ein JSON-Array statt Zeilen.

Damit zählt bei einem vorzeitigen Ende — Zeitgrenze, voller Kontext, ein
abgestürztes CLI —, was bis dahin feststand. In Version 1 zählte ein solcher
Lauf null, auch wenn der Agent die Hälfte der Befunde schon gesehen hatte.

Die Datei kommt vor der Schlussantwort, weil ein Agent-CLI auf stdout auch
Bruchstücke seiner Werkzeugaufrufe ausgibt und das erste JSON darin nicht
zwingend die Liste ist. Eine leere Datei oder eine ohne ein einziges gültiges
Objekt fällt auf die Schlussantwort zurück.

Im Log steht die Herkunft in `Score.metadata.answer_source` (`findings_file`
oder `final_answer`). Ist zusätzlich `final_answer_error` gesetzt, war die
Schlussantwort nicht auswertbar, und gewertet wurde ein Zwischenstand. Der
Kundenbericht vermerkt das im Bewertungskasten.

Ein Lauf, der mit einem Fehler abbricht (etwa eine Modellanfrage, die der
Server endgültig ablehnt), bekommt weiterhin keinen Score: Inspect wertet
einen Datensatz mit Fehler gar nicht. Teilwertung gibt es also für Zeit- und
Nachrichtengrenzen und für ein CLI, das sich selbst beendet.

## Was die Aufgabe ausgibt

```
score/mean          F1 je Fähigkeitsklasse, gemittelt — die eine Kennzahl
precision/mean      von den gemeldeten Paaren war so viel richtig
recall/mean         von den vorhandenen Paaren hat es so viel gefunden
recall_<klasse>     Recall je Fähigkeitsklasse
clicked/mean        hat der Agent geklickt?
scrolled/mean       hat er zu scrollen versucht?
ua_switched/mean    hat er sich je als Crawler ausgegeben?
cache_bypassed/mean hat er unvermittelt erneut geholt?
coverage/mean       wie viele der genannten Seiten hat er abgerufen?
```

Alles neben `score` ist Diagnose. Precision und Recall stehen einzeln daneben,
weil F1 die beiden Fehlerarten verrechnet: 0.6 aus lauter Falschmeldungen und
0.6 aus lauter Lücken sind verschiedene Befunde, und nur der erste ist durch
einen strengeren Prompt zu heilen. Je Lauf steht dazu im Log, **welche**
Fehlerklasse gefehlt hat (`Score.metadata.missed` / `.false`).

**`coverage` zuerst lesen.** Sie sagt, wie viele der genannten Seiten der Agent
überhaupt abgerufen hat — und trennt damit zwei Nullen, die `recall`
verrechnet: „geprüft und nichts gefunden" und „nie hingesehen". Ein Modell, das
eine Seite prüft und danach abgibt, sieht ohne diese Spalte aus wie eines, das
an der Aufgabe scheitert. Welche Seiten fehlen, steht namentlich in
`Score.metadata.pages_missed`.

Gezählt wird der Abruf, nicht das Werkzeug: die Zieladresse einer Navigation,
eine vollständige URL irgendwo in den Argumenten (so steht sie in einem `fetch`
und in einem `curl` des Shell-Werkzeugs), oder ein `fetch` mit relativem Ziel.
Die bloße Erwähnung einer Adresse zählt nicht — ein Skript, das Links
vergleicht, nennt Seiten, ohne sie anzusehen.

**`clicked`, `scrolled` und `ua_switched` sind kein Leistungsmaß, sondern
Verhalten.** Genau diese drei Fähigkeiten trennen hier einen Agenten von einem
Renderer: `interaction_blocked` braucht einen Klick (oder das Lesen des
Skripts), `infinite_scroll_no_pagination` braucht Scrollen, `cloaked_content`
braucht eine andere Kennung. Stehen sie auf 0 und `recall` ist niedrig, sind
diese Befunde nicht verpasst, sondern **nie gesucht** — und das ist eine andere
Aussage. Der vollständige Werkzeugverlauf steht als Histogramm in
`Score.metadata.tool_calls`.

Die Spalten messen die **Handlung, egal auf welchem Weg**, und nicht die
Benutzung eines bestimmten Werkzeugs. Ein Modell kann A01 lösen, ohne ein
einziges Mal das Werkzeug `click` zu benutzen — indem es sich in der Seite ein
eigenes Audit-Programm schreibt (`fetch` plus `DOMParser` für Quelltext gegen
DOM, `getBoundingClientRect()` für den LCP-Kandidaten, ein eigener
`PerformanceObserver` für CLS, eine `scrollTo`-Schleife, ein `btn.click()` mit
eigener Zeitmessung). Eine Spalte, die Werkzeugnamen zählt, hielte so ein Modell
für passiv.

Erkannt wird der Versuch, nicht die Wirkung: `.click(` bzw. `MouseEvent('click'`,
und `scrollTo`/`scrollBy`/`scrollIntoView`/`scrollTop =` oder eine
weiterblätternde Taste. Bewusst nicht erkannt: das bloße Lesen von `scrollHeight`
oder eines `onclick`-Attributs — das ist Inspektion, keine Handlung.

## Die Messbedingungen

**Die Topologie steht in `compose.yaml`, nicht im Python-Code** — nur so kann man
sie nachlesen statt sie zu glauben:

```
default    Chrome + chrome-devtools-mcp. Kein Mount aus dem Projekt.
testsite   nginx, liefert `site/` aus — read-only gemountet.
           Netzwerk-Alias `www.example.net`: unter DIESEM Namen sieht der
           Agent die Site, und nur der steht im Prompt.
networks   `internal: true` — kein Weg nach draußen.
```

Referenz, Scorer und Aufgabencode liegen in keinem dieser Container. Ein Agent,
der die Seiten nicht rendern kann, kann auch nicht ausweichen und etwas
nachschlagen. `tests/test_a01_browser_audit.py` prüft diese Zusicherungen an der
Datei.

**Der Viewport ist Messbedingung, nicht Geschmack.** `layout_shift_after_load`
und `lazy_lcp_image` hängen an der Fenstergröße — dasselbe Bild liegt bei
1280x900 im ersten sichtbaren Bereich und bei 375x667 nicht, und derselbe
CLS-Wert kann je nach Viewport unter oder über der Schwelle liegen. Deshalb
stellt der **MCP-Server** den Viewport (`--viewport`), und der Prompt nennt ihn
zusätzlich. Die Angabe im Prompt allein wäre eine Bitte; der Schalter ist die
Bedingung.

**Die Domain ist `www.example.net`.** `example.net` und `example.com` sind nach
RFC 2606 für genau diesen Zweck reserviert und lösen nirgends auf einen echten
Host auf — ein erfundener Name stünde im Kundenbericht und sähe aus wie die
Domain eines echten Auftraggebers. Zwei verschiedene davon bilden außerdem den
Unterschied ab, an dem die Canonical-Befunde hängen:

```
www.example.net   die geprüfte Site
example.com       die fremde Domain (die Canonicals, die hinauszeigen)
```

Im Containernetz trägt der Dienst den Namen als **Netzwerk-Alias**; `.local`
wäre die schlechtere Wahl, weil es auf macOS mit mDNS kollidiert.

**Nichts, was ausgeliefert wird, darf den Benchmark verraten — auch kein
Kommentar.** Ein Agent liest auch Kommentare in `sitemap.xml`, HTML und CSS und
zitiert sie in seiner Überlegung. Die Regel „Kommentare begründen die
Entscheidung" gilt für Benchmark-Code; `site/` ist kein Code, sondern
Messgegenstand. `test_keine_ausgelieferte_datei_verraet_den_benchmark` liest
deshalb **jede** Datei unter `site/` — HTML, XML, CSS, JS — und verlangt, dass
darin weder eine Katalog-Id noch das Vokabular des Benchmarks vorkommt.

**Kein Befund darf aus dem Seitentext ablesbar sein.** Ein Satz wie „Weitere
Motive werden beim Scrollen geladen." machte `infinite_scroll_no_pagination` aus
einem Snapshot erschließbar, ohne zu scrollen.
`test_keine_seite_kuendigt_ihren_befund_an` hält das fest: Der Mechanismus
gehört ins Markup und ins Skript, die Ankündigung in keinen sichtbaren Text.

**Der Benchmark pflegt keinen Kontext.** Ein Agent schickt bei jedem Zug das
ganze Gespräch mit: jeden Browser-Snapshot, jede Werkzeugantwort und jedes
Messskript, das er sich selbst geschrieben hat. In Testläufen der Version 1 lag
der Median der Promptlänge bei 30.000 bis 53.000 Tokens, die Spitzen reichten
bis knapp 88.000. Wie ein Agent damit umgeht, ist Teil der Messung und nicht
Sache des Benchmarks:

- Im **`react`-Pfad** misst A01 das Modell. Inspect verdichtet und kürzt dort
  nichts. Sparsam abzufragen und die Befunde in die Datei zu schreiben, ist die
  Kontextpflege, die ein Modell hier leisten kann.
- Bei einem **Agent-CLI** gehört dessen eigene Kontextpflege zu dem, was die
  Achse vergleicht. goose und pi verdichten selbst, nach ihren eigenen Regeln.

Inspects Compaction in der Bridge wäre dort nicht nur ein Eingriff in den
Prüfling, sondern schlicht falsch. Sie führt einen einzigen Verlauf und hängt
jede neue Nachricht daran an, auch die einer Nebenanfrage. goose fragt
nebenher nach einem Titel für die Sitzung. Im ersten Lauf von Version 2 stand
diese Anweisung danach in jeder Anfrage des Audits, und das Modell lieferte am
Ende einen Titel statt der Befunde.

**Das Fenster wird trotzdem ermittelt** (`plan_context`, einmal zu Beginn des
Agenten). Es ist eine Laufbedingung, und ein CLI braucht es: Für den
Modellnamen `inspect` kennt goose kein Fenster und rechnet mit 128.000 Tokens.
Damit wäre es in jedes 64k-Fenster gelaufen, bevor es verdichtet. Genau daran
ist ein Lauf von Version 1 gescheitert. goose bekommt deshalb `input_budget`
als `GOOSE_CONTEXT_LIMIT`, eine Angabe zum Aufbau wie die Serveradresse. pi
bekommt das ganze Fenster als `contextWindow` und verdichtet ab
`contextWindow - reserveTokens`; seine Reserve ist in der Vorgabe 16.384 Tokens,
die Schwelle also ebenfalls `input_budget`. Eine Schwelle setzt der Benchmark
nicht, beide verdichten nach ihrer Vorgabe.

```
Fenster              65.536   131.072   was das Modell je Anfrage hat
- ANSWER_MAX_TOKENS  16.384    16.384   Reserve für die Antwort, je Anfrage
= input_budget       49.152   114.688   so lang darf eine Anfrage höchstens sein
```

Woher das Fenster kommt:

1. **Lokale Server** (`local/…`, `openai-api/…`): `n_ctx` aus `/props` von
   llama-server, also das Fenster je Slot. Bei llama-swap gilt der Wert nur,
   wenn das Modell in `/running` steht; sonst könnten die Props vom vorher
   geladenen Modell stammen. Die Abfrage unter `/upstream/<alias>/props` lädt
   das Modell und wartet darauf bis zu 10 Minuten (`LOAD_TIMEOUT_SECONDS`).
   Die erste Modellanfrage hätte genauso lange gewartet. Inspects Modelldaten
   zählen hier ausdrücklich nicht: Sie kennen das Fenster des Modells
   (Qwen 3.5 9B: 262.144), nicht das, mit dem der Server gestartet wurde.
2. **Cloud-Modelle**: Inspects Modelldaten (Claude Haiku 5.5: 1.000.000).
3. **Sonst** 65.536, das Minimum.

Im Log steht das Ergebnis unter `Score.metadata.context` (`window`, `source`
mit `server`, `model_info` oder `assumed`, dazu `input_budget`) und in der
Begründung. Bei `assumed` nennt `note` den Grund, etwa eine Zeitüberschreitung
der Abfrage. Ein Fenster unter 65.536 wird gemessen, steht aber als Störung im
Bewertungskasten des Berichts.

Zwei Läufe desselben Modells mit verschiedenen Fenstern sind damit zwei
verschiedene Bedingungen, so wie zwei Quantisierungen. Wer vergleicht, liest
das Fenster mit.

**Wenn es überläuft.** Dann meldet der Server den Fehler, und der
`local/`-Provider übersetzt ihn (llama.cpp schreibt
`exceed_context_size_error`, Inspect erkennt nur OpenAIs
`context_length_exceeded`). `react()` beendet den Agenten dann sauber, und
gewertet wird die Befundliste. Bei goose geht der Fehler an das CLI zurück, und
was es daraus macht, gehört zur Messung. Wie oft das passiert ist, steht in
`Score.metadata.context_overflows` und im Bewertungskasten des Berichts.

**Ein weiteres Gerüst anbinden** heißt damit dreierlei:

- die Modellanfragen über `sandbox_agent_bridge(state)` leiten, ohne
  `compaction`,
- dem Gerüst mit `budget = await plan_context()` das Fenster
  `budget.input_budget` nennen, in seiner eigenen Schreibweise, falls es das
  nicht selbst herausfindet,
- ihm ein Dateiwerkzeug geben, damit es die Befundliste führen kann.

Wie es seinen Kontext pflegt, bleibt seine Sache.

## Woher die Referenz kommt

Die 31 Paare sind nicht getippt, sondern gerechnet:

```bash
uv run python -m german_seo_bench.reference                      # rendern und vergleichen
uv run python -m german_seo_bench.reference --capture c.json     # vorhandenes Capture, kein Docker
uv run python -m german_seo_bench.reference --write              # Abweichung übernehmen
```

Das Capture landet ohne `--out` unter `~/.cache/german-seo-bench/`, nicht im
Temp-Verzeichnis des Systems: Das liegt unter macOS in `/var/folders`, und nicht
jede Docker-Umgebung gibt diesen Pfad an Container frei.

Der Lauf fährt einen eigenen Container (`reference.compose.yaml`, Puppeteer
direkt statt chrome-devtools-mcp) gegen **dieselbe** testsite mit derselben
nginx-Konfiguration, die auch der Agent sieht, friert das Ergebnis als Capture
ein und lässt `reference/audit.py` darüber laufen. Was der findet, IST die
Referenz.

Zwei Entscheidungen dabei sind wichtiger als der Code:

- **Vergleichen ist die Vorgabe, Schreiben der Ausnahmefall.** Ein Werkzeug,
  das die Referenz bei jedem Lauf stillschweigend überschreibt, macht jede
  Abweichung zur Nebensache — und eine veraltete Referenz sieht im Oracle-Lauf
  grün aus, weil der sie gegen sich selbst prüft.
- **Der Auditor rendert mit Puppeteer, nicht über chrome-devtools-mcp.** Ein
  Fehler im Werkzeugpfad des Agenten darf sich nicht in der Referenz
  wiederholen — sonst hebt er beide Seiten um denselben Betrag und wird
  unsichtbar.

Eine Falle, die knapp unter einer Schwelle liegen soll, ist nur dann „kein
Befund", wenn jemand nachrechnet. Zusätzlich und unabhängig davon weist
`tests/test_a01_browser_audit.py` für jeden Befund, der im Quelltext
entscheidbar ist, den Mechanismus nach, der ihn erzeugt — für die
seitenübergreifenden Befunde ebenso wie für die vierzehn Fallen und für die
dreiteilige Kaskade hinter `hidden_by_css`. Fasst jemand `site/` an, ohne die
Referenz nachzuziehen, schlägt das an, ohne dass ein Container laufen muss.

## Über welches Gerüst — `react`, `goose` oder `pi`?

Das ist die zweite Messachse der Aufgabe, und sie beantwortet eine andere Frage
als die erste:

```bash
uv run inspect eval german_seo_bench/a01_browser_audit                       # react (Vorgabe)
uv run inspect eval german_seo_bench/a01_browser_audit -T agent=goose        # Agent-CLI goose im Container
uv run inspect eval german_seo_bench/a01_browser_audit -T agent=pi           # Agent-CLI pi im Container
```

| | `react` | `goose` | `pi` |
|---|---|---|---|
| misst | Modellfähigkeit an einem neutralen Werkzeug-Loop | „welches Agentic Framework taugt für SEO?" | ebenso |
| Werkzeuge | chrome-devtools-mcp, direkt von Inspect verdrahtet, dazu `text_editor` für die Befundliste | dieselben, plus die eingebauten Datei- und Shell-Werkzeuge von goose | dieselben, aber über `codemode` (siehe unten), plus `read`, `bash`, `edit`, `write` |
| Antwort | Befundliste `/root/OUT.jsonl`, ersatzweise `submit` | Befundliste `/root/OUT.jsonl`, ersatzweise stdout | ebenso (`--print`) |
| Kontextpflege | keine: Das Modell muss mit seinem Fenster auskommen | goose selbst; es erfährt `input_budget` als `GOOSE_CONTEXT_LIMIT` | pi selbst; es erfährt das Fenster als `contextWindow` in `models.json` |
| Grenze | `MESSAGE_LIMIT` (Nachrichten) | `--max-turns` | keine eigene |
| Zeit | `TIME_LIMIT` (120 Min. je Datensatz), `MODEL_CALL_TIMEOUT` (1800 s je Modellanfrage) | `TIME_LIMIT`, ohne eigene Grenze für das CLI | ebenso |
| Antwortlänge | `ANSWER_MAX_TOKENS` (16384 Tokens je Modellanfrage) | dieselbe: Inspect setzt sie auch für die Anfragen über die Bridge | ebenso |
| Modellanfragen | Inspect stellt sie | das CLI stellt sie, über den Bridge-Proxy im Container | ebenso |

**pi läuft so, wie ein typischer pi-Nutzer es einrichtet.** pi gibt
MCP-Werkzeuge in seiner Vorgabe nicht einzeln an das Modell weiter, sondern über
das Werkzeug `codemode`: Das Modell schreibt JavaScript, das die
Browser-Werkzeuge als `tools.mcp__chrome_devtools__click({…})` aufruft. Der
Benchmark lässt das so. Gemessen wird das Konzept des Frameworks, nicht ein
nachgebauter Werkzeugkasten. Der Browser ist trotzdem derselbe: `mcp.json`
startet chrome-devtools-mcp mit `MCP_ARGS`. Die Konfiguration schreibt der
Agent vor dem Start nach `/root/.pi/agent/` (`pi_config`); Versionsprüfung,
Katalogabgleich und Telemetrie sind abgeschaltet, Kontextdateien, Skills und
Vorlagen ebenso. Erweiterungen bleiben an, denn MCP und `codemode` sind
eingebaute Erweiterungen von pi.

Prompt, Wertung, Referenz, Browser und Viewport sind in allen Fällen dieselben
— die einzige geänderte Größe ist das Framework, sonst wäre die Differenz nicht
zuzuordnen. Deshalb gibt jedes Gerüst seine Antwort auf demselben Weg ab, über
die Befundliste: Ein CLI hat kein `submit`-Werkzeug, und der `react()`-Loop
bekommt dafür `text_editor`. Eine Shell bekommt er nicht; der react-Pfad misst
das Modell am Browser, nicht an curl.

Technisch hängt der CLI-Pfad an `sandbox_agent_bridge`: Inspect startet **im**
Container einen Proxy (Port 13131), der die OpenAI-API spricht und an den
Modell-Provider des Laufs weiterreicht. goose bekommt
`OPENAI_HOST=http://localhost:13131` und den Modellnamen `inspect`, pi
denselben Endpunkt als Anbieter `inspect` in `models.json`. Damit gilt
dreierlei: Jede Modellanfrage des CLI steht im Inspect-Log (Nachrichten,
Werkzeuge, Tokens), `local/<alias>` funktioniert unverändert, und das
`internal: true`-Netz bleibt — der Proxy läuft im Container, es braucht keinen
Weg nach draußen.

Was man beim Lesen eines CLI-Laufs wissen muss:

- **Die Werkzeuge heißen anders.** Über das CLI heißen sie
  `chrome-devtools__click`, über `react()` schlicht `click`. `clicked`,
  `scrolled` und `ua_switched` vergleichen deshalb den letzten Namensteil —
  sonst wären die Spalten auf dem CLI-Pfad blind und meldeten „nicht geklickt"
  für einen Agenten, der geklickt hat.
- **Bei pi stecken die Browser-Aufrufe im Skript.** `tool_calls` zählt
  `codemode` und zusätzlich jeden Aufruf `tools.<werkzeug>(` darin. `clicked`
  und `scrolled` sehen ebenfalls in das Skript.
- **goose fragt das Modell auch für sich selbst.** Es lässt sich den
  Sitzungstitel vom selben Endpunkt erzeugen; diese Anfrage steht mit im Log.
  Der Tokenverbrauch eines CLI-Laufs ist damit **Framework plus Aufgabe**, nie
  die Aufgabe allein. Inspect verfolgt den Hauptfaden davon getrennt, die
  Wertung ist also nicht betroffen — die Verbrauchszahl schon.

Wenn ein CLI-Lauf 0.00 ergibt, steht die Ursache im Score: `Score.metadata.cli`
hält Rückgabewert und die letzten Zeilen von stderr/stdout fest, und
`answer_source` sagt, ob die Befundliste gewertet wurde oder stdout. Ohne
diese Felder bliebe von einem CLI, das gar nicht startet, nur eine Null übrig,
und die Suche landete beim Modell statt beim Aufruf.

## Der Kundenbericht — eine Zugabe ohne Note

Das Ergebnis von A01 ist ein JSON-Array; das ist richtig für die Auswertung und
nutzlos für jeden, der einer Kundin erklären soll, was zu tun ist. Deshalb
läuft nach dem Audit ein **zweiter, eigener Schritt**: Das Modell bekommt seine
eigene JSON-Antwort zurückgereicht und soll daraus einen Bericht bauen, wie ihn
ein technischer SEO übergibt — Zusammenfassung, Befunde nach Dringlichkeit,
Auswirkung und Empfehlung je Befund, Überblickstabelle, Reihenfolge der
Maßnahmen. Das Ergebnis liegt als PDF und als Markdown in `reports/`
(Ziel über `GERMAN_SEO_BENCH_REPORT_DIR`).

```bash
uv run inspect eval german_seo_bench/a01_browser_audit -T report=false     # nur die Zahlen
```

Drei Trennungen machen die Zugabe zu einer Zugabe, und jede hat einen Grund:

*Er steht nicht im Auftrag.* Der Audit-Prompt verlangt ein JSON-Array und sonst
nichts — er erwähnt weder Bericht noch PDF noch Kunden
(`test_der_audit_prompt_weiss_nichts_vom_bericht`). Stünde beides zusammen
darin, müsste ein Modell zwei Dinge zugleich liefern, und gerade kleine Modelle
vermischen das dann: Fließtext um das JSON herum, oder ein Bericht statt der
Liste. Gemessen wird die Analyse.

*Er läuft in einem frischen, kurzen Kontext.* Nicht das ganze Audit-Gespräch
wird fortgesetzt — bei 26 Seiten voller Browser-Snapshots hätte ein kleines
Modell dort kein Fenster mehr. Es bekommt nur seine Antwort und die
Seitenliste. Das ist nebenbei die Arbeitsteilung eines echten Beraters: erst
messen, dann berichten.

*Er kann nichts kaputt machen.* Der Schritt fasst `state.output` nicht an (der
Scorer liest dort die gewertete Antwort) und fängt jeden Fehler ab. Ein Modell,
das keinen Bericht zustande bringt, verliert keinen Punkt — es fehlt eine
Datei, und warum, steht in `Score.metadata.report`. Geprüft wird das am
Oracle-Lauf **mit** Berichtsschritt, wo es gar keinen Container gibt und der
Schritt zwangsläufig scheitert: Der Score bleibt 1.00
(`test_ein_gescheiterter_bericht_kostet_keinen_punkt`).

**Das Modell schreibt Markdown, nicht HTML und schon gar nicht PDF.** Der Weg
ist Markdown → HTML (Layout aus dem Repo) → PDF (Chromium im Container,
`--headless --print-to-pdf`). Ein HTML-Bericht braucht bei gleichem Inhalt etwa
doppelt so viele Ausgabe-Tokens: Fast die Hälfte geht für `<style>`-Block und
Tags drauf, also für Markup, das `REPORT_CSS` ohnehin mitbringt. Der PDF-Druck
selbst kostet nichts Nennenswertes — die Länge der Modellantwort ist der
Kostenfaktor. Zwei weitere Gründe für Markdown: Der Bericht sieht damit bei
jedem Modell gleich aus (sonst vergleicht man beim Durchblättern zweier Berichte
zwei Geschmäcker statt zweier Analysen), und ein kleines Modell bringt Markdown
zuverlässiger zustande als ein vollständiges HTML-Dokument.

Umgewandelt wird mit `markdown-it-py` im CommonMark-Modus plus Tabellen, und mit
`html=False`: Was das Modell schreibt, wird als Text behandelt. Es hat die
geprüfte Website gelesen, mitsamt ihrer Skripte — ein daraus übernommenes
`<script>` soll im Bericht Text sein und kein Code.

Anerkannt wird eine Antwort nur, wenn sie lang genug ist und mindestens eine
Markdown-Überschrift trägt; sonst entsteht bewusst **kein** Notbehelf-PDF, das
nach einem Bericht aussähe. Steckt der ganze Bericht in einem Codeblock, fällt
der Zaun weg. Eine Zeile Höflichkeit darüber („Gern, hier ist der Bericht:")
bleibt stehen — sie zu erkennen hieße zu raten, wo der Bericht anfängt, und das
ist der teurere Fehler.

**Der Bericht trägt seine eigene Bewertung.** Vor dem ersten Befundabschnitt —
also hinter der Zusammenfassung des Modells und vor seinen Empfehlungen — setzt
der Berichtsschritt einen **rot umrandeten Kasten** ein, der nicht vom Modell
stammt:

```
Bewertung dieses Berichts durch den Benchmark
Von 6 gemeldeten Problemen gibt es 3 nicht; 28 tatsächlich vorhandene fehlen.
50% der Meldungen sind richtig · 10% der Probleme gefunden · Score 0,13 von 1,00
   source 1 von 18 (3 falsch) · network 2 von 5 · render 0 von 4 · …
Frei erfunden: poster-a3.html/client_side_redirect, …
```

Ohne ihn wäre das PDF ein hübsches Dokument ohne Wahrheitswert: Ein Bericht aus
einem Lauf mit Score 0,13 sieht genauso sauber aus wie einer mit 0,96 —
dieselbe Gliederung, dieselbe Sprache, dieselben Empfehlungen; nur stimmt bei
dem einen fast nichts davon. Die Zeile „Frei erfunden" ist die praktisch
wichtigste: Sie sagt einem Leser, welche Empfehlung auf nichts beruht.

**War der Lauf gestört, steht es als Erstes im Kasten** — weiß auf rot, über
den Zahlen, weil es entscheidet, wie sie zu lesen sind:

```
Dieser Lauf war gestört. Das Kontextfenster des Modells lief 1× über. Was der
Agent danach noch prüfen konnte, hing davon ab, wie sein Gerüst damit umging;
gewertet wurde die Befundliste, die er bis dahin geführt hatte.
```

Vermerkt werden Kontextüberläufe (aus `Score.metadata.context_overflows`, im
`local/`-Provider gezählt), ein Agent-CLI mit Rückgabewert ungleich null, eine
unlesbare oder teilweise verworfene Antwort, und ein Agent nahe an der
Zuggrenze. Ein ungestörter Lauf bekommt **keine** Zeile — stünde sie immer da,
wäre sie wertlos.

Was dort **nicht** stehen kann: harte Abbrüche. Zeitgrenze, Nachrichtengrenze
und Netzwerkfehler beenden den Datensatz, bevor der Berichtsschritt läuft —
dann entsteht gar kein PDF, und die Abwesenheit ist das Signal; der Grund steht
in der Ergebnisdatei (`report.status`, Fehler des Datensatzes). Die Störungen
im Kasten sind gerade die, die einen Bericht **überleben**, und das sind die
gefährlicheren: Ein abgebrochener Lauf fällt auf, ein neunmal gekürzter liefert
einen sauber formulierten Bericht ab und sieht aus wie ein normales Ergebnis.

Gerechnet wird der Kasten im **Berichtsschritt selbst**, nicht vom Scorer — der
läuft erst danach. Beide benutzen dieselbe Funktion (`evaluate_audit`); zwei
Fassungen derselben Rechnung liefen auseinander, und dann stünde im PDF eine
andere Zahl als im Log. Die Einfügestelle ist die erste Zwischenüberschrift, die
nicht nach Zusammenfassung klingt. Auf eine bestimmte Überschrift zu matchen wäre
unzuverlässig: Modelle übernehmen die Formulierung aus dem Prompt selten
wörtlich („Kritische Befunde: Indexierung und Crawling", „Indexierung und
Crawling (Höchste Priorität)", …).

In `reports/` landen je Lauf zwei Dateien: das PDF und das **rohe Markdown** des
Modells. Letzteres, weil es die Antwort des Modells ist — unser HTML darum ist
reproduzierbar und braucht keine Ablage, der Druck ist nicht zitierfähig. Wer
zwei Modelle vergleichen will, legt die beiden `.md` nebeneinander. Verbrauch
und Dauer stehen als `report.tokens` und `report.seconds` in den Metadaten —
beides, damit sich die Zugabe aus den Zahlen des Datensatzes herausrechnen
lässt: `sample.total_time` kennt den Unterschied zwischen Audit und Bericht
nicht.

## Die Prüflandschaft selbst ansehen

Für Menschen, die verstehen wollen, was ein Modell dort finden soll — oder die
sie mit einem echten Werkzeug crawlen wollen (Screaming Frog, Sitebulb, curl):

```bash
uv run python -m german_seo_bench.testsite          # http://www.example.net/
uv run python -m german_seo_bench.testsite --hosts  # nur die /etc/hosts-Zeile
```

Der Befehl prüft **vor** dem Start, ob der Port benutzbar ist, und
unterscheidet dabei zwei Fälle, die verschiedene Antworten brauchen:

```
belegt          da hört schon etwas  → anderer Port
nicht erlaubt   Port unter 1024 und rootless Docker (Fedora-Vorgabe)
                → sudo sysctl net.ipv4.ip_unprivileged_port_start=80
                  oder --port 8099, dann stimmt die sitemap.xml nicht mehr
```

Der zweite Fall ist der teure: `docker compose up` meldet ihn zwar, läuft aber
weiter, der Browser sagt „nicht erreichbar" und `curl` sagt `Could not
connect` — keine der drei Meldungen nennt die Ursache. Danach meldet der Befehl,
ob die Site tatsächlich antwortet — gefragt wird dabei über `127.0.0.1`, nicht
über den Hostnamen. Das ist die Unterscheidung, die man beim Suchen zuerst
braucht: Antwortet die Schleifenadresse nicht, liegt es am Server; antwortet sie
und der Browser nicht, liegt es an der Namensauflösung oder am Browser.

**Wenn Chrome „nicht erreichbar" sagt, obwohl der Server läuft**, ist es fast
immer die automatische HTTPS-Erhöhung: Chrome versucht bei einer Adresse ohne
Schema zuerst `https://`, und die Prüflandschaft spricht nur HTTP. Die Adresse
deshalb **mit `http://`** eingeben. Hilft das nicht, entscheidet ein
`curl -I http://127.0.0.1/home.html`, auf welcher Seite der Fehler liegt.

Einmalig muss `127.0.0.1 www.example.net` in `/etc/hosts` stehen, dann ist die
Site unter ihrem echten Namen erreichbar — inklusive `sitemap.xml` und
`robots.txt`, deren Adressen absolut sind. Der Vorgabeport ist deshalb **80**:
Mit einem anderen zeigte die Sitemap ins Leere, und ein Crawler, der ihr folgt,
fände die angemeldeten Seiten nicht.

Es ist **derselbe nginx mit derselben Konfiguration** wie im Messlauf, nur mit
veröffentlichtem Port und ohne `internal`-Netz — und das ist der Zweck der
eigenen Compose-Datei. Ein einfacher Dateiserver lieferte nur `site/` aus und
zeigte an vier der sechsundzwanzig Adressen 404 statt der Befunde, die dort
liegen (Cloaking, Zweitbesuch, zwei Weiterleitungen). `TestLokalerServer` hält
Image und Mounts der beiden Topologien zusammen.

Der lokale Server läuft unter einem **eigenen Compose-Projektnamen**
(`german-seo-bench-testsite`). Ohne den teilte er sich den Namensraum mit dem
Messlauf — alle drei Compose-Dateien liegen im selben Verzeichnis, und Compose
leitet den Projektnamen daraus ab. Zwei Dienste hießen dann beide `testsite`
und wären dieselbe Identität: Ein `up` zum Ansehen könnte den Container eines
**laufenden Messlaufs** neu erzeugen.

## Was A01 an Docker braucht

Beim ersten Mal ein paar Minuten für das Image (Chromium plus
`chrome-devtools-mcp`, Version festgenagelt). Der Rest läuft ohne Netz: Der
MCP-Server startet **im Container**, nicht auf dem Host.

Inspect prüft vor dem ersten Container zwei Versionen und ruft danach
ausschließlich `docker` und `docker compose`:

| | |
|---|---|
| Docker Engine | ≥ 24.0.6, und `docker version` muss einen `Server`-Abschnitt melden |
| Docker Compose | ≥ 2.21.0 — das **v2-Plugin** (`docker compose`), nicht `docker-compose` v1 |

**Plattformübergreifend** liefern Docker Desktop (oder OrbStack, Colima mit
Compose-Plugin) auf macOS und Windows genau diese beiden Bausteine, auf Linux
Docker CE. Am Image ändert sich dabei nichts — `chromium` aus Debian gibt es für
amd64 **und** arm64, und genau deshalb steht es im Dockerfile statt Chrome for
Testing, das es für arm64 nicht gibt.

**Rootless Docker funktioniert** und ist der empfohlene Weg auf Linux: Es ist
dasselbe `dockerd` im User-Namespace, meldet also eine Docker-Version ≥ 24 und
bringt das Compose-v2-Plugin mit.

```bash
sudo dnf -y install docker-ce docker-ce-cli docker-compose-plugin docker-ce-rootless-extras
dockerd-rootless-setuptool.sh install          # als normaler Benutzer, nicht mit sudo
systemctl --user enable --now docker
docker context use rootless                    # dauerhaft, auch für Skripte und IDE-Terminals
loginctl enable-linger "$USER"                 # Daemon läuft weiter, wenn die SSH-Sitzung endet
```

Der Kontext ist dem `export DOCKER_HOST=…` vorzuziehen: Er hängt nicht an einer
Shell-Konfiguration. Wer beides setzt, muss dasselbe Ziel nennen — `DOCKER_HOST`
schlägt den Kontext. Ob es dauerhaft sitzt, zeigt eine Shell ohne die Variable:

```bash
env -u DOCKER_HOST bash -lc 'docker version --format json | head -c 200'
```

**Das Agent-Image läuft im Container als root, und das ist Absicht.** Inspect
legt seine Sandbox-Werkzeuge nach `/var/tmp/.<hash>` — root-eigen, Modus 0700 —
und ruft sie danach als Vorgabebenutzer des Images auf. Ein `USER` im Dockerfile
bricht deshalb jeden Werkzeugaufruf mit `returncode=126`. Unter rootless Docker
kostet das nichts: Container-root **ist** dort der eigene Host-Benutzer. Wer ein
eigenes Agent-Image baut, lässt den `USER`-Wechsel weg;
`test_image_laeuft_als_root` hält das fest.

### Podman genügt nicht

Und zwar aus drei unabhängigen Gründen — wichtig auf Fedora und RHEL, wo
`podman-docker` ein `/usr/bin/docker` mitbringt:

1. Podman ist daemonlos. `docker version` liefert Exit 0, aber keinen
   `Server`-Abschnitt — Inspect meldet dann `Unable to determine the Docker
   Engine server version`.
2. Der Compose-Provider ist dort `podman-compose` (1.x). Inspect verlangt 2.21.
3. Auch Podmans Docker-kompatibler Socket hilft nicht: Er meldet Podmans eigene
   Version (5.x), und Inspect vergleicht sie als Semver gegen 24.0.6.

Dazu benutzt Inspect Compose-Schalter, die `podman-compose` nicht kennt
(`up --wait --wait-timeout`, `pull --ignore-buildable --policy`, `cp -L`). Der
Weg ist deshalb nicht, die Gates zu umgehen, sondern Docker zu installieren:

```bash
# Fedora: Docker CE statt des Podman-Wrappers
sudo dnf remove podman-docker                  # gibt den Namen `docker` frei; podman bleibt
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"                # danach neu anmelden
docker version --format json | head -c 200     # muss einen "Server"-Abschnitt zeigen
```

### Docker zeigt auf den falschen Socket

In dieser Reihenfolge prüfen — es ist die Auflösungsreihenfolge der Docker-CLI,
und jede Ebene schlägt die nächste:

```bash
env | grep -i docker                  # 1. DOCKER_HOST gesetzt? schlägt alles
docker context ls                     # 2. welcher Kontext ist aktiv (*), wohin zeigt er?
systemctl --user is-active docker     # 3. läuft der rootless-Daemon?
ls -l /run/user/$(id -u)/docker.sock  # 4. gibt es den Socket?
```

Ein typischer Fall: Punkt 3 und 4 sind in Ordnung, der Kontext `rootless`
existiert — und trotzdem scheitert jeder Lauf, weil `DOCKER_HOST` noch auf
`…/podman/podman.sock` steht und den Kontext überstimmt. Docker sagt das sogar
(`Warning: DOCKER_HOST environment variable overrides the active context`), nur
steht die Warnung bei `docker context ls` und nicht in der Fehlermeldung von
Inspect.

Tritt das in einem **frisch geöffneten** Terminal auf, ist die Variable nicht
von Hand gesetzt worden, sondern kommt aus der Umgebung — und dann nützt ein
`unset` nur in diesem Fenster. Die Quelle finden:

```bash
grep -rn DOCKER_HOST ~/.bash_profile ~/.profile ~/.bashrc ~/.zshrc 2>/dev/null
grep -rn DOCKER_HOST ~/.config/environment.d/ /etc/profile.d/ /etc/environment 2>/dev/null
systemctl --user show-environment | grep -i docker
```

Dabei zwei Fallstricke:

- **Ein `bash -lc` erbt die Umgebung seines Aufrufers.** Wer damit prüft, ob
  eine Startdatei die Variable setzt, misst in Wahrheit das eigene Terminal.
  Richtig ist `env -u DOCKER_HOST bash -lc 'echo ${DOCKER_HOST:-<leer>}'` —
  bleibt das leer, ist keine Startdatei beteiligt.
- **`~/.config/environment.d/` schlägt kein `/etc/profile.d/`.** Ersteres liest
  der systemd-User-Manager beim Anmelden, Letzteres die Shell danach — ein
  Gegeneintrag in `environment.d` wird also überschrieben, wenn die Quelle ein
  Profilskript ist. Gegen ein Profilskript hilft nur ein späteres Profilskript
  (`/etc/profile.d/zz-…sh`) oder das Entfernen der Quelle.

**Und wenn gar keine Datei sie setzt?** Dann wurde sie imperativ gesetzt —
`systemctl --user set-environment` oder `dbus-update-activation-environment`,
typischerweise einmalig beim Umstieg von Podman auf Docker. So eine Variable
steht nirgends auf der Platte, vererbt sich aber an jeden Prozess, der danach
gestartet wurde. Ein langlebiges Terminal (Konsole, gnome-terminal-server) gibt
sie dann an jedes neue Fenster weiter — deshalb hilft „neues Terminal" nicht.
Nachsehen lässt sich das an der Prozessumgebung des Terminals selbst:

```bash
tr '\0' '\n' < /proc/$(pgrep -u "$USER" -x konsole)/environ | grep -i DOCKER
```

Aufräumen: `systemctl --user unset-environment DOCKER_HOST` für den
User-Manager, dann das Terminal vollständig beenden und neu starten. Sicher weg
ist sie nach einem Neustart — mit `loginctl enable-linger` überlebt der
User-Manager sogar das Abmelden, ein reines Ab- und Anmelden räumt eine solche
Altlast also nicht zwangsläufig weg.

## Grenzen

Der Agent-Pfad ist nur auf einer Maschine mit Docker nachvollziehbar; der
Oracle-Lauf und `uv run pytest` laufen überall. Gleiches gilt für die
Referenz-Erzeugung — ohne Docker bleibt `--capture` als Weg, ein anderswo
erzeugtes Capture auszuwerten.
