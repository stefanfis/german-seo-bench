# Läufe durchführen und auswerten

## Modelle anbinden

### Die richtige Modellbezeichnung finden

Ein Modellname besteht aus zwei Teilen, und nur der erste gehört Inspect:

```
--model anthropic/claude-sonnet-5
        ^^^^^^^^^ ^^^^^^^^^^^^^^^
        Provider  geht WÖRTLICH an den Anbieter
```

Inspect streicht das Präfix und reicht den Rest unverändert weiter
(`service_model_name()` im Anthropic-Provider). Es gibt **keine Liste gültiger
Namen in Inspect** und keine Übersetzung — was der Anbieter akzeptiert, gilt;
was er nicht kennt, endet als `NotFoundError` beim ersten Aufruf. Daraus folgt
die Regel: Den Namen nicht raten und nicht aus dem Gedächtnis tippen, sondern
dort holen, wo er herkommt.

**Anthropic.** Zwei Wege, beide liefern denselben String:

```bash
# 1. Die Modell-API — die verbindliche Liste, mit Datum
curl -s https://api.anthropic.com/v1/models \
     -H "x-api-key: $ANTHROPIC_API_KEY" \
     -H "anthropic-version: 2023-06-01" \
  | python3 -c 'import json,sys; [print(m["id"], "\t", m["display_name"]) for m in json.load(sys.stdin)["data"]]'
```

2. Im Dashboard (`platform.claude.com`) das Modell anklicken — der
   Kopieren-Knopf gibt dieselbe Id heraus, etwa `claude-sonnet-5-5`. Davor das
   `anthropic/`, fertig: `--model anthropic/claude-sonnet-5-5`.

**Alias oder gepinnter Snapshot — für eine Messung nimm den Snapshot.** Manche
Modelle gibt es unter zwei Ids, und das Dashboard zeigt je nach Modell die eine
oder die andere:

```
claude-haiku-4-5              Alias     — zeigt auf den jeweils aktuellen Stand
claude-haiku-4-5-20251001     Snapshot  — feste Gewichte, festes Datum
```

Beide funktionieren; Inspect reicht sie unverändert weiter. Für einen Benchmark
ist der Unterschied aber derselbe wie bei einem llama-swap-Alias: **Ein Alias
ist eine Behauptung.** Er kann auf einen neuen Stand umziehen, und dann misst
derselbe Befehl in drei Monaten etwas anderes — ohne dass eine Zahl es verrät.
Im Log steht wörtlich, was auf der Kommandozeile stand (`eval.model`); bei
einem Alias steht dort also der Alias und nicht, welche Gewichte geantwortet
haben. Wer Zahlen später zitieren will, nimmt die datierte Id.

Was dagegen **nicht** funktioniert, ist ein Datum, das man sich selbst
ausdenkt: Die Ids der aktuellen Generation (`claude-sonnet-5`,
`claude-sonnet-5-5`, `claude-opus-4-8`) sind vollständig, wie sie dastehen, und
ein daran angehängtes `-2026…` ergibt einen Namen, den es nicht gibt — Antwort
ist ein `NotFoundError`. Deshalb die Regel oben: kopieren, nicht konstruieren.
Welche Form es für ein bestimmtes Modell gibt, zeigt die Modell-API.

**Punktversionen werden mit Bindestrich geschrieben.** Aus „Sonnet 5.5" wird
`claude-sonnet-5-5`, aus „Opus 4.8" wird `claude-opus-4-8`. Inspect erkennt
solche Punktversionen selbst als Claude-5-Generation und stellt die Anfrage
entsprechend — auch mit angehängtem Datum, und auch für ein Modell, das neuer
ist als das installierte Inspect. Ein Update braucht es dafür nicht.

**Lokale Server.** Dieselbe Frage, dieselbe Antwort — nur ist die Liste hier
die der eigenen llama-swap-Konfiguration:

```bash
curl -s http://192.168.1.50:8080/v1/models | python3 -c 'import json,sys; [print(m["id"]) for m in json.load(sys.stdin)["data"]]'
```

Ein Tippfehler im Alias sieht übrigens nicht wie ein Tippfehler aus: llama-swap
antwortet mit 5xx, und im Log steht `RetryError … InternalServerError`. Deshalb
steht die Zieladresse je Datensatz in `metadata.endpoint` und der tatsächlich
geladene Upstream nach der Antwort in `metadata.server` — Absicht, Auftrag,
Ergebnis (siehe „Was ein Lauf hinterlässt").

### Lokal, Cloud, mehrere Server

Lokale Server sprechen die OpenAI-Schnittstelle — llama-swap, llama-server,
Ollama, LM Studio. `--model local/<modell>` erreicht sie:

```bash
--model local/qwen35-4b                                       # localhost:8080
--model local/qwen35-4b --model-base-url http://10.0.0.7:8080/v1
```

Einen API-Schlüssel verlangt der Provider nicht; lokale Server prüfen keinen.
Wer die Adresse nicht bei jedem Aufruf tippen will, setzt `LOCAL_BASE_URL` —
als Umgebungsvariable oder in einer Datei `.env` im Projektverzeichnis, die
Inspect selbst einliest. Vorrang hat immer `--model-base-url`.

Wer mehrere Server nebeneinander misst, nimmt Inspects eigenen Weg:
`--model openai-api/<dienst>/<modell>` mit `<DIENST>_BASE_URL` und
`<DIENST>_API_KEY` je Dienst. Dann steht der Rechner im Modellnamen und damit
auch im Log.

Frontier-Modelle zum Vergleich laufen über dieselbe Kommandozeile —
`--model anthropic/claude-sonnet-5`, `--model openai/gpt-5`; die genaue Id
holt man sich wie oben beschrieben. Die Clients für beide liegen in den
Abhängigkeiten, nicht in einem Extra: Der Vergleich lokal gegen Frontier ist
die Frage, für die es dieses Repo gibt, und ein vergessener Schalter fiele
sonst erst beim Start eines bezahlten Laufs auf. Andere Anbieter bleiben
ungebunden — für einen einzelnen Lauf genügt
`uv run --with google-genai inspect eval …`. Deren Schlüssel
erwartet Inspect in `ANTHROPIC_API_KEY` bzw. `OPENAI_API_KEY`; auch die kann
`.env` tragen. Dass die Wertung identisch bleibt, ist der Punkt: lokal gegen
Cloud wird mit demselben Maß gemessen.

**Denken bei Cloud-Modellen.** Claude ab 4.7 denkt ohne Angabe adaptiv, also
nach eigenem Ermessen. Abgeschaltet wird es mit Inspects Schalter
`--reasoning-effort none`, die Tiefe stellt `low` bis `xhigh` ein:

```bash
uv run inspect eval german_seo_bench/m03_facts \
    --model anthropic/claude-haiku-5-5 --reasoning-effort none
```

Der Bericht führt diesen Lauf als `…-think-none` unter „aus“, ohne Schalter als
`…-think-adaptive` unter „an“ (siehe „Denkmodi“ unten). Was `none` bewirkt,
hängt am Modell:

| Modell | `--reasoning-effort none` |
|---|---|
| Haiku 5.5, Opus 5, Sonnet 5, Opus 4.7/4.8 | Denken aus |
| Sonnet 5.5 | kein Denken vorab, aber weiter zwischen Werkzeugaufrufen (zählt nur in A01) |
| Opus 5.5, Fable 5 | nicht abschaltbar; Inspect schickt das `none` gar nicht erst |

Haiku 5.5 und Opus 5 schalten das Denken nur bei `--effort` bis `high` ab;
mit `xhigh` oder `max` dazu setzt Inspect den Effort mit einer Warnung auf
`high` herunter. Der Benchmark setzt `--effort` nirgends. Ob ein Lauf wirklich
ohne Denken lief, zeigt `inspect view`: In den Modellantworten steht dann kein
Reasoning-Block. Der Bericht kann das nicht prüfen — er liest nur, was
angefordert wurde, und führt auch einen Opus-5.5-Lauf mit `none` unter „aus“.


## Wie ein Lauf eingestellt ist

Die Vorgaben sind bewusst vorsichtig. Ein Lauf soll ohne Zutun durchlaufen,
auch gegen einen Inferenzserver auf dem eigenen Schreibtisch:

| Vorgabe | Wert | wogegen |
|---|---|---|
| `max_connections` | 1 | eine Anfrage nach der anderen |
| `attempt_timeout` | 600 s, A01: 1800 s | ein Versuch, der nie zurückkommt |
| `timeout` | 600 s, A01: 1800 s | die Wiederholungen insgesamt |
| `max_retries` | 2 | endloses Wiederholen |
| `max_tokens` | 4096, A01: 16384 | ein Modell, das nicht aufhört zu generieren |

Die beiden Zeitgrenzen sind **nicht** dasselbe, und die Verwechslung kostet
Fehlersuche: `timeout` wird nur *zwischen* zwei Versuchen geprüft — ein
einzelner Versuch, der hängt, läuft daran vorbei. Nur `attempt_timeout` bricht
ihn wirklich ab.

600 s reichen für `max_tokens` bei 10 Tokens pro Sekunde. Darauf kommt es an:
Erreicht eine ausufernde Antwort die Längengrenze, wird sie abgeschnitten
gewertet, und der Lauf geht weiter. Greift vorher die Zeitgrenze, bricht der
ganze Lauf mit einem Fehler ab.

A01 setzt beide auf 1800 s und `max_tokens` auf 16384. Dort wächst der
Kontext über das Audit auf Zehntausende Tokens, ein Agent braucht lange
Zwischenüberlegungen, und die Schlussantwort mit allen Befunden ist die längste
des Laufs; bei eingeschaltetem Denkmodus zählt die Denkphase mit. 1800 s reichen
für die volle Länge bei 10 Tokens pro Sekunde. Die äußere Schranke bleibt die
Zeitgrenze des Datensatzes (120 Minuten).

Unter Inspect sitzt noch der OpenAI-Client, und der hat eigene Vorgaben: 600 s
je Anfrage und zwei stille Wiederholungen. Beim Provider `local/…` sind sie
abgeschaltet (Wartezeit 7200 s, keine Wiederholung), damit die Zeitgrenzen
oben die sind, die wirklich greifen, und jede Wiederholung im Log steht.
Sonst bricht der Client eine Antwort, die länger als 10 Minuten braucht, ab
und schickt sie neu, bis `attempt_timeout` den Lauf beendet. Im Log von
llama-swap steht das als drei Anfragen mit Status 499 nach je 10 Minuten.
Wer `--attempt-timeout` über 7200 s hebt, setzt `-M client_timeout=…` mit.

Inspects eigene Vorgaben zielen auf gehostete Anbieter: Es startet bei **20
gleichzeitigen Verbindungen**, wiederholt **unbegrenzt** und wartet
**unbegrenzt**. Gegen llama-swap auf einem kleinen Rechner ergibt das einen
Lauf, der bei 19 von 20 Datensätzen stehenbleibt und nie fertig wird: Der
Server antwortet unter Last mit 503, das zählt bei Inspect nicht als
`rate_limit`, und der adaptive Regler senkt die Nebenläufigkeit deshalb nie.

Alles davon lässt sich überschreiben; die Kommandozeile gewinnt gegen die
Repo-Vorgabe:

```bash
--max-connections 8      # gegen einen Cloud-Anbieter lohnt Parallelität
--timeout 900            # großes Modell mit langer Reasoning-Spur
--max-retries 5          # unzuverlässige Verbindung
--epochs 5               # mehrere Durchgänge (Pflicht bei Temperatur > 0)
```

Die Werte stehen als benannte Konstanten in `src/german_seo_bench/config.py`, mitsamt
Begründung.

## Wenn etwas schiefgeht

| Symptom | Was es ist |
|---|---|
| Lauf bleibt bei *n*−1 von *n* stehen, `http_retries` wächst | Server überlastet oder nicht erreichbar. Mit den Vorgaben hier kann das nicht mehr passieren — tritt es doch auf, wurde `--max-connections` hochgesetzt. |
| `RetryError … InternalServerError` nach ~20 s | Der Server antwortet mit 5xx. Bei llama-swap meist: Modellname gibt es nicht, oder das Modell lädt noch. `curl http://localhost:8080/v1/models` zeigt, was da ist. |
| `InternalServerError … 500 … "Compute error."` | Das Modell ist geladen, aber **jede** Generierung scheitert — ein Problem des Upstreams, nicht der Aufgabe. In drei Schritten eingegrenzt, ohne Benchmark: eine Ein-Satz-Anfrage an dasselbe Modell (scheitert auch), dieselbe Anfrage an ein anderes Modell desselben Servers (läuft), und `/upstream/<alias>/tokenize` gegen `/upstream/<alias>/completion` (Tokenisieren geht, Dekodieren nicht). Dann liegt es am GGUF, am llama.cpp-Build oder am Speicher für den KV-Cache — nicht an der Promptlänge und nicht an den Werkzeugschemata. |
| `AttemptTimeoutError` nach 10 Minuten (A01: 30 Minuten) | Ein Versuch kam nicht zurück. Bei lokalen Servern fast immer eine durchgelaufene Generierung. Zeigt das Log von llama-swap dazu mehrere Anfragen mit Status 499 nach je 10 Minuten, hat der OpenAI-Client abgebrochen und wiederholt, nicht Inspect: Dann lief das Modell nicht über `local/…`, oder `-M client_timeout`/`-M max_retries` wurde überschrieben. |
| Einzelne Scores mit „bei max_tokens abgeschnitten" | Das Modell hat nicht schlecht formuliert, sondern nicht aufgehört. Das ist eine Modellaussage — aber eine andere als „schreibt schlechte Titel". |
| `Anthropic API requires optional dependencies. Install with: pip install anthropic` | Der Anbieter-Client fehlt in der Umgebung. `anthropic` steht in den Abhängigkeiten — ein `uv sync` (oder einfach der nächste `uv run`) genügt. Für einen Anbieter, der nicht mitinstalliert ist: `uv run --with <paket> inspect eval …`. |
| `APIConnectionError` sofort | Kein Server unter der Adresse. Welche das war, steht je Datensatz unter `metadata.endpoint` — steht dort `provider_default: true`, hat niemand eine genannt und es galt `localhost:8080`. |
| Alle Scores 0.00 | Erst die Antworten ansehen (`uv run inspect view`), bevor das als Modellaussage gilt. |
| In M10 bricht jeder Datensatz mit einem Serverfehler ab | Das Modell hat keinen Vision-Projektor. Das ist eine Aussage über den Aufbau, nicht über das Modell — der llama-swap-Eintrag braucht ein `--mmproj`. Was geladen war, steht nach dem Lauf in `metadata.server`. |
| A01: `Unable to determine the Docker Engine server version` | Das `docker` im PATH ist **Podman**. Es ist daemonlos, `docker version` liefert deshalb Exit 0 ohne `Server`-Abschnitt. Siehe [„Was A01 an Docker braucht"](tasks/a01_browser_audit.md#was-a01-an-docker-braucht). |
| A01: `require Docker Compose >= 2.21.0 (current: 1.6.0)` | Der Compose-Provider ist `podman-compose`. Inspect braucht das Compose-v2-Plugin. |
| A01: `failed to connect to the docker API at unix:///run/user/…/podman/podman.sock` | Etwas zeigt noch auf Podman. Tritt es in einem **frischen** Terminal auf, ist es keine vergessene Variable von vorhin — dann die Diagnose unter [„Docker zeigt auf den falschen Socket"](tasks/a01_browser_audit.md#docker-zeigt-auf-den-falschen-socket) durchgehen. |
| A01: `returncode=126 … /var/tmp/.<hash>/inspect-sandbox-tools: Permission denied` | Das Image wechselt auf einen Nicht-root-Benutzer. Inspect legt seine Sandbox-Werkzeuge root-eigen mit 0700 ab und ruft sie als Vorgabebenutzer des Images auf — ein `USER` im Dockerfile bricht damit jeden Werkzeugaufruf. Nicht die Mount-Optionen von `/var/tmp`. |
| A01 mit `-T agent=goose` oder `pi`: score 0.00, `answer_source: final_answer` | Das CLI hat keine Befundliste geschrieben, gewertet wurde stdout. Erst `Score.metadata.cli` ansehen: Dort stehen Rückgabewert und die letzten Zeilen von stdout/stderr. Ein CLI, das gar nicht startet, liefert dieselbe Null wie ein Modell, das nichts findet. Steht dort `exceeds the context`, als Nächstes `Score.metadata.context` ansehen: Ein `window` unter 65536 ist zu klein für A01, nicht das Modell zu schwach. |
| A01: `Score.metadata.context.source` ist `assumed` bei einem lokalen Modell | Das Fenster ließ sich vor dem Lauf nicht vom Server lesen, gerechnet wurde mit 65536. Den Grund nennt `context.note` (etwa eine Zeitüberschreitung, weil das Modell länger als 10 Minuten lud). Bei goose heißt das: Es hat mit einem zu kleinen Fenster gerechnet und unnötig früh verdichtet. `metadata.server` zeigt, was `/props` und `/running` nach dem Lauf geliefert haben. |
| A01 mit `-T agent=goose` oder `pi`: `Score.metadata.cli.finished` ist `false` | Das CLI war an `TIME_LIMIT` (120 Minuten je Datensatz) nicht fertig; der Datensatz trägt `limit: time`. Gewertet wurde der letzte Stand der Befundliste. Das Modell ist zu langsam für die Zeitgrenze oder hat sich verrannt; die Modellaufrufe im Log zeigen, was es zuletzt tat. |
| A01 mit `-T agent=goose`: Die Schlussantwort ist ein kurzer Titel wie „Kartenshop technical SEO audit" | Die Titelanfrage von goose ist in den Verlauf des Audits geraten. Das passiert, wenn die Bridge mit `compaction=` läuft; A01 tut das seit dem Lauf vom 2.11.2026 nicht mehr. |
| A01: `answer_source: findings_file` und `final_answer_error` gesetzt | Der Lauf endete vorzeitig, gewertet wurde der letzte Stand der Befundliste. Warum er endete, steht in `Score.metadata.cli` (goose) bzw. im `limit` des Datensatzes (Zeit- oder Nachrichtengrenze). |
| A01 mit `-T agent=goose` oder `pi`: `'goose' not found` bzw. `'pi' not found` | Das Agent-Image ist älter als dieses CLI. `docker compose -f …/compose.yaml build` neu bauen. |
| A01: die Referenz-Erzeugung meldet Abweichungen nach einer Änderung an `site/` | Genau dafür ist sie da. Absicht? Dann `--write` und `version` in `data/a01_browser_audit.json` hochzählen, falls seit der letzten Veröffentlichung noch nicht geschehen ([Versionen](../README.md#versionen)). Sonst ist die Prüflandschaft kaputt. |

Einen laufenden Eval kann man aus einer zweiten Shell ausfragen — das zeigt,
welcher Datensatz gerade dran ist und wie lange er schon nichts mehr gesagt hat:

```bash
uv run inspect ctl sample
```


## Was ein Lauf hinterlässt

Eine `.eval`-Datei in `logs/`. Sie enthält je Datensatz den Prompt, die Antwort
im Wortlaut, die Teilwerte je Prüfung und die Begründung, dazu Modell, Endpunkt,
Sampling-Werte, Token-Verbrauch und Aufgabenversion.

Bei A01 zusätzlich ein Kundenbericht in `reports/` — als PDF und als rohes
Markdown, Beiwerk für Menschen und keine Messung (`-T report=false` schaltet ihn
ab). Beide Verzeichnisse stehen in `.gitignore`: Ein Lauf hinterlässt Ergebnisse, kein
Repo-Inhalt.

Beide liegen relativ zum Arbeitsverzeichnis. Wer seine Läufe außerhalb des
Repos sammeln will, setzt die Ziele fest, am einfachsten in der `.env`:

```
INSPECT_LOG_DIR=/pfad/zu/bench-results/logs
GERMAN_SEO_BENCH_REPORT_DIR=/pfad/zu/bench-results/reports
INSPECT_EVAL_LOG_FILE_PATTERN={task}_{model}_{id}
```

Die letzte Zeile nimmt das Modell in den Dateinamen auf:
`2026-10-18T10-55-00-00-00_m01-titles_local-gemma4-12b-think-off_<id>.eval`
statt `…_m01-titles_<id>.eval`. Den Zeitstempel setzt Inspect immer an den
Anfang, das Muster bestimmt nur den Rest. Alles, was hier Logs auswählt
(`--glob '*m01-titles*'`), passt auf beide Schemata.

**Wo die Laufzeit steht.** Am Ende des Laufs nennt Inspect sie auf der Konsole
(`total time:`); im Log steht sie zweimal, je Lauf und je Datensatz:

```bash
uv run inspect log dump logs/….eval | jq '.stats | {started_at, completed_at}'
uv run inspect log dump logs/….eval | jq '.samples[] | {id, total_time, working_time}'
```

`total_time` ist die Wanduhr des Datensatzes, `working_time` zieht das Warten
ab (Backoff, Nebenläufigkeit). Bei einem Lauf gegen den eigenen Server liegen
beide dicht beieinander; wo sie auseinanderlaufen, hat der Endpunkt gebremst und
nicht das Modell. Bei A01 steht in
`samples[].scores.audit_scorer.metadata.report` zusätzlich, wie viel davon auf
den Kundenbericht entfiel (`seconds`, `tokens`) — ohne das sähe die Zugabe wie
Modellzeit aus. Dieselben Felder liegen in Python unter `log.stats` und
`log.samples[i].total_time` (`from inspect_ai.log import read_eval_log`).

Der Endpunkt steht dort zweimal, und die beiden Angaben sind nicht dasselbe:

```
metadata.endpoint    wohin gefragt wird — aufgelöste Basis-URL, VOR der Anfrage
metadata.server      was dort lief — /props und /running, NACH der Antwort
```

Die Reihenfolge ist der ganze Zweck. `endpoint` steht auch dann in der Datei,
wenn nie eine Antwort kam — sonst ließe eine gescheiterte Messung offen, ob der
Server aus war oder die Adresse falsch. `server` kann es umgekehrt nicht
früher geben: llama-swap lädt das Modell erst mit der ersten Anfrage.

Inspects eigenes Feld `eval.model_base_url` ersetzt `endpoint` nicht — es hält
nur `--model-base-url` fest und ist leer, sobald die Adresse aus
`LOCAL_BASE_URL` oder aus der Voreinstellung kam.

```bash
uv run inspect view                  # im Browser
uv run inspect log dump logs/….eval  # als JSON
```

Damit lässt sich jede Zahl nachrechnen, ohne den Lauf zu wiederholen.

## Läufe zusammenfassen

```bash
uv run python -m german_seo_bench.report                          # alle Logs in ./logs
uv run python -m german_seo_bench.report logs --since 2026-11-01T09:00
uv run python -m german_seo_bench.report logs --glob '*m01-titles*' --out auswertung
```

Das Ergebnis ist eine Tabelle: eine Zeile pro Modell, eine Spalte pro Aufgabe,
dazu der Gesamtscore. Läuft ein Modell in mehreren Denkmodi, bekommt jede
Aufgabe eine Spalte pro Modus (siehe „Denkmodi“ unten). Ohne `--out` erscheint sie nur im Terminal. Mit `--out`
werden zusätzlich diese Dateien geschrieben: `report.md` (Pipe-Tabelle für
Hugo, dahinter die Laufdaten und die Einzelergebnisse), `report.html` (derselbe
Text als eigenständige Seite, oben `matrix.svg`), `report.json` (jede Zelle mit
Einzelwerten, die Laufdaten unter `run_stats` und die Liste der ausgewerteten
Dateien), `overall.svg` und
`matrix.svg`, dazu je Aufgabe `bars-<aufgabe>.svg` mit einem Balken je Modell,
nach Score sortiert. Steht hinter jedem Modellnamen ein Denkmodus, nennt der Titel
jeder Grafik ihn: „ohne Denken“, „mit Denken“ oder „mit und ohne Denken“. Welche
Einstellung genau dahintersteht, etwa die Denkgrenze, steht nicht in der Grafik.
Die SVGs wechseln selbst zwischen hell und dunkel, auch wenn sie
als `<img>` eingebunden sind. `auswertung/`
steht wie `logs/` und `reports/` in `.gitignore`: Was man aus Läufen errechnet,
ist ein Ergebnis und kein Inhalt des Repos.

**Score gegen Dateigröße.** Mit `--model-sizes` entstehen zusätzlich
`scatter.svg` (Gesamtscore) und je Aufgabe `scatter-<aufgabe>.svg`: der Score
nach oben, die Dateigröße des Modells nach rechts, logarithmisch. Die Größe
steht in keinem Log, sie kommt aus einer CSV mit den Spalten `modell` und
`dateigroesse_gb`:

```
modell;dateigroesse_gb;quantisierung
local/gemma4-12b;7,9;Q5_K_M
local/gemma4-26b-a4b;16;UD-Q4_K_M
```

Getrennt wird mit Semikolon oder Komma, mit Semikolon darf das Dezimalzeichen
ein Komma sein. Weitere Spalten werden übergangen. Der Name ist der aus der
Tabelle, bei Denkmodi also ohne `-think-…`. Modelle ohne Eintrag, etwa
Cloud-Modelle, stehen als gestrichelte Linie in Höhe ihres Scores im Diagramm.

Die Dateigröße und nicht die Parameterzahl, weil sie entscheidet, ob ein
Modell auf eine Maschine passt. Sie enthält die Quantisierung, und bei
Mixture-of-Experts-Modellen zählen alle Experten, die ja ebenfalls im
Speicher liegen müssen.

```bash
uv run python -m german_seo_bench.report logs --out auswertung --model-sizes modelle.csv
```

**Laufdaten.** Hinter der Tabelle steht, was ein Lauf gekostet hat und unter
welchen Bedingungen er lief. Keine dieser Zahlen geht in einen Score ein, und
alle stehen schon im Log; neu laufen muss dafür nichts.

| Angabe | Quelle im Log | Gesamtspalte |
|---|---|---|
| Sampling | `metadata.server.params` (llama-server `/props`), überschrieben von Werten des Laufs (`--temperature` usw.) | — |
| Laufzeit je Lauf | Start bis Ende des Laufs, Median über die Läufe | Summe: ein Durchgang durch alle Aufgaben |
| Generierung, Prompt-Verarbeitung (Tokens/s) | `timings` in der Rohantwort von llama-server, Summe Tokens durch Summe Zeit | über alle Aufgaben |
| Größter Kontext | größtes `usage.total_tokens` eines Modellaufrufs | Maximum: so groß muss `--ctx-size` mindestens sein |

- Die Rohantwort hebt Inspect nur für die ersten fünf Modellaufrufe eines
  Datensatzes auf. Bei A01 stammen die Tokens/s deshalb aus den ersten
  Schritten des Agenten, mit noch kurzem Kontext; solche Zellen tragen ein ¹.
  Der Kontext stützt sich immer auf alle Aufrufe.
- ² heißt: Ein Aufruf hat das Kontextfenster des Servers (`n_ctx`) ganz
  gefüllt. Der wirkliche Bedarf liegt dann darüber.
- Tempo und Laufzeit hängen an Rechner, Serverbuild und Last zum Zeitpunkt des
  Laufs. Laufen Aufgaben eines Modells an verschiedenen Tagen, können ihre
  Raten deutlich auseinanderliegen.
- Ohne llama-server (Cloud-Modelle) bleiben Sampling und Tokens/s leer (—).

**Einzelergebnisse.** Hinter der Tabelle steht je Aufgabe und Datensatz die
Musterlösung und darunter jede Antwort mit Score und Begründung, so wie der
Scorer sie ins Log geschrieben hat. Über jedem Datensatz steht, was man zum
Beurteilen braucht: bei M01 die `<h1>` des Artikels und die Pflichtphrasen,
bei M02 die Frage mit der richtigen Option, bei M03 der Produkttext, bei M10
der Link auf die Pixabay-Seite des Bildes mit Motiv- und Kontextliste. Dazu
kommt das Mittel über alle Antworten, an dem man Datensätze erkennt, die für alle Modelle schwer sind.

- Die Zeilen folgen der Reihenfolge der Tabelle. Gleiche Antworten eines
  Modells über mehrere Läufe stehen als eine Zeile mit „(×n)“ da; eine
  abweichende Antwort bekommt eine eigene.
- Antworten erscheinen wörtlich: `**Titel**` steht mit Sternchen da und nicht
  fett, ein Zeilenumbruch als `↵`. Genau das sind die Fehler, die `format`
  meldet. Bei M01 und M10 steht die Zeichenzahl daneben („(63 Z.)“). Über 600
  Zeichen wird gekürzt.
- Die Bilder von M10 liefert der Bericht nicht mit, er verlinkt die
  Pixabay-Seite aus `data/media.json`. So lässt er sich veröffentlichen, ohne
  fremde Bilder weiterzuverteilen.
- In `report.md` und `report.html` ist der Kopf jeder Aufgabenspalte der
  Scoretabellen ein Link auf die Einzelergebnisse der Aufgabe. Die Sprungziele
  bildet der Bericht wie GitHub (`m01_titles--einzelergebnisse`), so führt
  derselbe Link auf GitHub und in der HTML-Seite an dieselbe Stelle.

**Denkmodi.** Der Modus steht im Modellnamen, als Endung `-think-<variante>`
(`local/gemma4-31b-think-off`, `local/qwen3.8-27b-think-medium`,
`local/qwen3.5-9b-think-on-precise`), so wie llama-swap die Varianten eines
Modells anlegt. Die Variante folgt dem Chat-Template des Modells
(`enable_thinking`: on/off, `reasoning_effort`: none bis xhigh); der Bericht
unterscheidet davon nur zwei Modi:

| Endung | Modus |
|---|---|
| `-think-off`, `-think-none` (auch mit Zusatz, `-think-off-precise`) | aus |
| jede andere `-think-…` (`on`, `low`, `medium`, `high`, `xhigh`, `on-precise`) | an |

**Cloud-Modelle** heißen mit und ohne Denken gleich (`anthropic/claude-haiku-5-5`).
Bei einem Namen ohne Endung ergänzt der Bericht sie deshalb aus dem Log:

| Im Log | Name im Bericht | Modus |
|---|---|---|
| `--reasoning-effort none` | `…-think-none` | aus |
| `--reasoning-effort low` … `xhigh` | `…-think-low` usw. | an |
| keine Angabe, Claude ab 4.7 (`claude-opus-4-7`, `claude-sonnet-5`, `claude-haiku-5-5` …) | `…-think-adaptive` | an |
| keine Angabe, sonst | unverändert | ? |

Claude ab 4.7 denkt ohne Angabe adaptiv, das ist die Vorgabe der API. Bei
anderen Anbietern steht die Vorgabe nirgends im Log, also nimmt der Bericht
keine an. Nicht jedes Claude-Modell lässt sich abschalten; welches was tut,
steht unter „Denken bei Cloud-Modellen“ oben. Lokale Aliase behalten ihre eigene
Endung, auch wenn zusätzlich `--reasoning-effort` gesetzt ist.

**Denkgrenze.** llama-server begrenzt das Denken auf n Tokens je Antwort, wenn
die Anfrage `thinking_budget_tokens` enthält. Bei einem Agenten wie A01 gilt
das für jeden Schritt, eine Grenze für den ganzen Lauf gibt es nicht:

```bash
uv run inspect eval … --model local/gemma4-12b-think-on --extra-body '{"thinking_budget_tokens": 512}'
```

Inspect hält den Anfragerumpf im Log fest, und der Bericht hängt die Grenze
als Zusatz an den Modus an (`local/gemma4-12b-think-on-budget512`, Modus „an“).
Die Startoption `llama-server --reasoning-budget` darf dafür nicht gesetzt
sein, auch nicht als Makro in llama-swap: Dann übergeht der Server das Feld,
und im Log stünde eine Grenze, die nicht galt.

Läufe mit und ohne Grenze oder mit verschiedenen Grenzen fallen in dieselbe
Spalte, und der Bericht bricht ab wie bei `think-low` und `think-high`. Ohne
die Angabe sähe er den Unterschied nicht und nähme den schlechteren Lauf über
beide Einstellungen. Bei „aus“ (`-think-off`, `-think-none`) entfällt der
Zusatz, denn ohne Denken greift keine Grenze. `-1` heißt unbegrenzt und zählt
wie keine Angabe. 0 oder ein anderer Wert, der keine ganze Zahl ist, bricht ab.

**Denken in den Einzelaufgaben: mit Grenze, 512 Tokens.** M01 bis M10 stehen
für Massenarbeit: Hunderte Titel oder ALT-Texte aus einem Crawl. Ein Modell,
das für einen Titel minutenlang nachdenkt, taugt dafür nicht, wie gut der
Titel am Ende auch wird. Für diese Aufgaben gilt deshalb als Messbedingung:
Denken mit höchstens 512 Tokens je Antwort
(`--extra-body '{"thinking_budget_tokens": 512}'`). Wer Ergebnisse
vergleichen will, nimmt denselben Wert. Inspect allein setzt ihn nicht, ohne
die Angabe läuft das Denken unbegrenzt.

- **Ein harter Schnitt, mit Absicht.** Was ein Modell daraus macht, gehört zur
  Messung: rechtzeitig zum Ende kommen, in der Antwort weiterdenken, bis
  `max_tokens` greift, oder ein eigenes `</think>` vor die Antwort schreiben.
  Der Benchmark hilft nicht nach, etwa mit einer Abschlussnachricht über
  `--reasoning-budget-message`. Gemessen werden soll, was ein Modell bei
  üblicher Nutzung leistet, nicht, wie weit man ihm mit Kniffen helfen kann.
- **Eine Setzung, keine Vorgabe des Modells.** Ohne Grenze denken manche
  Modelle für einen Titel mit 54 Zeichen im Mittel 2700 Tokens, einzelne
  Antworten laufen in Schleifen bis `max_tokens`. Wer Denken unbegrenzt laufen
  lässt, misst eine andere Bedingung, und der Bericht führt sie getrennt.
- **Kleine Modelle scheitern an dieser Grenze, und das ist ein Befund.**
  qwen3.5-4b dachte in 17 von 29 Titeln nach dem Schnitt in der Antwort
  weiter, bis `max_tokens` griff, qwen3.5-9b in 16 von 29. Solche Titel
  scheitern. Ein größeres Budget wäre trotzdem keine übliche Nutzung: Wer auf
  ein so kleines Modell angewiesen ist, betreibt es meist auf langsamer
  Hardware, und dort dauern schon 512 Denk-Tokens je Antwort spürbar. Für
  diese Modelle ist der Vergleich mit „aus“ der aufschlussreichere.
- **Cloud-Modelle laufen nicht unter derselben Grenze.** `thinking_budget_tokens`
  versteht nur llama-server. Claude denkt bei „an“ adaptiv und ohne diese
  Grenze. Steht es neben einem lokalen Modell mit `-budget512`, sind das zwei
  verschiedene Bedingungen, der Name im Bericht zeigt es.
- **A01 ist davon ausgenommen.** Ein Agent soll gründlich prüfen, und dort
  gibt der Benchmark keine Grenze vor. Wer eine setzt, sieht sie im Bericht wie
  oben als Zusatz.

Trägt mindestens ein Name eine solche Endung, steht vorn eine zusammengefasste
Tabelle: eine Zeile je Modell (`local/qwen3.8-27b`), je Aufgabe eine Spalte pro
gelaufenem Modus („m03_facts · aus“, „m03_facts · an“), in jeder Aufgabe in
derselben Reihenfolge: aus, an, ?. Ein Name ohne Endung
steht unter „?“ (Modus nicht angegeben). Laufen zwei Varianten desselben
Modells dieselbe Aufgabe im selben Modus (etwa `think-low` und `think-high`),
bricht der Bericht ab und nennt die Dateien; dann mit `--glob` eine der beiden
auswählen. Welche Variante hinter einem Modus steht, nennt eine Fußnote je
Modell („local/qwen3.5-9b: an = think-on-precise“); nimmt ein Modell im selben
Modus je Aufgabe eine andere Variante, steht die Aufgabe in Klammern dabei.
Zusammengelegt wird nur, gerechnet nicht: Jede Spalte ist genau eine Messung
und dieselbe Zahl wie in der Tabelle je vollem Namen.

- Es gibt keinen Standardmodus je Aufgabe und keinen „besten Modus“. Ein
  Standardmodus würde eine Annahme festschreiben, etwa dass Einzelfragen kein
  Nachdenken brauchen, und genau die soll sich prüfen lassen. Der beste Modus
  gäbe einem Modell mit vier Modi vier Versuche und einem mit einem Modus nur
  einen. Welche Modi laufen, entscheidet, wer die Läufe startet.
- Hat eine Aufgabe mehrere Modi, gibt es mehrere Gesamtspalten, eine je
  Kombination aus einer Messung pro Aufgabe („Gesamt [a]“, „Gesamt [b]“; die
  Kombination steht in der Fußnote). Kombinationen, die kein Modell hat,
  fallen weg; bei einem Modus je Aufgabe bleibt genau ein Gesamt. Sortiert
  wird nach der ersten Gesamtspalte, das ist die mit den meisten Modellen.
- Hat ein Modell eine Aufgabe einer Gesamtspalte nur in einem anderen Modus,
  bleibt seine Zelle dort leer („—“). Aus den übrigen Aufgaben gerechnet,
  stünde dort dieselbe Zahl wie nebenan, nur unter einer Kombination, die nie
  gemessen wurde. Fehlt die Aufgabe ganz, gilt dagegen die übliche Regel:
  Gesamt aus den vorhandenen, mit `*`.
- Die Tabelle je vollem Namen steht dahinter, sobald ein Modell mehr als einen
  Modus hat. Hat jedes nur einen, wären beide gleich, und es bleibt bei der
  zusammengefassten. `overall.svg` und `matrix.svg` zeigen die
  zusammengefasste Tabelle, `report.json` hat sie unter `combined`.

Wer eigene Modellnamen ohne diese Endung verwendet, bekommt nur die Tabelle
je Name: eine Zeile je Name.

**Ausgewählt wird über Dateien.** Ausgewertet wird jede `.eval`-Datei im
Verzeichnis, die zu `--glob` passt und mit `--since` nicht ausgeschlossen ist.
`--since` vergleicht mit `eval.created` im Log. Fehlt die Zeitzone, gilt UTC,
also dieselbe Zeit wie in den Dateinamen. Darüber hinaus wählt das Modul nichts
aus und ordnet nichts ein.

**Neu gewertet wird nichts.** Jede Zahl ist ein Score, den der Scorer der
Aufgabe selbst ins Log geschrieben hat: je Datensatz der Wert unter dem
Schlüssel des Leitwerts (`eval.headline_metric`). Ob ein Lauf
gültig ist, entscheidet allein der Scorer der Aufgabe. Eine zweite Prüfung an
dieser Stelle könnte der ersten widersprechen und würde veralten, ohne dass es
jemand merkt. Geprüft wird hier nur, was erst beim Zusammenfassen sichtbar
wird. Jeder dieser Fälle bricht ab und nennt alle betroffenen Dateien:

- Läufe einer Aufgabe mit verschiedener Aufgabenversion, verschiedenen
  Parametern (`-T`), einem anderen Ausschnitt des Datensatzes (`--limit`,
  `--sample-id`) oder einer anderen Epochenzahl (`--epochs`). Bei der
  Epochenzahl geht es um die Gewichtung: Ein Lauf mit drei Epochen stützt
  sich auf dreimal so viele Urteile, zählte gemittelt aber nur so viel wie
  einer mit einer. Jeder dieser Läufe ist für sich gültig. Ihr Mittel wäre aber
  eine Zahl über zwei verschiedene Benchmarks.
  Ausgenommen sind Parameter, die den Leitwert nicht berühren
  (`SCORE_NEUTRAL_ARGS`, heute nur `report` von A01). A01 mit und ohne
  Kundenbericht ist also vergleichbar.

**Benchmark-Version.** Unter jeder Tabelle und in der Fußzeile beider SVGs
steht, mit welcher Paketversion die Läufe entstanden sind
(„Benchmark-Version: german-seo-bench 2026.1“). Sie stammt aus
`eval.packages` im Log, in `report.json` steht sie unter
`benchmark_versions` und je Lauf als `benchmark_version`. Verschiedene Stände
bei gleicher Aufgabenversion brechen nicht ab, denn vergleichbar macht zwei
Scores die Aufgabenversion. Der Bericht nennt dann jeden Stand mit der Zahl
seiner Läufe („2026.1 (3 Läufe), 2026.2 (2 Läufe)“). Fehlt die Angabe im Log,
steht „unbekannt“. Logs aus der Zeit vor der ersten Veröffentlichung tragen
`0.1.0`; der Bericht zeigt sie als `2026.1`, derselbe Benchmark unter seinem
veröffentlichten Namen.

**Messzeitraum.** Daneben steht, in welchen Monaten die eingerechneten Läufe
gestartet wurden, etwa „Messzeitraum: August bis Oktober 2026“, in einem
einzigen Monat nur „Oktober 2026“. Gerechnet wird in UTC über genau die Läufe
der Tabelle, abgebrochene zählen nicht. Tagesgenau steht der Zeitraum in
`report.json` unter `measurement_period` (`first`, `last`). Wann ein
einzelnes Modell lief, steht dort je Lauf unter `created`.

**Abgebrochene Läufe.** Logs ohne Ergebnis (`error`, `cancelled`) lassen sich
nicht einrechnen, brechen den Bericht aber nicht ab: Ein Lauf, der an einem
hängenden Server gescheitert ist, sagt nichts über die übrigen. Jeder steht mit
Ursache, Zahl der Modellaufrufe und Laufzeit im Terminal und in `report.md`
unter „Abgebrochene Läufe“, in `report.json` unter `failed`. Als Ursache gilt
die genaueste Meldung im Log, also die des gescheiterten Modellaufrufs
(`AttemptTimeoutError("attempt_timeout '1800' exceeded.")`) statt der Hülle
`RetryError(…)`. Fehlt einem Modell dadurch eine Aufgabe, ist sein Gesamt mit
`*` markiert; ein Modell ganz ohne Ergebnis steht nur in dieser Liste. Sind alle
Läufe der Auswahl ohne Ergebnis, bricht der Bericht ab. Den ganzen Verlauf bis
zum Fehler zeigt `uv run inspect view --log-dir <verzeichnis>`. Bei einer
Zeitüberschreitung liegt die Ursache meist beim Server; dann hilft dessen Log
(llama-swap schreibt ohne eigene Einstellung nach stdout, als Dienst also ins
Journal).

**Judge-Spalten.** Liegt neben einem Log ein Sidecar (`X.judges.json`, siehe
[„Der Titel-Judge“](tasks/m01_titles.md#der-titel-judge)), kommt je Aufgabe und Judge eine Spalte dazu: der
Anteil der Titel, die der Judge als unbrauchbar einstuft, gemittelt über die
Läufe („7 %“). In `matrix.svg` stehen diese Spalten abgesetzt hinter dem
Gesamtscore. Sie gehen nie in den Gesamtscore ein. `?` heißt, der Judge hat
kein lesbares Urteil abgegeben; das zählt nicht als 0. Hat ein Judge nicht
alle Läufe einer Zelle gewertet, steht das dabei („1 von 3 Läufen“). Abgebrochen wird, wenn derselbe Judge in der
Auswahl mit verschiedenen Prompt-Versionen vorkommt oder wenn ein Sidecar zu
einem anderen Lauf gehört als das Log daneben. Beides fällt erst beim
Zusammenlesen auf.

**So wird gerechnet.** Gruppiert wird nach Aufgabe und Modell. Mehrere Läufe
derselben Gruppe gelten als Wiederholungen, und sie werden **nicht
gemittelt**: Je Datensatz zählt der schlechteste Score über alle Läufe (und
Epochen), der Wert der Zelle ist das Mittel dieser Werte über die Datensätze.
Ein Aussetzer an einem Datensatz kostet damit diesen Datensatz ganz, statt im
Mittel der Läufe unterzugehen. Ein Abzug bleibt dabei ein Abzug: Ein Titel mit
0.9 im schlechtesten Lauf zählt 0.9. Das Minimum der Laufmittel wäre die
falsche Regel: Zwei Läufe, die an verschiedenen Datensätzen scheitern,
ergäben dort 0.5 statt 0, obwohl auf keinen der beiden Verlass ist.

Je mehr Läufe in einer Zelle stecken, desto strenger ist sie. Verschiedene
Laufzahlen sind zulässig; die Zahl steht dann in der Zelle („(n=3)“). Für
Vergleiche gehören gleich viele Läufe je Modell in die Auswahl. Bei
Temperatur 0 sind alle Läufe gleich, und der schlechteste ist der einzige;
siehe [„Zuverlässigkeit statt Schwierigkeit“](architecture.md#zuverlässigkeit-statt-schwierigkeit).

Der Gesamtscore eines Modells ist das arithmetische Mittel seiner
Aufgabenwerte. Jede Aufgabe zählt also gleich, unabhängig davon, wie viele
Datensätze sie hat. Fehlt einem Modell eine Aufgabe, wird der
Gesamtscore aus den vorhandenen gebildet und mit `*` und Fußnote markiert, im
Diagramm schraffiert. Würde die fehlende Aufgabe als 0 zählen, stünde dort das
Ergebnis einer Messung, die nie stattgefunden hat. Die Rechenart steht in
`report.json` unter `aggregation`; dort steht je Zelle auch der Wert je
Datensatz (`samples`) und als Rohdaten das Mittel jedes Laufs aus dem Log
(`run_scores`). Für die Tabelle werden die Logs ganz gelesen, nicht nur ihre
Köpfe.
