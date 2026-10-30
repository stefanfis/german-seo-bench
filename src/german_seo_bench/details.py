"""Einzelergebnisse für den Bericht: jede Antwort neben ihrer Wertung.

Die Tabelle in `report.py` sagt, wie gut ein Modell ist; erst die einzelnen
Antworten sagen, warum. Dieser Teil des Berichts zeigt je Aufgabe und Datensatz
die Musterlösung und darunter, was jedes Modell geantwortet hat, mit Score und
Begründung des Scorers.

Wie die Tabelle **wertet er nichts neu**: Score und Begründung stehen so da,
wie der Scorer sie ins Log geschrieben hat. Eine zweite Rechnung hier könnte
der im Log widersprechen, und dann wüsste niemand, welcher Zahl er glauben soll.

Gruppiert wird nach Datensatz, nicht nach Modell. Die Musterlösung steht dann
einmal oben, und alle Modelle lassen sich am selben Fall vergleichen. Gleiche
Antworten eines Modells über mehrere Läufe hinweg werden zu einer Zeile mit
„(×n)“ zusammengefasst — sonst stünde bei fünf Läufen fünfmal derselbe Titel da,
und die eine abweichende Antwort ginge darin unter.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from inspect_ai.log import EvalLog, read_eval_log

from german_seo_bench.checks.choice import correct_letter
from german_seo_bench.media import manifest
from german_seo_bench.modelname import model_label

if TYPE_CHECKING:
    from german_seo_bench.report import Report

# Länger wird eine Antwort in der Tabelle nicht gezeigt. Titel, Fakten und
# ALT-Texte bleiben weit darunter; gekürzt werden vor allem ausufernde
# Antworten, die ohnehin scheitern, und die Befundlisten von A01.
MAX_ANSWER = 600

# Aufgaben mit Längenvorgabe: Dort steht die Zeichenzahl neben der Antwort,
# weil „gescheitert an: length“ sonst nicht sagt, um wie viel.
SHOW_LENGTH = frozenset({"m01_titles", "m10_alt_text"})


@dataclass(frozen=True)
class Answer:
    """Eine Antwort eines Modells auf einen Datensatz, mit ihrer Wertung aus dem Log."""

    task: str
    sample_id: str
    model: str
    answer: str
    score: float | None
    explanation: str
    metadata: dict[str, Any]


def read_answers(files: list[Path]) -> list[Answer]:
    """Alle Antworten der Logs.

    Der Score je Antwort steht unter dem Schlüssel des Leitwerts
    (`eval.headline_metric`), aus dem auch `report.read_runs` die Zahl der
    Tabelle nimmt. So zeigen Tabelle und Einzelergebnisse denselben Wert.
    """
    answers: list[Answer] = []
    for path in files:
        answers += log_answers(read_eval_log(str(path), resolve_attachments=True))
    return answers


def log_answers(log: EvalLog) -> list[Answer]:
    """Die Antworten eines vollständig gelesenen Logs (mit `resolve_attachments`)."""
    answers: list[Answer] = []
    key = log.eval.headline_metric.score if log.eval.headline_metric else "score"
    task = log.eval.task.rsplit("/", 1)[-1]
    for sample in log.samples or []:
        if not sample.scores:
            continue
        # Jede Aufgabe hat genau einen Scorer; sein Name ist je Aufgabe verschieden.
        score = next(iter(sample.scores.values()))
        value = score.value
        points = value.get(key) if isinstance(value, dict) else value
        answer = score.answer if score.answer is not None else sample.output.completion
        answers.append(
            Answer(
                task=task,
                sample_id=str(sample.id),
                model=model_label(log),
                answer=answer,
                score=float(points) if isinstance(points, (int, float)) else None,
                explanation=score.explanation or "",
                metadata=dict(sample.metadata or {}),
            )
        )
    return answers



# ---------------------------------------------------------------- Darstellung


def _literal(text: str, limit: int = MAX_ANSWER) -> str:
    """Text so, dass Markdown ihn wörtlich zeigt — auch in einer Tabellenzelle.

    Jedes Zeichen, das mitten in einer Zeile Markdown auslöst, bekommt einen
    Backslash. `**Titel**` erscheint dann mit Sternchen und nicht fett: Genau
    diesen Fehler meldet `format` in M01, und die Anzeige darf ihn nicht
    verschwinden lassen. Alle Satzzeichen zu escapen wäre ebenso sicher, machte
    aber `report.md` selbst unlesbar. Zeichen, die nur am Zeilenanfang wirken
    (`#`, `-`, `1.`), können hier nicht vorkommen: Jeder Text steht hinter
    einer Beschriftung oder in einer Tabellenzelle. Ein Umbruch wird
    zu einem sichtbaren `↵`, weil eine Tabellenzeile keinen kennt und ein
    Umbruch im Titel selbst ein Fehler ist.
    """
    if len(text) > limit:
        text = text[:limit] + f" … ({len(text)} Zeichen)"
    text = re.sub(r"([\\`*_\[\]<>|~!&])", r"\\\1", text)
    return re.sub(r"\r?\n", " ↵ ", text)


def _phrases(values: list[str] | None) -> str:
    return ", ".join(values or []) or "—"


def _mc_answer(m: dict[str, Any]) -> str:
    choices = m.get("choices") or []
    if m.get("answer") not in choices:
        return "—"
    return f"{correct_letter(m)}) {m['answer']}"


def _h1(html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else "—"


# Kurzer Kontext je Aufgabe über dem Datensatz. Nicht die ganze Eingabe: Ein
# M01-Artikel ist länger als alle Antworten darunter zusammen. Gezeigt wird,
# was man braucht, um eine Antwort zu beurteilen.
CONTEXT: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "m01_titles": lambda m: [
        f"**Überschrift:** {_literal(_h1(m.get('text', '')))}",
        f"**Pflichtphrasen:** {_literal(_phrases(m.get('required')))}",
    ],
    "m02_knowledge_mc": lambda m: [
        f"**Frage:** {_literal(m.get('question', ''))}",
        f"**Richtig:** {_literal(_mc_answer(m))}",
        *([f"**Quelle:** {_literal(m['source'])}"] if m.get("source") else []),
    ],
    "m03_facts": lambda m: [f"**Produkttext:** {_literal(m.get('text', ''), 2000)}"],
    "m10_alt_text": lambda m: [
        f"**Motiv:** {_literal(_phrases(m.get('subject')))}",
        f"**Zusammenhang:** {_literal(_phrases(m.get('context')))}",
    ],
}


def _image(answers: list[Answer]) -> str | None:
    """Der Verweis auf die Pixabay-Seite des Bildes eines Datensatzes.

    Der Bericht liefert die Bilder nicht mit, aus demselben Grund wie das Repo
    (siehe `german_seo_bench.media`): Er soll sich veröffentlichen lassen, ohne
    fremde Bilder weiterzuverteilen. Die Seite nennt auch den Uploader.
    """
    name = answers[0].metadata.get("image")
    if not name:
        return None
    entry = next((e for e in manifest() if e.path == name), None)
    if entry is None:
        return f"**Bild:** {_literal(name)} (nicht im Manifest)"
    return f"**Bild:** [{_literal(Path(name).name)} bei Pixabay]({entry.page_url}) von {_literal(entry.author)}"


def details_heading(task: str) -> str:
    return f"{task} — Einzelergebnisse"


def anchor(heading: str) -> str:
    """Sprungziel einer Überschrift, so wie GitHub es bildet.

    Kleinbuchstaben, Satzzeichen außer `-` und `_` fallen weg, Leerzeichen
    werden zu `-`: „m01_titles — Einzelergebnisse“ wird zu
    `m01_titles--einzelergebnisse`. `render_html` vergibt dieselben Ziele, so
    funktioniert ein Link aus `report.md` auf GitHub wie in `report.html`.
    """
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def _score_text(score: float | None) -> str:
    return "—" if score is None else f"{score:.2f}"


def render_details(report: Report, answers: list[Answer]) -> str:
    """Der Markdown-Teil mit den Einzelergebnissen."""
    rank = {row.model: i for i, row in enumerate(report.rows)}
    parts: list[str] = []
    for task in report.tasks:
        mine = [a for a in answers if a.task == task.name]
        if not mine:
            continue
        parts.append(f"## {details_heading(task.name)}\n")
        for sample_id in dict.fromkeys(a.sample_id for a in mine):
            group = [a for a in mine if a.sample_id == sample_id]
            scored = [a.score for a in group if a.score is not None]
            mean = f"  (Mittel über {len(scored)} Antworten: {statistics.fmean(scored):.2f})" if scored else ""
            parts.append(f"### {_literal(sample_id)}{mean}\n")
            meta = group[0].metadata
            lines = CONTEXT.get(task.name, lambda m: [])(meta)
            if image := _image(group):
                lines.insert(0, image)
            oracle = meta.get("oracle")
            if oracle is not None:
                lines.append(f"**Musterlösung:** {_literal(str(oracle))}")
            # Zwei Leerzeichen am Zeilenende: ein harter Umbruch in CommonMark.
            parts.append("  \n".join(lines) + "\n")
            parts.append(_answer_table(group, rank, task.name in SHOW_LENGTH))
    return "\n".join(parts)


def _answer_table(group: list[Answer], rank: dict[str, int], length: bool) -> str:
    rows: dict[tuple[str, str, float | None, str], int] = {}
    for a in sorted(group, key=lambda a: rank.get(a.model, len(rank))):
        key = (a.model, a.answer, a.score, a.explanation)
        rows[key] = rows.get(key, 0) + 1
    lines = ["| Modell | Antwort | Score | Bewertung |", "| :--- | :--- | ---: | :--- |"]
    for (model, answer, score, explanation), count in rows.items():
        label = model + (f" (×{count})" if count > 1 else "")
        shown = _literal(answer) if answer.strip() else "*(leer)*"
        if length and answer.strip():
            shown += f" ({len(answer)} Z.)"
        lines.append(
            f"| {_literal(label)} | {shown} | {_score_text(score)} | {_literal(explanation)} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- HTML

HTML_CSS = """
:root { color-scheme: light dark; --bg:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --line:#e4e3df; --head:#f1f0ec; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1a1a19; --ink:#f4f4f2; --ink2:#c3c2b7; --line:#383835; --head:#242422; }
}
body { background:var(--bg); color:var(--ink); margin:0 auto; padding:24px 16px; max-width:1400px;
       font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h2 { margin-top:2.5em; border-bottom:2px solid var(--line); padding-bottom:4px; }
h3 { margin-top:2em; }
table { border-collapse:collapse; width:100%; margin:8px 0 16px; }
th, td { border:1px solid var(--line); padding:4px 8px; vertical-align:top; text-align:left; }
th { background:var(--head); }
td:first-child { white-space:nowrap; }
td:nth-child(3) { white-space:nowrap; font-variant-numeric:tabular-nums; }
p { color:var(--ink2); }
img { max-width:320px; height:auto; border-radius:4px; }
img.matrix { max-width:100%; }
"""


def _heading_ids(body: str) -> str:
    """Jede Überschrift bekommt ihr Sprungziel nach `anchor`.

    Gleiche Überschriften zählt GitHub mit `-1`, `-2` … durch; hier ebenso,
    damit ein Ziel auf beiden Seiten dieselbe Überschrift trifft.
    """
    seen: dict[str, int] = {}

    def add_id(match: re.Match[str]) -> str:
        level, inner = match.group(1), match.group(2)
        base = anchor(unescape(re.sub(r"<[^>]+>", "", inner)))
        count = seen.get(base, 0)
        seen[base] = count + 1
        target = base if count == 0 else f"{base}-{count}"
        return f'<h{level} id="{escape(target)}">{inner}</h{level}>'

    return re.sub(r"<h([1-6])>(.*?)</h\1>", add_id, body)


def render_html(markdown: str, matrix: str | None = "matrix.svg") -> str:
    """Derselbe Bericht als eigenständige HTML-Seite.

    `html=False`: Was ein Modell geantwortet hat, ist Text und kein Markup. Ein
    `<script>` in einer Antwort soll hier als Zeichen erscheinen.
    """
    from markdown_it import MarkdownIt

    body = _heading_ids(MarkdownIt("commonmark", {"html": False}).enable(["table"]).render(markdown))
    figure = f'<img class="matrix" src="{escape(matrix)}" alt="Score je Aufgabe und Modell">\n' if matrix else ""
    return (
        '<!doctype html>\n<html lang="de">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>german-seo-bench: Auswertung</title>\n<style>{HTML_CSS}</style>\n</head>\n<body>\n"
        f"<h1>Auswertung</h1>\n{figure}{body}</body>\n</html>\n"
    )
