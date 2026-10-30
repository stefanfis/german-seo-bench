# Ergebnisse

Messungen, die ich selbst gefahren habe, je Benchmark-Version und Denkmodus ein
Verzeichnis. Darin steht der Bericht so, wie
`python -m german_seo_bench.report` ihn mit `--out` schreibt: `report.md` mit
Tabellen, Laufdaten und jeder einzelnen Antwort, dazu die Grafiken.

Je Modell und Datensatz zählt der **schlechteste** Lauf, siehe
[„So wird gerechnet“](../docs/running.md#läufe-zusammenfassen). Ohne Denken
lief jedes Modell dreimal. Mit Denken sind es je Modell und Aufgabe
verschieden viele Läufe, die Zahl steht dort in jeder Zelle. Ein einzelner
Lauf wird milder gewertet als der schlechteste von dreien.

Erklärungen und Einordnung je Aufgabe stehen auf
[fischerlaender.de/lab/benchmarks](https://fischerlaender.de/lab/benchmarks/).

## 2026.1, ohne Denken

![Gesamtscore ohne Denken](2026.1/think-off/overall.svg)

[Bericht mit allen Antworten](2026.1/think-off/report.md)

## 2026.1, mit Denken

![Gesamtscore mit Denken](2026.1/think-on/overall.svg)

[Bericht mit allen Antworten](2026.1/think-on/report.md)
