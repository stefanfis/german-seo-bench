# german-seo-bench

Benchmarks für Sprachmodelle mit Fokus auf deutschen SEO-Aufgaben — Messungen für lokale Modelle und Frontier-Modelle, basierend auf dem Framework [Inspect AI](https://inspect.aisi.org.uk).

Jeder Score wird rein **maschinell** gerechnet: Er ergibt sich aus der Antwort des Modells und einer vorgegebenen Referenz und lässt sich damit nachrechnen. In die Benchmark-Ergebnisse fließt somit keine Bewertung durch eine KI ein. Jeder Lauf
hinterlässt ein vollständiges Log mit jeder Anfrage und jeder Antwort im Wortlaut.

> **Status: 2026.1:** Stabil genug für aussagekräftige Messungen. Es werden aber noch Aufgaben hinzukommen und sich bestehende ändern. Siehe [Versionen](#versionen).

## Start

Voraussetzung ist [uv](https://docs.astral.sh/uv/) — sonst nichts. `uv` besorgt
beim ersten Aufruf selbst ein passendes Python und alle Abhängigkeiten (rund
90 MB). Das passiert einmal, danach startet ein Lauf sofort.

```bash
uv run german-seo-bench      # alle Aufrufe und alle Aufgaben, immer aktuell
uv run pytest                # Wertung und Prüfungen — ohne Modell, ohne Netz
uv run inspect eval german_seo_bench/m01_titles --model local/qwen35-4b
uv run inspect view          # Ergebnisse im Browser
```

`uv run german-seo-bench` nennt die typischen Aufrufe und listet jede Aufgabe mit
Anzeigename und Anzahl der Datensätze. Die Liste wird bei jedem Aufruf aus dem
Paket erzeugt, kann also nicht veralten. Gemessen wird damit nichts.

Nichts einzurichten, keine Konfigurationsdatei. `local/…` erwartet einen
OpenAI-kompatiblen Server (llama-swap, llama-server, Ollama, LM Studio) auf
`http://localhost:8080/v1`; läuft er woanders, kommt
`--model-base-url http://192.168.1.50:8080/v1` dazu oder `LOCAL_BASE_URL` in
eine `.env`. Frontier-Modelle laufen über dieselbe Kommandozeile
(`--model anthropic/claude-sonnet-5`, `--model openai/gpt-5`), mit dem Schlüssel
in `ANTHROPIC_API_KEY` bzw. `OPENAI_API_KEY`.

Die Logs landen in `./logs` — relativ zum **Arbeitsverzeichnis**, nicht zum
Projekt. Am einfachsten ruft man die Befehle im Projektwurzelverzeichnis auf;
wer es festnageln will, setzt `INSPECT_LOG_DIR`.

Ohne Checkout, direkt aus dem Repo:

```bash
uvx --from git+https://github.com/stefanfis/german-seo-bench german-seo-bench
uvx --from git+https://github.com/stefanfis/german-seo-bench \
    inspect eval german_seo_bench/m01_titles --model local/qwen35-4b
```

### Die Bilder für M10

Die 32 Bilder von M10 stammen von [Pixabay](https://pixabay.com) und liegen
**nicht** im Repo. Pixabay prüft nicht, ob ein Uploader die Rechte an einem Bild
hat, und die Lizenzbedingungen haben sich mehrfach geändert. Das Repo nennt die
Bilder deshalb nur: [`data/media.json`](src/german_seo_bench/data/media.json)
führt je Bild die Pixabay-Seite, den Uploader und die Prüfsumme der Fassung,
gegen die gemessen wird. Geholt werden sie einmalig über die Pixabay-API, mit
einem [kostenlosen Schlüssel](https://pixabay.com/api/docs/):

```bash
PIXABAY_API_KEY=… uv run python -m german_seo_bench.media
```

Der Befehl holt, was fehlt, und prüft jede Datei gegen ihre Prüfsumme. Die
Bilder landen in `bench-media/` im aktuellen Ordner, neben dem `logs/` von
Inspect; ein Update über `uvx` lässt sie dort unberührt. M10 sucht sie am
selben Ort, also dort starten, wo sie geholt wurden — oder
`GERMAN_SEO_BENCH_MEDIA` auf einen festen Ordner setzen, beim Holen wie beim
Lauf. Ohne Bilder bricht M10 beim Start ab und nennt den Pfad, unter dem es
gesucht hat. Alle
anderen Aufgaben, der Oracle-Lauf von M10 und `uv run pytest` brauchen sie
nicht.

**Es gibt bewusst keine Hülle um `inspect eval`.** Die Bedienoberfläche ist
Inspect selbst — `--limit`, `--epochs`, `--temperature`, `-T`. Ein Wrapper
könnte davon nur eine Auswahl durchreichen und würde das Werkzeug verdecken, das
man ohnehin kennen muss.

## Aufgaben

| ID | Was gemessen wird | Art | Doku |
|----|-------------------|-----|------|
| M01 | Deutsche SEO-Seitentitel: 29 Seiten, fünf Prüfungen, fünf Stufen | one-shot | [m01_titles](docs/tasks/m01_titles.md) |
| M01-Judge | Kalibrierung eines Titel-Judges gegen menschliche Urteile | one-shot | [m01_titles](docs/tasks/m01_titles.md#der-titel-judge) |
| M02 | Weltwissen für SEO im deutschsprachigen Raum, Multiple Choice: 195 Fragen mit je zehn Optionen, darunter Reisen, E-Commerce mit Normen und Handelsrecht, technisches SEO, Österreich und Schweiz; `score`, `invalid`, Profil nach Thema | one-shot | [m02_knowledge_mc](docs/tasks/m02_knowledge_mc.md) |
| M03 | Fakten-Extraktion aus Produkttexten: 30 Texte, sieben Felder | one-shot | [m03_facts](docs/tasks/m03_facts.md) |
| M10 | Deutsche ALT-Texte: 32 Bilder, sieben Prüfungen — **braucht ein bildfähiges Modell** und [einmalig die Bilder](#die-bilder-für-m10) | one-shot | [m10_alt_text](docs/tasks/m10_alt_text.md) |
| A01 | Browser-Audit einer JS-Site: 26 Seiten, 22 Fehlertypen, wahlweise über `react` oder `goose` — **braucht Docker** | agentisch | [a01_browser_audit](docs/tasks/a01_browser_audit.md), [zum Mitspielen](docs/A01.md) |

In M01, M03 und M10 ist `score` **alles oder nichts**: Ein Datensatz zählt, wenn
er vollständig richtig ist, sonst nicht. Die Einzelspalten daneben sagen, woran
es lag. M01 zieht zusätzlich bei bestandenen Titeln leicht ab, für grobe
Stilfehler (z. B. „Igel Richtig Füttern“), für ein Keyword, das zu weit
hinten steht, und für Titel, die ein paar Zeichen zu lang sind, siehe
[Abzüge](docs/tasks/m01_titles.md#abzüge). M10 zieht ebenso für Überlänge ab. A01 wertet anders, weil ein Datensatz dort kein Frage-Antwort-Paar ist, sondern ein Auftrag über 26 Seiten mit 31 Befunden.

Mehrere Läufe desselben Modells werden **nicht gemittelt**: Im Bericht zählt
je Datensatz der schlechteste Lauf, siehe [„So wird gerechnet“](docs/running.md#läufe-zusammenfassen).
Das klingt auf den ersten Blick eventuell unfair, doch im Einsatz für den SEO-Alltag ist Zuverlässigkeit essenziell: Ein Modell, das in vier von fünf Läufen zuverlässig einen guten Seitentitel schreibt, aber im fünften völlig daneben liegt, ist für den massenhaften Einsatz nicht zu gebrauchen.

## Ergebnisse

Meine eigenen Messungen stehen unter [`results/`](results/README.md), je
Benchmark-Version und Denkmodus ein Bericht mit allen Antworten.

## Der Oracle-Lauf

```bash
uv run inspect eval german_seo_bench/m01_titles -T oracle=true --model none/none
```

`-T oracle=true` stellt keine Anfrage. Es setzt die Musterlösung aus der
Datendatei als Antwort ein und schickt sie durch **denselben** Scorer wie eine
Modellantwort. Das Ergebnis **muss** 1.00 sein. Ist es das nicht, ist nicht das
Modell schlecht, sondern die Prüfung kaputt — und dann ist jede Zahl aus dieser
Aufgabe wertlos. `--model none/none` ist dabei kein Platzhalter, sondern eine
Zusicherung: Ein Lauf, der doch ein Modell anfragt, bricht ab statt still zu
messen.

`uv run pytest` fährt denselben Lauf für jede Aufgabe mit und prüft dazu die
Gegenprobe: Eine absichtlich untaugliche Antwort darf *nicht* 1.00 ergeben.

## Weiterlesen

- [Läufe durchführen und auswerten](docs/running.md) — Modellbezeichnungen,
  lokale und Cloud-Server, Laufvorgaben, Fehlersuche, was ein Lauf hinterlässt,
  Läufe zu Tabellen, Laufdaten (Sampling, Laufzeit, Tokens/s, Kontext) und
  Einzelergebnissen zusammenfassen, abgebrochene Läufe mit Ursache, Denkmodi
  eines Modells nebeneinander (`python -m german_seo_bench.report`)
- [Aufbau und Methodik](docs/architecture.md) — die Suchregel, warum der Prompt
  strenger ist als die Prüfung, eine eigene Aufgabe bauen, Trennschärfe je
  Datensatz (`itemcheck`), Zuverlässigkeit über mehrere Durchgänge
- Je Aufgabe: [M01](docs/tasks/m01_titles.md),
  [M02](docs/tasks/m02_knowledge_mc.md),
  [M03](docs/tasks/m03_facts.md), [M10](docs/tasks/m10_alt_text.md),
  [A01](docs/tasks/a01_browser_audit.md)

## Versionen

Es gibt zwei Versionsnummern, und sie beantworten verschiedene Fragen:

- Die **Benchmark-Version** ist die Paketversion (`version` in `pyproject.toml`,
  Git-Tag `vJJJJ.N`) und benennt einen Stand des ganzen Repos. Sie besteht aus
  dem Jahr der Veröffentlichung und einer laufenden Nummer innerhalb des Jahres:
  `2026.1`, `2026.2`, …, `2027.1`. Inspect schreibt sie in jedes Log
  (`eval.packages`), `report` nennt sie unter jeder Tabelle.
- Die **Aufgabenversion** (`version` in der Datendatei, im Log als
  `eval.task_version`) sagt, ob zwei Scores **derselben Aufgabe** vergleichbar
  sind. Sie steigt, sobald sich Prompt, Datensatz oder Prüfung ändern — auch
  wenn sich nur ein Satz im Prompt ändert.

Die beiden laufen bewusst nicht synchron: Ändert sich eine Aufgabe, bleiben die
Scores aller anderen vergleichbar. Wer eine Zahl zitiert, nennt beides, etwa
„german-seo-bench 2026.1, A01 v1". `report` bricht ab, wenn Läufe einer Aufgabe
mit verschiedenen Aufgabenversionen zusammengerechnet werden sollen.
Verschiedene Benchmark-Versionen bei gleicher Aufgabenversion fasst er
zusammen und nennt jede mit der Zahl ihrer Läufe.

Ein Gesamtscore trägt nur die Benchmark-Version, denn sie legt alle
Aufgabenversionen fest (Tabelle unten). Mit dem Gesamtscore eines anderen
Stands ist er nur vergleichbar, wenn dort dieselben Aufgaben in denselben
Versionen gelaufen sind.

| Benchmark | M01 | M01-Judge | M02 | M03 | M10 | A01 |
|---|---|---|---|---|---|---|
| 2026.1 | 1 | 1 | 1 | 1 | 1 | 1 |

## Grenzen

- Die Scores verschiedener Aufgaben sind **verschieden definiert** und nicht
  gegeneinander vergleichbar. „Modell X hat 0,72" ist ohne Angabe der Aufgabe
  wertlos.
- Bei `--temperature 0` ist eine Wiederholung meist bitgleich; die Praxis läuft
  aber fast überall mit einem Vorgabewert über 0. Für Werte über 0 sind
  mehrere Durchgänge Pflicht (`--epochs 5`), eine Einzelmessung sagt dort
  nichts.
- `llama-server` ignoriert den Modellnamen einer Anfrage stillschweigend und
  antwortet mit dem Modell, das gerade geladen ist — auch auf einen Tippfehler.
  Mit `llama-swap` greift die Namensauflösung hingegen; wer direkt gegen `llama-server`
  misst, sollte vorher per `GET /v1/models` prüfen, welches Modell wirklich benutzt wird.
- In M01 und M10 treffen die Prüfungen **nur die aufgezählten Formen** (siehe
  [die Suchregel](docs/architecture.md#die-suchregel-eine-phrase-an-wortgrenzen-wörtlich)).
  „Filialen" trifft `Filiale` nicht, und in M01 „Gartenbänke" nicht `Bank`. Damit werden
  manchmal korrekte Antworten in Einzelfällen als Fehler gewertet. Dies lässt sich bei einer
  deterministischen Prüfung nicht komplett vermeiden und es ist immer eine Abwägung, was
  der Test gelten lassen soll und was nicht. 
  Ausnahme sind Motiv und Zusammenhang in M10, dort trifft ein Listenwort auch als Grundwort
  („Holzhütte" trifft `Hütte`). Endungen werden aber auch dort nicht geraten:
  Beim Erweitern gehört jede Form in die Liste, Plural und Genitiv
  eingeschlossen.
- In M01, M03 und M10 ist `score` alles oder nichts (M01 und M10 mit
  leichten Abzügen auf bestandene Antworten). Das macht ihn hart gegen
  Zufall: Ein Modell, das je Datensatz eine Kleinigkeit anders falsch macht,
  landet bei 0.00, obwohl es fast alles konnte. Wer wissen will, WIE knapp es
  war, liest die Einzelspalten.
- Die Stufen in M01 und M03 sind empirisch: Sie bilden ab, was für einige
  kleine Modelle schwer war, und die in M03 sind ausdrücklich nicht kalibriert.
  M10 hat gar keine. Eine Stufe ist keine Eigenschaft der Aufgabe, sondern ein
  Messbefund mit Verfallsdatum; `python -m german_seo_bench.itemcheck
  logs/*.eval` rechnet sie nach, sobald neue Läufe vorliegen.
- M10 misst nur, was sich aus der Zeichenkette gegen eine eingefrorene Referenz
  entscheiden lässt. `grounded` findet nur die Erfindungen, die jemand vorher in
  die Sperrliste geschrieben hat: Es ist eine Stichprobe auf Halluzination, kein
  Nachweis ihrer Abwesenheit.
- M02 wertet nur eine Antwort, die sich festlegt: Sie beginnt mit dem Buchstaben oder nennt genau
  den Text einer Option. „Ich bin nicht sicher, aber ich glaube C“ zählt nicht, auch wenn C richtig ist.
- Die Musterlösungen stehen offen im Repo. Das ist Absicht — nachvollziehbar zu
  sein ist der Zweck. Irgendwann mag vielleicht ein Trainingsdaten-Effekt eintreten,
  das lässt sich nicht ganz ausschließen.
- Die Agentic-Aufgabe A01 benötigt Docker; der Oracle-Lauf und `uv run pytest`
  laufen überall.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Das gilt für Code und Datensätze, nicht für
die Bilder von M10: Für sie gelten die Bedingungen von Pixabay, und sie sind
nicht Teil des Repos (siehe [oben](#die-bilder-für-m10)).
