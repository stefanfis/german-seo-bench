"""Zusicherungen über die Einzelergebnisse im Bericht (`german_seo_bench.details`).

Wie in `test_report.py` gegen echte Logs aus Oracle-Läufen und Läufen mit
fester Antwort. Geprüft wird vor allem, dass die Anzeige nichts verfälscht:
Score und Begründung kommen unverändert aus dem Log, und eine Antwort mit
Markdown darin erscheint wörtlich statt formatiert.
"""

import re
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from german_seo_bench import details
from german_seo_bench.details import _literal, anchor, read_answers, render_details, render_html
from german_seo_bench.media import manifest
from german_seo_bench.report import build, main, render_markdown
from german_seo_bench.tasks.m01_titles import m01_titles
from german_seo_bench.tasks.m03_facts import m03_facts
from german_seo_bench.tasks.m10_alt_text import m10_alt_text

LIMIT = 2
MARKDOWN_TITLE = "**Golf** | lernen\nzweite Zeile"


@solver
def _fixed(content: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="fixed", content=content)
        state.messages.append(state.output.message)
        return state

    return solve


def _run(task, model: str, log_dir: Path, **kwargs) -> Path:
    log = inspect_eval(task, model=model, log_dir=str(log_dir), limit=LIMIT,
                       display="none", **kwargs)[0]
    assert log.status == "success", log.error
    return Path(log.location)


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> Path:
    """alpha: zweimal die Musterlösung. beta: einmal ein Titel mit Markdown und Umbruch.

    M03 einmal mit Musterlösung, einmal falsch, beide auf alpha und beta,
    damit beide Aufgaben vollständig sind.
    """
    target = tmp_path_factory.mktemp("detail-logs")
    _run(m01_titles(oracle=True), "mockllm/alpha", target)
    _run(m01_titles(oracle=True), "mockllm/alpha", target)
    _run(m01_titles(oracle=True), "mockllm/beta", target, solver=_fixed(MARKDOWN_TITLE))
    _run(m03_facts(oracle=True), "mockllm/alpha", target)
    _run(m03_facts(oracle=True), "mockllm/beta", target, solver=_fixed('{"name": "Falsch"}'))
    return target


@pytest.fixture(scope="module")
def out(log_dir: Path, tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("detail-out")
    assert main([str(log_dir), "--out", str(target)]) == 0
    return target


@pytest.fixture(scope="module")
def markdown(out: Path) -> str:
    return (out / "report.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html(out: Path) -> str:
    return (out / "report.html").read_text(encoding="utf-8")


def _section(markdown: str, task: str) -> str:
    start = markdown.index(f"## {task} — Einzelergebnisse")
    end = markdown.find("\n## ", start + 1)
    return markdown[start:end if end != -1 else None]


class TestInhalt:
    def test_tabelle_steht_vorne(self, markdown: str) -> None:
        assert markdown.startswith("| Modell | Gesamt |")
        assert markdown.index("| Modell | Gesamt |") < markdown.index("Einzelergebnisse")

    def test_je_datensatz_ein_abschnitt_mit_musterloesung(self, log_dir: Path, markdown: str) -> None:
        log = read_eval_log(str(next(log_dir.glob("*m01-titles*.eval"))))
        section = _section(markdown, "m01_titles")
        for sample in log.samples:
            assert f"### {sample.id}" in section
            assert f"**Musterlösung:** {_literal(sample.metadata['oracle'])}" in section

    def test_m01_zeigt_ueberschrift_und_pflichtphrasen(self, markdown: str) -> None:
        section = _section(markdown, "m01_titles")
        assert section.count("**Überschrift:**") == LIMIT
        assert section.count("**Pflichtphrasen:**") == LIMIT

    def test_gleiche_antworten_werden_zusammengefasst(self, markdown: str) -> None:
        section = _section(markdown, "m01_titles")
        assert section.count("| mockllm/alpha (×2) |") == LIMIT
        # Gegenprobe: Ohne Zusammenfassung stünde alpha je Datensatz zweimal da.
        assert "| mockllm/alpha |" not in section

    def test_verschiedene_antworten_bleiben_getrennt(self, log_dir: Path) -> None:
        files = sorted(log_dir.glob("*m01-titles*.eval"))
        report = build(log_dir, "*m01-titles*")
        answers = read_answers(files)
        # Eine der beiden alpha-Antworten auf denselben Datensatz verändert.
        first = next(i for i, a in enumerate(answers) if a.model == "mockllm/alpha")
        changed = answers[first].__class__(**{**vars(answers[first]), "answer": "Anderer Titel"})
        answers[first] = changed
        text = render_details(report, answers)
        assert "| mockllm/alpha (×2) |" in text  # der andere Datensatz
        assert "Anderer Titel" in text
        assert text.count("| mockllm/alpha |") == 2

    def test_score_und_begruendung_kommen_aus_dem_log(self, log_dir: Path, markdown: str) -> None:
        log = read_eval_log(str(next(log_dir.glob("*m03-facts*.eval"))))
        section = _section(markdown, "m03_facts")
        wrong = [line for line in section.splitlines() if line.startswith("| mockllm/beta |")]
        assert len(wrong) == LIMIT
        for path in log_dir.glob("*m03-facts*.eval"):
            log = read_eval_log(str(path))
            if log.eval.model != "mockllm/beta":
                continue
            for sample in log.samples:
                score = sample.scores["facts_scorer"]
                row = f"| {score.value['score']:.2f} | {_literal(score.explanation)} |"
                assert any(row in line for line in wrong)

    def test_mittel_je_datensatz(self, markdown: str) -> None:
        # M03: alpha richtig (1), beta falsch — das Mittel liegt dazwischen.
        means = re.findall(r"Mittel über 2 Antworten: ([0-9.]+)", _section(markdown, "m03_facts"))
        assert len(means) == LIMIT
        assert all(0 < float(m) < 1 for m in means)

    def test_m01_nennt_die_laenge(self, markdown: str) -> None:
        assert f"({len(MARKDOWN_TITLE)} Z.)" in _section(markdown, "m01_titles")
        # Gegenprobe: M03 hat keine Längenvorgabe, also keine Zeichenzahl.
        assert " Z.) |" not in _section(markdown, "m03_facts")


class TestWoertlich:
    def test_markdown_in_der_antwort_bleibt_sichtbar(self, html: str) -> None:
        assert "**Golf** | lernen" in html
        assert "<strong>Golf</strong>" not in html

    def test_gegenprobe_ohne_escaping_waere_fett(self) -> None:
        raw = render_html(f"| a |\n| - |\n| {MARKDOWN_TITLE.splitlines()[0]} |\n", matrix=None)
        assert "<strong>Golf</strong>" in raw

    def test_umbruch_wird_sichtbar(self, markdown: str) -> None:
        assert "lernen ↵ zweite Zeile" in markdown

    def test_tabellen_bleiben_heil(self, html: str) -> None:
        # Der `|` im Titel darf keine zusätzliche Spalte aufmachen. Nur die
        # Einzelergebnisse: Score- und Laufdatentabellen davor sind anders breit.
        details = html[html.index("Einzelergebnisse</h2>"):]
        for row in re.findall(r"<tr>(.*?)</tr>", details, re.S):
            assert row.count("<td") in (0, 4)

    def test_html_wird_nicht_ausgefuehrt(self, tmp_path: Path) -> None:
        text = render_html(_literal('<script>alert(1)</script>'), matrix=None)
        assert "<script>alert" not in text
        assert "&lt;script&gt;" in text

    def test_lange_antworten_werden_gekuerzt(self) -> None:
        text = _literal("x" * 1000)
        assert text.startswith("x" * 600 + " … (1000 Zeichen)")


class TestSprungziele:
    @pytest.mark.parametrize(
        ("heading", "expected"),
        [
            ("m01_titles — Einzelergebnisse", "m01_titles--einzelergebnisse"),
            ("Je Modell, Denkmodi als Spalten", "je-modell-denkmodi-als-spalten"),
            ("Größter Kontext (Tokens)", "größter-kontext-tokens"),
        ],
    )
    def test_anchor_wie_github(self, heading: str, expected: str) -> None:
        assert anchor(heading) == expected

    def test_kopf_verweist_auf_einzelergebnisse(self, markdown: str) -> None:
        header = next(line for line in markdown.splitlines() if line.startswith("| Modell |"))
        for task in ("m01_titles", "m03_facts"):
            assert f"[{task}](#{task}--einzelergebnisse)" in header
            assert f"## {task} — Einzelergebnisse" in markdown

    def test_jeder_link_hat_ein_ziel_im_html(self, html: str) -> None:
        targets = re.findall(r'href="#([^"]+)"', html)
        assert {"m01_titles--einzelergebnisse", "m03_facts--einzelergebnisse"} <= set(targets)
        ids = re.findall(r'id="([^"]+)"', html)
        assert len(ids) == len(set(ids))
        assert set(targets) <= set(ids)

    def test_gleiche_ueberschriften_werden_durchgezaehlt(self) -> None:
        html = render_html("### a\n\n### a\n\n### a\n", matrix=None)
        assert re.findall(r'<h3 id="([^"]+)">', html) == ["a", "a-1", "a-2"]

    def test_ohne_links_kein_verweis(self, log_dir: Path) -> None:
        # Für Tabellen, die allein weiterverwendet werden, etwa auf einer Website.
        assert "](#" not in render_markdown(build(log_dir))


class TestDateien:
    def test_html_neben_markdown(self, out: Path) -> None:
        # Die Balken je Aufgabe prüft test_report.py.
        names = sorted(p.name for p in out.iterdir() if not p.name.startswith("bars-"))
        assert names == ["matrix.svg", "overall.svg", "report.html", "report.json", "report.md"]

    def test_html_bindet_die_matrix_ein(self, html: str) -> None:
        assert 'src="matrix.svg"' in html


class TestBilder:
    @pytest.fixture(scope="class")
    @classmethod
    def m10_logs(cls, media_present, tmp_path_factory) -> Path:
        logs = tmp_path_factory.mktemp("m10-logs")
        _run(m10_alt_text(), "mockllm/alpha", logs)
        return logs

    @pytest.fixture(scope="class")
    @classmethod
    def m10_out(cls, m10_logs: Path, tmp_path_factory) -> Path:
        out = tmp_path_factory.mktemp("m10-out")
        assert main([str(m10_logs), "--out", str(out)]) == 0
        return out

    def test_link_auf_pixabay(self, m10_out: Path) -> None:
        text = (m10_out / "report.md").read_text(encoding="utf-8")
        pages = {e.path: e for e in manifest()}
        shown = re.findall(r"\*\*Bild:\*\* \[(\d+)\.jpg bei Pixabay\]\((\S+?)\) von (\S+)", text)
        assert len(shown) == LIMIT
        for number, url, author in shown:
            entry = pages[f"m10_alt_text/{number}.jpg"]
            assert url == entry.page_url
            # `_literal` maskiert den Unterstrich, damit Markdown nicht kursiv setzt.
            assert author.replace("\\_", "_") == entry.author

    def test_link_im_html(self, m10_out: Path) -> None:
        html = (m10_out / "report.html").read_text(encoding="utf-8")
        assert html.count('href="https://pixabay.com/photos/') == LIMIT

    def test_keine_bilder_neben_dem_bericht(self, m10_out: Path) -> None:
        assert not (m10_out / "images").exists()
        assert not any(p.suffix in (".jpg", ".png") for p in m10_out.rglob("*"))
        # Das einzige Bild im HTML ist die Matrix.
        assert (m10_out / "report.html").read_text(encoding="utf-8").count("<img") == 1

    def test_bild_nicht_im_manifest(self, m10_logs: Path, monkeypatch) -> None:
        answers = read_answers(sorted(m10_logs.glob("*.eval")))
        monkeypatch.setattr(details, "manifest", lambda: [])
        assert "(nicht im Manifest)" in details._image(answers[:1])
