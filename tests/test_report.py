"""Zusicherungen über `german_seo_bench.report`, gegen echte Logs.

Die Logs entstehen hier aus Oracle-Läufen und Läufen mit fester, falscher
Antwort — mit `mockllm/<name>`, damit es mehrere Modelle gibt, ohne dass ein
Server läuft. Nachgebaute Logs wären bequemer, prüften aber nur, ob das Modul
zu seiner eigenen Vorstellung von einem Log passt, nicht zu dem, was Inspect
schreibt.

Jede Rechenregel hat ihre Gegenprobe in Form einer Zahl, die bei der
naheliegenden falschen Regel herauskäme:

- Je Datensatz zählt der schlechteste Lauf, nicht das Mittel der Läufe
  (0.00 statt 0.50).
- Scheitern zwei Läufe an verschiedenen Datensätzen, ist die Zelle 0 — das
  Minimum der Laufmittel wäre 0.50.
- Gesamt je Modell ist das Mittel der Aufgaben, nicht der Läufe
  (0.50 statt 0.667).
- Eine fehlende Aufgabe fällt heraus und zählt nicht als 0 (1.00 statt 0.50).
"""

import json
import shutil
from importlib.metadata import version
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.event import ModelEvent
from inspect_ai.model import GenerateConfig, ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from german_seo_bench.report import (
    Cell,
    ModelRow,
    Report,
    ReportError,
    Run,
    Task,
    build,
    check_consistency,
    combined_matrix,
    comparable_args,
    main,
    period_text,
    read_failure,
    render_failed,
    render_json,
    render_markdown,
    read_model_sizes,
    render_svg_matrix,
    render_svg_overall,
    render_svg_scatter,
    render_svg_task,
    render_terminal,
    select_logs,
    split_model,
    write_outputs,
)
from german_seo_bench.tasks.m01_titles import m01_titles
from german_seo_bench.tasks.m02_knowledge_mc import m02_knowledge_mc
from german_seo_bench.tasks.m03_facts import m03_facts

LIMIT = 2


@solver
def _wrong() -> Solver:
    """Antwortet mit einem JSON, das kein Feld trifft — M03 wertet das mit 0."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="wrong", content='{"name": "Falsch"}')
        state.messages.append(state.output.message)
        return state

    return solve


@solver
def _wrong_on(sample_id: str) -> Solver:
    """Musterlösung bis auf einen Datensatz; dort dieselbe falsche Antwort wie `_wrong`."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if state.sample_id == sample_id:
            state.output = ModelOutput.from_content(model="wrong", content='{"name": "Falsch"}')
        else:
            state.output = ModelOutput.from_content(model="oracle", content=state.metadata["oracle"])
        state.messages.append(state.output.message)
        return state

    return solve


@solver
def _fixed(content: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="fixed", content=content)
        state.messages.append(state.output.message)
        return state

    return solve


def _run(task, model: str, log_dir: Path, **kwargs) -> Path:
    if "sample_id" not in kwargs:
        kwargs["limit"] = LIMIT
    log = inspect_eval(task, model=model, log_dir=str(log_dir),
                       display="none", **kwargs)[0]
    assert log.status == "success", log.error
    return Path(log.location)


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> Path:
    """alpha: M03 einmal richtig, einmal falsch, M02 richtig. beta: nur M03 richtig."""
    target = tmp_path_factory.mktemp("report-logs")
    _run(m03_facts(oracle=True), "mockllm/alpha", target)
    _run(m03_facts(oracle=True), "mockllm/alpha", target, solver=_wrong())
    _run(m02_knowledge_mc(oracle=True), "mockllm/alpha", target)
    _run(m03_facts(oracle=True), "mockllm/beta", target)
    return target


@pytest.fixture(scope="module")
def report(log_dir: Path):
    return build(log_dir)


def _row(report, model: str):
    return next(row for row in report.rows if row.model == model)


def _copy(log_dir: Path, target: Path) -> Path:
    target.mkdir()
    for path in log_dir.glob("*.eval"):
        shutil.copy(path, target)
    return target


class TestRechnung:
    def test_je_datensatz_zaehlt_der_schlechteste_lauf(self, report) -> None:
        cell = _row(report, "mockllm/alpha").cells["m03_facts"]
        assert sorted(cell.run_scores) == [0.0, 1.0]
        assert cell.n == 2
        assert all(sorted(scores) == [0.0, 1.0] for scores in cell.samples.values())
        # Gemittelt über die Läufe wäre es 0.50.
        assert cell.value == 0.0

    def test_ein_lauf_ist_der_leitwert(self, report) -> None:
        cell = _row(report, "mockllm/alpha").cells["m02_knowledge_mc"]
        assert cell.n == 1
        assert cell.value == cell.run_scores[0] == 1.0

    def test_mittel_der_minima_nicht_minimum_der_mittel(self, tmp_path: Path) -> None:
        # Jeder Lauf scheitert an einem anderen Datensatz. Beide Laufmittel sind
        # 0.50, aber auf keinen der beiden Datensätze ist Verlass.
        first, second = sorted(s.id for s in m03_facts(oracle=True).dataset)[:LIMIT]
        _run(m03_facts(oracle=True), "mockllm/alpha", tmp_path, solver=_wrong_on(first),
             sample_id=[first, second])
        _run(m03_facts(oracle=True), "mockllm/alpha", tmp_path, solver=_wrong_on(second),
             sample_id=[first, second])
        cell = build(tmp_path).rows[0].cells["m03_facts"]
        assert sorted(cell.run_scores) == [0.5, 0.5]
        assert min(cell.run_scores) == 0.5  # die falsche Regel
        assert cell.value == 0.0

    def test_abzug_bleibt_abzug(self, tmp_path: Path) -> None:
        # Ein Titel mit Abzug (0.9) im schlechtesten Lauf zählt 0.9, nicht 0.
        _run(m01_titles(oracle=True), "mockllm/alpha", tmp_path, sample_id="golf")
        _run(m01_titles(oracle=True), "mockllm/alpha", tmp_path, sample_id="golf",
             solver=_fixed("Golf Lernen Anfänger: Platzreife, Kosten & Etikette"))
        cell = build(tmp_path).rows[0].cells["m01_titles"]
        assert sorted(cell.run_scores) == [0.9, 1.0]
        assert cell.value == 0.9

    def test_verschiedene_laufzahlen_sind_zulaessig(self, report) -> None:
        # alpha hat M03 zweimal, beta einmal — kein Abbruch, die Zahl steht dabei.
        assert "(n=2)" in render_markdown(report)
        assert "(n=1)" in render_markdown(report)

    def test_gesamt_ist_mittel_der_aufgaben_nicht_der_laeufe(self, report) -> None:
        # Über die drei Läufe gemittelt wäre es (1 + 0 + 1) / 3 = 0.667.
        assert _row(report, "mockllm/alpha").overall == 0.5

    def test_fehlende_aufgabe_zaehlt_nicht_als_null(self, report) -> None:
        beta = _row(report, "mockllm/beta")
        # Mit der fehlenden Aufgabe als 0 wäre es 0.50.
        assert beta.overall == 1.0
        assert beta.missing == ("m02_knowledge_mc",)
        assert not beta.complete

    def test_vollstaendiges_modell_ist_nicht_markiert(self, report) -> None:
        assert _row(report, "mockllm/alpha").complete

    def test_sortiert_nach_gesamt(self, report) -> None:
        assert [row.model for row in report.rows] == ["mockllm/beta", "mockllm/alpha"]

    def test_leitwert_kommt_aus_dem_log(self, report, log_dir: Path) -> None:
        """Die Zahl je Lauf ist der Leitwert, den die Aufgabe geschrieben hat."""
        for run in report.runs:
            log = read_eval_log(str(log_dir / run.file), header_only=True)
            headline = log.eval.headline_metric
            entry = next(s for s in log.results.scores if s.name == headline.score)
            assert run.score == entry.metrics[headline.metric].value

    def test_datensatzwerte_kommen_aus_dem_log(self, report, log_dir: Path) -> None:
        """Neu gewertet wird nichts: Jeder Wert je Datensatz steht so im Log."""
        logged: dict[tuple[str, str, str], list[float]] = {}
        for run in report.runs:
            log = read_eval_log(str(log_dir / run.file))
            for sample in log.samples:
                value = next(iter(sample.scores.values())).value["score"]
                logged.setdefault((run.task, run.model, str(sample.id)), []).append(float(value))
        for row in report.rows:
            for task, cell in row.cells.items():
                for sample, scores in cell.samples.items():
                    assert sorted(scores) == sorted(logged[(task, row.model, sample)])


class TestAuswahl:
    def test_glob_waehlt_teilmenge(self, log_dir: Path) -> None:
        files = select_logs(log_dir, "*m02-knowledge*")
        assert len(files) == 1
        assert len(select_logs(log_dir)) == 4

    def test_since_in_der_zukunft_waehlt_nichts(self, log_dir: Path) -> None:
        later = datetime.now(timezone.utc) + timedelta(days=1)
        with pytest.raises(ReportError, match="Keine .eval-Datei"):
            select_logs(log_dir, since=later)

    def test_since_ohne_zeitzone_gilt_als_utc(self, log_dir: Path) -> None:
        earlier = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
        assert len(select_logs(log_dir, since=earlier)) == 4

    def test_nur_eval_dateien(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "mixed")
        (target / "notiz.json").write_text("{}")
        assert len(select_logs(target, "*")) == 4


class TestNurBeimZusammenfassenSichtbar:
    def _run(self, task: str, version: str, file: str, **kwargs) -> Run:
        return Run(file=file, task=task, display_name=task, version=version,
                   model="m", score=1.0, stderr=None, created="", **kwargs)

    def test_verschiedene_versionen_werden_gemeldet(self) -> None:
        problems = check_consistency([self._run("m01", "10", "a"), self._run("m01", "11", "b")])
        assert len(problems) == 1
        assert "Aufgabenversion" in problems[0]
        assert "a" in problems[0] and "b" in problems[0]

    def test_gegenprobe_gleiche_versionen_sind_in_ordnung(self) -> None:
        assert check_consistency([self._run("m01", "11", "a"), self._run("m01", "11", "b")]) == []

    def test_bericht_beruehrt_den_score_nicht(self) -> None:
        plain = comparable_args({"oracle": False})
        assert comparable_args({"oracle": False, "report": False}) == plain

    def test_gegenprobe_andere_parameter_zaehlen_weiter(self) -> None:
        assert comparable_args({"oracle": True}) != comparable_args({"oracle": False})
        assert comparable_args({"agent": "react"}) != comparable_args({})

    def test_versionen_verschiedener_aufgaben_sind_unabhaengig(self) -> None:
        assert check_consistency([self._run("m01", "11", "a"), self._run("m02", "1", "b")]) == []

    def test_anderer_ausschnitt_bricht_ab(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "limit")
        inspect_eval(m03_facts(oracle=True), model="mockllm/gamma", log_dir=str(target),
                     limit=1, display="none")
        with pytest.raises(ReportError, match="Datensatz-Ausschnitt"):
            build(target)

    def test_verschiedene_epochen_brechen_ab(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "epochen")
        inspect_eval(m03_facts(oracle=True), model="mockllm/alpha", log_dir=str(target),
                     limit=LIMIT, epochs=2, display="none")
        with pytest.raises(ReportError, match="Epochenzahl"):
            build(target)

    def test_gegenprobe_gleiche_epochen_sind_in_ordnung(self, tmp_path: Path) -> None:
        for model in ("mockllm/alpha", "mockllm/beta"):
            inspect_eval(m03_facts(oracle=True), model=model, log_dir=str(tmp_path),
                         limit=LIMIT, epochs=2, display="none")
        assert build(tmp_path).rows[0].cells["m03_facts"].n == 1

    def test_abgebrochener_lauf_verdeckt_keinen_konflikt(self, log_dir: Path,
                                                         tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "both")
        _broken(target)
        inspect_eval(m03_facts(oracle=True), model="mockllm/gamma", log_dir=str(target),
                     limit=1, display="none")
        with pytest.raises(ReportError, match="Datensatz-Ausschnitt"):
            build(target)


@solver
def _crash() -> Solver:
    """Scheitert wie ein Lauf, dessen Server nicht mehr antwortet."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        raise RuntimeError("Server weg")

    return solve


def _broken(target: Path, model: str = "mockllm/gamma") -> Path:
    log = inspect_eval(m03_facts(oracle=True), model=model, log_dir=str(target),
                       limit=LIMIT, solver=_crash(), display="none")[0]
    assert log.status == "error"
    return Path(log.location)


@pytest.fixture(scope="module")
def mixed(log_dir: Path, tmp_path_factory) -> tuple[Path, Path]:
    """Die Läufe von `log_dir` und ein abgebrochener daneben."""
    target = _copy(log_dir, tmp_path_factory.mktemp("abgebrochen") / "logs")
    return target, _broken(target)


class TestAbgebrocheneLaeufe:
    def test_bricht_nicht_ab_und_rechnet_mit_den_uebrigen(self, mixed, report) -> None:
        target, broken = mixed
        mine = build(target)
        assert [f.file for f in mine.failed] == [broken.name]
        # Gegenprobe: Die übrigen Zahlen sind genau die ohne den Fehlerlauf.
        assert [(r.model, r.overall) for r in mine.rows] == [
            (r.model, r.overall) for r in report.rows]
        assert "mockllm/gamma" not in {r.model for r in mine.rows}

    def test_ursache_erste_zeile_mit_aufrufen_und_laufzeit(self, mixed) -> None:
        failed = build(mixed[0]).failed[0]
        assert failed.cause == "RuntimeError('Server weg')"
        assert (failed.task, failed.model, failed.status) == ("m03_facts", "mockllm/gamma", "error")
        assert failed.calls == 0 and failed.seconds is not None

    def test_steht_im_terminal_mit_hinweis_auf_den_viewer(self, mixed) -> None:
        target, broken = mixed
        text = render_terminal(build(target))
        assert "mockllm/gamma · m03_facts: RuntimeError('Server weg')" in text and broken.name in text
        assert f"uv run inspect view --log-dir {target}" in text

    def test_abschnitt_in_report_md_und_json(self, mixed, tmp_path: Path) -> None:
        target, broken = mixed
        report = build(target)
        written = write_outputs(report, tmp_path / "out")
        markdown = next(p for p in written if p.name == "report.md").read_text()
        assert "## Abgebrochene Läufe" in markdown
        assert markdown.index("## Abgebrochene Läufe") < markdown.index("## Laufdaten")
        assert "| mockllm/gamma | m03_facts | error | RuntimeError('Server weg') |" in markdown
        assert json.loads(render_json(report))["failed"][0]["file"] == broken.name

    def test_gegenprobe_ohne_fehlerlauf_kein_abschnitt(self, report, tmp_path: Path) -> None:
        assert render_failed(report) == ""
        assert "Ohne Ergebnis" not in render_terminal(report)
        assert json.loads(render_json(report))["failed"] == []

    def test_genaueste_meldung_vom_modellaufruf(self, mixed, tmp_path: Path) -> None:
        """Im Kopf steht oft nur die Hülle (RetryError), am Modellaufruf die Ursache."""
        log = read_eval_log(str(mixed[1]))
        log.error.message = "RetryError(<Future raised AttemptTimeoutError>)"
        event = ModelEvent(model="m", input=[], tools=[], tool_choice="none",
                           config=GenerateConfig(), output=ModelOutput(),
                           error="AttemptTimeoutError(\"attempt_timeout '1800' exceeded.\")")
        log.samples[0].events.append(event)
        path = tmp_path / mixed[1].name
        write_eval_log(log, str(path))
        failed = read_failure(path)
        assert failed.cause == "AttemptTimeoutError(\"attempt_timeout '1800' exceeded.\")"
        assert failed.calls == 1

    def test_gegenprobe_ohne_datensaetze_die_meldung_des_laufs(self, mixed,
                                                               tmp_path: Path) -> None:
        log = read_eval_log(str(mixed[1]))
        log.error.message = "RetryError(<Future raised AttemptTimeoutError>)\nzweite Zeile"
        log.samples = None
        path = tmp_path / mixed[1].name
        write_eval_log(log, str(path))
        failed = read_failure(path)
        assert failed.cause == "RetryError(<Future raised AttemptTimeoutError>)"
        assert failed.calls is None

    def test_abgebrochen_ohne_fehlermeldung(self, log_dir: Path, tmp_path: Path) -> None:
        source = next(log_dir.glob("*m02-knowledge*"))
        log = read_eval_log(str(source))
        log.status = "cancelled"
        log.results = None
        path = tmp_path / source.name
        write_eval_log(log, str(path))
        assert read_failure(path).cause == "abgebrochen"

    def test_alle_ohne_ergebnis_bricht_ab(self, tmp_path: Path) -> None:
        broken = _broken(tmp_path)
        with pytest.raises(ReportError) as caught:
            build(tmp_path)
        assert broken.name in str(caught.value) and "Server weg" in str(caught.value)


class TestAusgabe:
    def test_markdown_tabelle(self, report) -> None:
        text = render_markdown(report)
        assert "| Modell | Gesamt | m02_knowledge_mc | m03_facts |" in text
        # Die Zahl der Läufe ist nicht überall gleich, also steht sie in jeder Zelle.
        assert "| mockllm/beta | 1.00* | — | 1.00 (n=1) |" in text
        assert "| mockllm/alpha | 0.50 | 1.00 (n=1) | 0.00 (n=2) |" in text
        assert "schlechteste Lauf" in text
        assert "ohne m02_knowledge_mc" in text

    def test_json(self, report) -> None:
        data = json.loads(render_json(report))
        assert data["aggregation"] == "worst_run_per_sample_then_mean"
        models = {m["model"]: m for m in data["models"]}
        assert models["mockllm/beta"]["tasks_missing"] == ["m02_knowledge_mc"]
        alpha = models["mockllm/alpha"]["cells"]["m03_facts"]
        assert alpha["n"] == 2
        assert alpha["score"] == 0.0
        assert sorted(alpha["run_scores"]) == [0.0, 1.0]
        assert set(alpha["samples"].values()) == {0.0}
        assert len(data["runs"]) == 4
        assert len(data["selection"]["files"]) == 4

    @pytest.mark.parametrize("render", [render_svg_overall, render_svg_matrix])
    def test_svg_ist_gueltig_und_vollstaendig(self, report, render) -> None:
        text = render(report)
        ElementTree.fromstring(text)  # wirft bei kaputtem XML
        for row in report.rows:
            assert row.model in text
        assert "nan" not in text.lower()

    def test_main_schreibt_alle_dateien(self, log_dir: Path, tmp_path: Path, capsys) -> None:
        assert main([str(log_dir), "--out", str(tmp_path / "out")]) == 0
        names = sorted(p.name for p in (tmp_path / "out").iterdir())
        assert names == ["bars-m02_knowledge_mc.svg", "bars-m03_facts.svg", "matrix.svg",
                         "overall.svg", "report.html", "report.json", "report.md"]
        assert "mockllm/alpha" in capsys.readouterr().out

    def test_main_meldet_fehler_mit_exitcode(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path)]) == 1
        assert "Fehler:" in capsys.readouterr().err


class TestBenchmarkVersion:
    """Der Stand des Pakets, aus dem die Zahlen stammen, steht in jeder Ausgabe."""

    def test_stammt_aus_dem_log(self, report) -> None:
        # Gegen die installierte Version, nicht gegen eine Zahl im Test: Die
        # Logs entstehen mit genau diesem Paket.
        installed = version("german-seo-bench")
        assert {run.benchmark_version for run in report.runs} == {installed}
        line = f"Benchmark-Version: german-seo-bench {installed}"
        assert line in render_terminal(report)
        assert line in render_markdown(report)
        assert json.loads(render_json(report))["benchmark_versions"] == {installed: 4}
        for render in (render_svg_overall, render_svg_matrix):
            assert f"german-seo-bench {installed} · Messzeitraum " in render(report)

    def test_verschiedene_staende_werden_genannt_nicht_abgelehnt(
        self, log_dir: Path, tmp_path: Path
    ) -> None:
        """Gegenprobe: Ein anderer Paketstand bei gleicher Aufgabenversion bricht nicht ab.

        Vergleichbar macht zwei Scores die Aufgabenversion. Der Bericht nennt
        dann beide Stände mit ihren Läufen, statt einen davon zu verschweigen.
        """
        target = _copy(log_dir, tmp_path / "staende")
        path = _m03_logs(target, "mockllm/beta")[0]
        log = read_eval_log(str(path))
        log.eval.packages["german-seo-bench"] = "0.0.9"
        write_eval_log(log, str(path))
        report = build(target)
        installed = version("german-seo-bench")
        assert (f"Benchmark-Version: german-seo-bench 0.0.9 (1 Lauf), {installed} (3 Läufe)"
                in render_markdown(report))
        assert json.loads(render_json(report))["benchmark_versions"] == {"0.0.9": 1, installed: 3}

    def test_alte_paketversion_heisst_2026_1(self, log_dir: Path, tmp_path: Path) -> None:
        """Logs aus der Zeit vor der Veröffentlichung tragen `0.1.0`.

        Die Gegenprobe steht im Test davor: `0.0.9` bleibt `0.0.9`, umbenannt
        wird nur, was in `RENAMED_VERSIONS` steht.
        """
        target = _copy(log_dir, tmp_path / "alt")
        for path in target.glob("*.eval"):
            log = read_eval_log(str(path))
            log.eval.packages["german-seo-bench"] = "0.1.0"
            write_eval_log(log, str(path))
        report = build(target)
        assert "Benchmark-Version: german-seo-bench 2026.1" in render_markdown(report)
        assert "0.1.0" not in render_markdown(report)
        assert {run.benchmark_version for run in report.runs} == {"2026.1"}
        # Das Log selbst bleibt, wie Inspect es geschrieben hat.
        path = next(target.glob("*.eval"))
        assert read_eval_log(str(path), header_only=True).eval.packages["german-seo-bench"] == "0.1.0"

    def test_fehlt_im_log(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "ohne")
        for path in target.glob("*.eval"):
            log = read_eval_log(str(path))
            log.eval.packages.pop("german-seo-bench")
            write_eval_log(log, str(path))
        assert "Benchmark-Version: german-seo-bench unbekannt" in render_markdown(build(target))


class TestMesszeitraum:
    """Die Monate, in denen die eingerechneten Läufe gestartet wurden."""

    @staticmethod
    def _runs(*created: str) -> list[Run]:
        return [Run(file=str(i), task="m01", display_name="m01", version="1", model="m",
                    score=1.0, stderr=None, created=c) for i, c in enumerate(created)]

    def test_ueber_mehrere_monate(self) -> None:
        # In umgekehrter Reihenfolge: Es zählen frühester und spätester Start,
        # nicht erster und letzter Eintrag der Liste.
        runs = self._runs("2026-10-12T09:00:00+00:00", "2026-09-01T09:00:00+00:00",
                          "2026-08-08T09:00:00+00:00")
        assert period_text(runs) == "August bis Oktober 2026"

    def test_ein_monat_steht_allein(self) -> None:
        runs = self._runs("2026-10-01T09:00:00+00:00", "2026-10-29T09:00:00+00:00")
        assert period_text(runs) == "Oktober 2026"

    def test_ueber_den_jahreswechsel_beide_jahre(self) -> None:
        # Gegenprobe zu „August bis Oktober 2026“: Ohne das erste Jahr stünde
        # dort „Dezember bis Februar 2027“, also Dezember 2027.
        runs = self._runs("2026-12-20T09:00:00+00:00", "2027-02-03T09:00:00+00:00")
        assert period_text(runs) == "Dezember 2026 bis Februar 2027"

    def test_gleicher_monat_anderes_jahr_ist_kein_einzelmonat(self) -> None:
        runs = self._runs("2026-10-01T09:00:00+00:00", "2027-10-01T09:00:00+00:00")
        assert period_text(runs) == "Oktober 2026 bis Oktober 2027"

    def test_in_utc_wie_die_dateinamen(self) -> None:
        # 1. November, 00:30 in Berlin, ist in UTC noch der 31. Oktober.
        runs = self._runs("2026-11-01T00:30:00+01:00")
        assert period_text(runs) == "Oktober 2026"

    def test_aus_den_logs(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "zeitraum")
        moments = iter(["2026-08-08T10:00:00+00:00", "2026-10-12T10:00:00+00:00"])
        for path in sorted(target.glob("*.eval"))[:2]:
            log = read_eval_log(str(path))
            log.eval.created = next(moments)
            write_eval_log(log, str(path))
        for path in sorted(target.glob("*.eval"))[2:]:
            log = read_eval_log(str(path))
            log.eval.created = "2026-09-15T10:00:00+00:00"
            write_eval_log(log, str(path))
        report = build(target)
        assert "Messzeitraum: August bis Oktober 2026" in render_markdown(report)
        assert "Messzeitraum: August bis Oktober 2026" in render_terminal(report)
        assert json.loads(render_json(report))["measurement_period"] == {
            "first": "2026-08-08T10:00:00+00:00", "last": "2026-10-12T10:00:00+00:00"}
        for render in (render_svg_overall, render_svg_matrix):
            assert "Messzeitraum August bis Oktober 2026</text>" in render(report)


def _tooltips(svg: str) -> dict[str, ElementTree.Element]:
    """Jede Gruppe mit Tooltip, nach dem Modellnamen am Anfang des Tooltips."""
    ns = "{http://www.w3.org/2000/svg}"
    groups = {}
    for group in ElementTree.fromstring(svg).iter(f"{ns}g"):
        title = group.find(f"{ns}title")
        if title is not None:
            groups[title.text.split(" · ")[0]] = group
    return groups


class TestBalkenJeAufgabe:
    def test_werte_und_reihenfolge(self, report) -> None:
        svg = render_svg_task(report, "m03_facts")
        ElementTree.fromstring(svg)
        assert "M03 — Fakten-Extraktion" in svg
        # Gegenprobe zur Sortierung: alpha steht in der Tabelle vor beta, im
        # Diagramm steht der höhere Score oben.
        assert svg.index(">mockllm/beta<") < svg.index(">mockllm/alpha<")
        assert ">1.00<" in svg and ">0.00<" in svg
        # Verschiedene Laufzahlen: die Zahl steht am Balken.
        assert "(n=2)" in svg and "(n=1)" in svg

    def test_modell_ohne_lauf_fehlt(self, report) -> None:
        # beta hat M02 nie gemessen; eine leere Zeile sähe aus wie Score 0.
        svg = render_svg_task(report, "m02_knowledge_mc")
        assert "mockllm/alpha" in svg
        assert "mockllm/beta" not in svg

    def test_fusszeile_nennt_nur_die_laeufe_der_aufgabe(self, report) -> None:
        svg = render_svg_task(report, "m02_knowledge_mc")
        assert f"german-seo-bench {version('german-seo-bench')} · Messzeitraum " in svg
        assert "ein Lauf" in svg


class TestModellgroessen:
    def test_semikolon_und_dezimalkomma(self, tmp_path: Path) -> None:
        path = tmp_path / "groessen.csv"
        path.write_text("modell;dateigroesse_gb;quantisierung\nlocal/a;7,9;Q5_K_M\n"
                        "local/b;16;Q4_K_M\n;;\n", encoding="utf-8")
        assert read_model_sizes(path) == {"local/a": 7.9, "local/b": 16.0}

    def test_komma_als_trenner(self, tmp_path: Path) -> None:
        path = tmp_path / "groessen.csv"
        path.write_text("modell,dateigroesse_gb\nlocal/a,4.7\n", encoding="utf-8")
        assert read_model_sizes(path) == {"local/a": 4.7}

    @pytest.mark.parametrize("content, message", [
        ("modell;groesse\nlocal/a;3\n", "Spalte fehlt: dateigroesse_gb"),
        ("modell;dateigroesse_gb\nlocal/a;viel\n", "Zeile 2: keine Zahl"),
        ("modell;dateigroesse_gb\nlocal/a;0\n", "größer als 0"),
        ("modell;dateigroesse_gb\nlocal/a;3\nlocal/a;4\n", "Zeile 3: local/a steht doppelt"),
    ])
    def test_fehler_brechen_ab(self, tmp_path: Path, content: str, message: str) -> None:
        path = tmp_path / "groessen.csv"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ReportError, match=message):
            read_model_sizes(path)


def _point(svg: str, model: str) -> tuple[float, float]:
    shape = _tooltips(svg)[model].find("{http://www.w3.org/2000/svg}circle")
    return float(shape.get("cx")), float(shape.get("cy"))


class TestStreudiagramm:
    def test_groesser_nach_rechts_besser_nach_oben(self, report) -> None:
        svg = render_svg_scatter(report, {"mockllm/alpha": 20.0, "mockllm/beta": 2.0})
        ElementTree.fromstring(svg)
        alpha, beta = _point(svg, "mockllm/alpha"), _point(svg, "mockllm/beta")
        assert alpha[0] > beta[0]  # 20 GB rechts von 2 GB
        assert beta[1] < alpha[1]  # Gesamt 1.00 über 0.50

    def test_achse_ist_logarithmisch(self, report) -> None:
        """Gleiches Verhältnis, gleicher Abstand; 10 GB mitten zwischen 1 und 100.

        Der Abstandsvergleich allein ließe eine lineare Achse durch, weil der
        Bereich mit den Daten wandert. Die Lage der Gitterlinie bei 10 GB
        nicht: Linear läge sie bei einem Elftel des Wegs.
        """
        def span(small: float, large: float) -> float:
            svg = render_svg_scatter(report, {"mockllm/alpha": small, "mockllm/beta": large})
            return _point(svg, "mockllm/beta")[0] - _point(svg, "mockllm/alpha")[0]

        assert span(1.0, 10.0) == pytest.approx(span(10.0, 100.0))
        wide = render_svg_scatter(report, {"mockllm/alpha": 1.0, "mockllm/beta": 100.0})
        left, right = _point(wide, "mockllm/alpha")[0], _point(wide, "mockllm/beta")[0]
        tick = next(float(line.get("x1")) for line in ElementTree.fromstring(wide).iter(
            "{http://www.w3.org/2000/svg}line") if line.get("x1") == line.get("x2")
            and abs(float(line.get("x1")) - (left + right) / 2) < 1)
        assert tick == pytest.approx((left + right) / 2, abs=1)
        assert ">10<" in wide

    def test_ohne_groesse_als_linie(self, report) -> None:
        svg = render_svg_scatter(report, {"mockllm/alpha": 4.0})
        beta = _tooltips(svg)["mockllm/beta"]
        ns = "{http://www.w3.org/2000/svg}"
        assert beta.find(f"{ns}circle") is None
        assert beta.find(f"{ns}line").get("stroke-dasharray")
        assert "Gestrichelt: Modelle ohne Dateigröße (mockllm/beta)" in svg

    def test_je_aufgabe(self, report) -> None:
        svg = render_svg_scatter(report, {"mockllm/alpha": 4.0, "mockllm/beta": 8.0}, "m02_knowledge_mc")
        assert "M02 — Deutsches Weltwissen, Multiple Choice nach Dateigröße" in svg
        assert "mockllm/alpha" in _tooltips(svg)
        assert "mockllm/beta" not in svg

    def test_main_mit_groessen(self, log_dir: Path, tmp_path: Path) -> None:
        sizes = tmp_path / "groessen.csv"
        sizes.write_text("modell;dateigroesse_gb\nmockllm/alpha;4\n", encoding="utf-8")
        assert main([str(log_dir), "--out", str(tmp_path / "out"), "--model-sizes", str(sizes)]) == 0
        names = {p.name for p in (tmp_path / "out").iterdir()}
        assert {"scatter.svg", "scatter-m02_knowledge_mc.svg", "scatter-m03_facts.svg"} <= names

    def test_groessen_ohne_out_bricht_ab(self, log_dir: Path, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exit_:
            main([str(log_dir), "--model-sizes", str(tmp_path / "x.csv")])
        assert exit_.value.code == 2


def _write_sidecar(log_path: Path, judges: dict[str, tuple[int, float | None]],
                   eval_id: str | None = None) -> None:
    """Ein Sidecar wie von `judge.py`, reduziert auf das, was die Auswertung liest.

    Von Hand geschrieben statt über `judge.py`: Geprüft wird hier die
    Auswertung, nicht der Judge, und so lässt sich jede Kombination
    (fehlende Note, andere Version, fremder Lauf) direkt herstellen.
    """
    log = read_eval_log(str(log_path), header_only=True)
    data = {
        "eval_id": eval_id or log.eval.eval_id,
        "judges": {
            judge: {"judge_version": version,
                    "summary": {"unusable_rate": rate, "parsed": 0.0 if rate is None else 1.0}}
            for judge, (version, rate) in judges.items()
        },
    }
    log_path.with_suffix(".judges.json").write_text(json.dumps(data), encoding="utf-8")


def _m03_logs(target: Path, model: str) -> list[Path]:
    return sorted(p for p in target.glob("*m03-facts*.eval")
                  if read_eval_log(str(p), header_only=True).eval.model == model)


class TestJudgeSpalten:
    def test_ohne_sidecar_keine_judge_spalte(self, report) -> None:
        assert report.judges == []
        assert "Judge" not in render_markdown(report)

    def test_anteil_gemittelt_ueber_die_laeufe(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "judged")
        first, second = _m03_logs(target, "mockllm/alpha")
        _write_sidecar(first, {"local/j": (3, 0.4)})
        _write_sidecar(second, {"local/j": (3, 0.2)})
        report = build(target)
        cell = _row(report, "mockllm/alpha").judge_cells[("m03_facts", "local/j")]
        assert cell.mean == pytest.approx(0.3)
        assert cell.n == 2

    def test_judge_geht_nicht_ins_gesamt(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "gesamt")
        for path in _m03_logs(target, "mockllm/alpha"):
            _write_sidecar(path, {"local/j": (3, 1.0)})
        assert _row(build(target), "mockllm/alpha").overall == 0.5

    def test_fehlendes_urteil_ist_kein_freispruch(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "ohne")
        first, second = _m03_logs(target, "mockllm/alpha")
        _write_sidecar(first, {"local/j": (3, 0.4)})
        _write_sidecar(second, {"local/j": (3, None)})
        report = build(target)
        # Mit dem fehlenden Urteil als 0 wäre es 0.2.
        assert _row(report, "mockllm/alpha").judge_cells[("m03_facts", "local/j")].mean == 0.4

    def test_nur_fehlende_urteile_zeigen_fragezeichen(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "frage")
        (path,) = _m03_logs(target, "mockllm/beta")
        _write_sidecar(path, {"local/j": (3, None)})
        assert "| mockllm/beta | 1.00* | — | 1.00 (n=1) | ? |" in render_markdown(build(target))

    def test_teilweise_gewertet_sagt_wie_viele(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "teil")
        first, _ = _m03_logs(target, "mockllm/alpha")
        _write_sidecar(first, {"local/j": (3, 0.4)})
        assert "40 % (1 von 2 Läufen)" in render_markdown(build(target))

    def test_zwei_judges_zwei_spalten(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "zwei")
        (path,) = _m03_logs(target, "mockllm/beta")
        _write_sidecar(path, {"local/a": (3, 0.4), "anthropic/b": (3, 0.2)})
        report = build(target)
        assert [c.judge for c in report.judges] == ["anthropic/b", "local/a"]
        text = render_markdown(report)
        assert "Judge m03_facts · local/a" in text and "Judge m03_facts · anthropic/b" in text

    def test_json_und_svg(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "ausgabe")
        (path,) = _m03_logs(target, "mockllm/beta")
        _write_sidecar(path, {"local/j": (3, 0.4)})
        report = build(target)
        data = json.loads(render_json(report))
        assert data["judges"] == [{"task": "m03_facts", "judge": "local/j", "judge_version": 3}]
        beta = next(m for m in data["models"] if m["model"] == "mockllm/beta")
        assert beta["judge_cells"]["m03_facts"]["local/j"]["mean"] == 0.4
        svg = render_svg_matrix(report)
        ElementTree.fromstring(svg)
        assert "Judge: unbrauchbar" in svg

    def test_verschiedene_judge_versionen_brechen_ab(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "versionen")
        first, second = _m03_logs(target, "mockllm/alpha")
        _write_sidecar(first, {"local/j": (3, 0.4)})
        _write_sidecar(second, {"local/j": (4, 0.4)})
        with pytest.raises(ReportError, match="verschiedene Judge-Versionen"):
            build(target)

    def test_gegenprobe_verschiedene_judges_duerfen_verschiedene_versionen_haben(
        self, log_dir: Path, tmp_path: Path
    ) -> None:
        target = _copy(log_dir, tmp_path / "andere")
        first, second = _m03_logs(target, "mockllm/alpha")
        _write_sidecar(first, {"local/a": (3, 0.4)})
        _write_sidecar(second, {"local/b": (4, 0.4)})
        build(target)

    def test_fremder_sidecar_bricht_ab(self, log_dir: Path, tmp_path: Path) -> None:
        target = _copy(log_dir, tmp_path / "fremd")
        (path,) = _m03_logs(target, "mockllm/beta")
        _write_sidecar(path, {"local/j": (3, 0.4)}, eval_id="fremd")
        with pytest.raises(ReportError, match="nicht zum Log daneben passen"):
            build(target)

    def test_sidecar_ohne_anteil_wird_gemeldet(self, log_dir: Path, tmp_path: Path) -> None:
        """Ein Eintrag ohne `unusable_rate` darf weder als Anteil durchgehen noch fehlen."""
        target = _copy(log_dir, tmp_path / "alt")
        (path,) = _m03_logs(target, "mockllm/beta")
        log = read_eval_log(str(path), header_only=True)
        old = {"eval_id": log.eval.eval_id,
               "judges": {"local/j": {"judge_version": 2, "summary": {"mean": 4.74}}}}
        path.with_suffix(".judges.json").write_text(json.dumps(old), encoding="utf-8")
        with pytest.raises(ReportError, match="kein Feld unusable_rate"):
            build(target)


# ------------------------------------------------------------- Denkmodi


@pytest.fixture(scope="module")
def mode_dir(tmp_path_factory) -> Path:
    """gamma: M02 und M03 mit think-off richtig, M03 mit think-high falsch.
    delta: nur M03, think-off, falsch. eps: ohne Endung, nur M03 richtig."""
    target = tmp_path_factory.mktemp("mode-logs")
    _run(m02_knowledge_mc(oracle=True), "mockllm/gamma-think-off", target)
    _run(m03_facts(oracle=True), "mockllm/gamma-think-off", target)
    _run(m03_facts(oracle=True), "mockllm/gamma-think-high", target, solver=_wrong())
    _run(m03_facts(oracle=True), "mockllm/delta-think-off", target, solver=_wrong())
    _run(m03_facts(oracle=True), "mockllm/eps", target)
    return target


@pytest.fixture(scope="module")
def mode_report(mode_dir: Path):
    return build(mode_dir)


def _mrow(matrix, model: str):
    return next(row for row in matrix.rows if row.model == model)


class TestDenkmodi:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("local/gemma4-31b-think-off", ("local/gemma4-31b", "aus")),
            ("local/qwen3.8-27b-think-none", ("local/qwen3.8-27b", "aus")),
            ("local/qwen3.5-4b-think-off-precise", ("local/qwen3.5-4b", "aus")),
            ("local/gemma4-31b-think-on", ("local/gemma4-31b", "an")),
            ("local/qwen3.8-27b-think-low", ("local/qwen3.8-27b", "an")),
            ("local/qwen3.8-27b-think-medium", ("local/qwen3.8-27b", "an")),
            ("local/qwen3.8-27b-think-high", ("local/qwen3.8-27b", "an")),
            ("local/qwen3.8-27b-think-xhigh", ("local/qwen3.8-27b", "an")),
            ("local/qwen3.5-9b-think-on-precise", ("local/qwen3.5-9b", "an")),
            # Gegenprobe: "none" nur als ganzes Wort, nicht als Anfang.
            ("local/x-think-nonestop", ("local/x", "an")),
            ("local/gemma4-12b", ("local/gemma4-12b", None)),
            ("local/thinker-7b", ("local/thinker-7b", None)),
            ("local/x-think-", ("local/x-think-", None)),
        ],
    )
    def test_modus_aus_der_endung(self, model: str, expected) -> None:
        assert split_model(model) == expected

    def test_je_aufgabe_eine_spalte_pro_modus(self, mode_report) -> None:
        # Der Modus mit den meisten Modellen zuerst, "?" zuletzt. Gegenprobe:
        # alphabetisch stünde an vor aus.
        assert [c.label for c in combined_matrix(mode_report).columns] == [
            "m02_knowledge_mc · aus", "m03_facts · aus", "m03_facts · an", "m03_facts · ?",
        ]

    def test_aus_vor_an_auch_wenn_an_mehr_modelle_hat(self, tmp_path: Path) -> None:
        # M03 läuft bei zwei Modellen mit Denken, bei einem ohne; M02 nur ohne.
        # Gegenprobe: nach Zahl der Modelle sortiert, stünde „an“ bei M03 vorn
        # und bei M02 hinten — dieselbe Spalte an verschiedenen Stellen.
        _run(m02_knowledge_mc(oracle=True), "mockllm/theta-think-off", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/theta-think-off", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/theta-think-on", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/iota-think-on", tmp_path)
        _run(m02_knowledge_mc(oracle=True), "mockllm/iota-think-on", tmp_path)
        _run(m02_knowledge_mc(oracle=True), "mockllm/kappa-think-on", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/kappa-think-on", tmp_path)
        assert [c.label for c in combined_matrix(build(tmp_path)).columns] == [
            "m02_knowledge_mc · aus", "m02_knowledge_mc · an", "m03_facts · aus", "m03_facts · an",
        ]

    def test_eine_zeile_je_modell(self, mode_report) -> None:
        matrix = combined_matrix(mode_report)
        assert [row.model for row in matrix.rows] == ["mockllm/gamma", "mockllm/delta", "mockllm/eps"]
        assert _mrow(matrix, "mockllm/gamma").variants == (
            "mockllm/gamma-think-high", "mockllm/gamma-think-off")

    def test_zellen_sind_die_der_vollen_namen(self, mode_report) -> None:
        """Umgruppiert, nicht gerechnet: Jede Zelle ist dasselbe Objekt."""
        gamma = _mrow(combined_matrix(mode_report), "mockllm/gamma")
        on = next(c for c in gamma.cells if c.mode == "an")
        assert gamma.cells[on] is _row(mode_report, "mockllm/gamma-think-high").cells["m03_facts"]
        assert gamma.sources[on] == "mockllm/gamma-think-high"

    def test_modi_werden_nicht_verrechnet(self, mode_report) -> None:
        """Beide M03-Modi stehen nebeneinander. Gegenprobe: bester Modus gäbe
        nur 1.0, Mittel der Modi 0.5 in einer einzigen Spalte."""
        gamma = _mrow(combined_matrix(mode_report), "mockllm/gamma")
        assert sorted(cell.value for c, cell in gamma.cells.items()
                      if c.task == "m03_facts") == [0.0, 1.0]

    def test_eine_gesamtspalte_je_kombination(self, mode_report) -> None:
        matrix = combined_matrix(mode_report)
        assert [(t.label, t.legend) for t in matrix.totals] == [
            ("Gesamt [a]", "m02_knowledge_mc · aus, m03_facts · aus"),
            ("Gesamt [b]", "m02_knowledge_mc · aus, m03_facts · an"),
            ("Gesamt [c]", "m02_knowledge_mc · aus, m03_facts · ?"),
        ]
        a, b, c = matrix.totals
        gamma = _mrow(matrix, "mockllm/gamma")
        assert gamma.overall(a).value == 1.0
        assert gamma.overall(b).value == 0.5
        eps = _mrow(matrix, "mockllm/eps")
        assert eps.overall(c).value == 1.0
        assert eps.overall(c).missing == ("m02_knowledge_mc",)

    def test_anderer_modus_ist_keine_fehlende_aufgabe(self, mode_report) -> None:
        """gamma hat M03, nur nicht ohne Modus: Gesamt [c] bleibt leer.
        Gegenprobe: aus den übrigen Aufgaben gerechnet, stünde dort 1.0."""
        matrix = combined_matrix(mode_report)
        assert _mrow(matrix, "mockllm/gamma").overall(matrix.totals[2]) is None
        assert _mrow(matrix, "mockllm/delta").overall(matrix.totals[1]) is None

    def test_fehlende_aufgabe_bleibt_markiert(self, mode_report) -> None:
        delta = _mrow(combined_matrix(mode_report), "mockllm/delta")
        overall = delta.overall(combined_matrix(mode_report).totals[0])
        assert overall.value == 0.0 and not overall.complete

    def test_markdown(self, mode_report) -> None:
        text = render_markdown(mode_report)
        assert "## Je Modell, Denkmodi als Spalten" in text
        assert ("| Modell | Gesamt [a] | Gesamt [b] | Gesamt [c] | m02_knowledge_mc · aus "
                "| m03_facts · aus | m03_facts · an | m03_facts · ? |") in text
        assert "| mockllm/gamma | 1.00 | 0.50 | — | 1.00 | 1.00 | 0.00 | — |" in text
        assert "[a] m02_knowledge_mc · aus, m03_facts · aus" in text
        assert "? = Modellname ohne Endung" in text
        # gamma hat zwei Modi, also steht die Tabelle je vollem Namen dahinter.
        assert "## Je Modellvariante" in text

    def test_markdown_mit_links(self, mode_report) -> None:
        # Jede Spalte einer Aufgabe verweist auf dieselben Einzelergebnisse.
        text = render_markdown(mode_report, links=True)
        assert ("| Modell | Gesamt [a] | Gesamt [b] | Gesamt [c] "
                "| [m02_knowledge_mc · aus](#m02_knowledge_mc--einzelergebnisse) "
                "| [m03_facts · aus](#m03_facts--einzelergebnisse) "
                "| [m03_facts · an](#m03_facts--einzelergebnisse) "
                "| [m03_facts · ?](#m03_facts--einzelergebnisse) |") in text
        # Die Fußnoten nennen die Spalten weiter ohne Link.
        assert "[a] m02_knowledge_mc · aus, m03_facts · aus" in text
        assert "| mockllm/gamma-think-high |" in text
        assert text.index("Je Modell,") < text.index("Je Modellvariante")

    def test_ein_modus_je_modell_eine_tabelle(self, mode_dir: Path, tmp_path: Path) -> None:
        target = tmp_path / "single"
        target.mkdir()
        for path in mode_dir.glob("*.eval"):
            if read_eval_log(str(path), header_only=True).eval.model == "mockllm/delta-think-off":
                shutil.copy(path, target)
        text = render_markdown(build(target))
        assert "| Modell | Gesamt | m03_facts · aus |" in text
        assert "| mockllm/delta | 0.00 | 0.00 |" in text
        assert "Je Modellvariante" not in text
        assert "[a]" not in text

    def test_ohne_endung_bleibt_alles_wie_bisher(self, report) -> None:
        text = render_markdown(report)
        assert "Denkmodi" not in text and "·" not in text.split("\n\n")[0]
        assert json.loads(render_json(report))["combined"] is None

    def test_json(self, mode_report) -> None:
        combined = json.loads(render_json(mode_report))["combined"]
        assert [t["label"] for t in combined["totals"]] == ["Gesamt [a]", "Gesamt [b]", "Gesamt [c]"]
        gamma = next(m for m in combined["models"] if m["model"] == "mockllm/gamma")
        assert gamma["totals"]["Gesamt [b]"]["score"] == 0.5
        assert gamma["totals"]["Gesamt [c]"] is None
        assert gamma["cells"]["m03_facts · an"]["source"] == "mockllm/gamma-think-high"

    @pytest.mark.parametrize("render", [render_svg_overall, render_svg_matrix])
    def test_svg(self, mode_report, render) -> None:
        text = render(mode_report)
        ElementTree.fromstring(text)
        for model in ("mockllm/gamma", "mockllm/delta", "mockllm/eps"):
            assert model in text
        # Beschriftet wird je Modell; der volle Name steht nur im Tooltip.
        assert ">mockllm/gamma</text>" in text
        assert "-think-off</text>" not in text
        assert "[c] m02_knowledge_mc · aus, m03_facts · ?" in text
        assert "nan" not in text.lower()

    def test_kombination_ohne_messung_faellt_weg(self) -> None:
        """a lief beide Aufgaben nur mit off, b nur mit on. (off, on) hat
        niemand gemessen; die Spalte entfiele sonst nur als lauter „—“."""
        def cell(value: float) -> Cell:
            return Cell({"s": (value,)}, (value,))

        tasks = [Task("t1", "T1", "1"), Task("t2", "T2", "1")]
        rows = [
            ModelRow("x/a-think-off", {"t1": cell(1.0), "t2": cell(0.5)}, ()),
            ModelRow("x/b-think-on", {"t1": cell(0.0), "t2": cell(1.0)}, ()),
        ]
        matrix = combined_matrix(Report(tasks, rows, runs=[]))
        assert [t.legend for t in matrix.totals] == ["t1 · aus, t2 · aus", "t1 · an, t2 · an"]
        assert [row.model for row in matrix.rows] == ["x/a", "x/b"]

    def test_verschiedene_bezeichnungen_eine_spalte(self, tmp_path: Path) -> None:
        """Gemma schaltet mit on/off, Qwen mit reasoning_effort: Beide landen
        in denselben zwei Spalten. Gegenprobe: je Bezeichnung eine Spalte
        wären vier."""
        _run(m03_facts(oracle=True), "mockllm/g-think-off", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/g-think-on", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-none", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-on-precise", tmp_path)
        matrix = combined_matrix(build(tmp_path))
        assert [c.label for c in matrix.columns] == ["m03_facts · aus", "m03_facts · an"]
        assert [row.model for row in matrix.rows] == ["mockllm/g", "mockllm/q"]

    def test_zwei_varianten_im_selben_modus_brechen_ab(self, tmp_path: Path) -> None:
        _run(m03_facts(oracle=True), "mockllm/q-think-low", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-high", tmp_path, solver=_wrong())
        with pytest.raises(ReportError) as error:
            build(tmp_path)
        text = str(error.value)
        assert "m03_facts: mehrere Varianten von mockllm/q im Modus „an“" in text
        assert "mockllm/q-think-high" in text and "mockllm/q-think-low" in text

    def test_gegenprobe_aus_und_an_derselben_aufgabe_sind_in_ordnung(self, tmp_path: Path) -> None:
        _run(m03_facts(oracle=True), "mockllm/q-think-none", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-high", tmp_path, solver=_wrong())
        assert len(combined_matrix(build(tmp_path)).columns) == 2

    def test_gegenprobe_zwei_varianten_verschiedener_aufgaben_sind_in_ordnung(
            self, tmp_path: Path) -> None:
        _run(m03_facts(oracle=True), "mockllm/q-think-low", tmp_path)
        _run(m02_knowledge_mc(oracle=True), "mockllm/q-think-high", tmp_path)
        assert [c.label for c in combined_matrix(build(tmp_path)).columns] == [
            "m02_knowledge_mc · an", "m03_facts · an"]

    def test_fussnote_nennt_die_variante_je_modus(self, tmp_path: Path) -> None:
        """Die Spalte sagt nur „an“; welche Variante es war, steht darunter.
        Gegenprobe: Die Zeile des anderen Modells nennt nicht dessen Endung."""
        _run(m03_facts(oracle=True), "mockllm/g-think-off", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/g-think-on", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-none", tmp_path)
        _run(m03_facts(oracle=True), "mockllm/q-think-on-precise", tmp_path)
        text = render_markdown(build(tmp_path))
        assert "mockllm/g: aus = think-off; an = think-on" in text
        assert "mockllm/q: aus = think-none; an = think-on-precise" in text
        assert "mockllm/g: aus = think-off; an = think-on-precise" not in text

    def test_fussnote_nennt_aufgaben_bei_wechselnder_variante(self, tmp_path: Path) -> None:
        _run(m03_facts(oracle=True), "mockllm/q-think-low", tmp_path)
        _run(m02_knowledge_mc(oracle=True), "mockllm/q-think-high", tmp_path)
        assert ("mockllm/q: an = think-high (m02_knowledge_mc), think-low (m03_facts)"
                in render_markdown(build(tmp_path)))

    def test_gegenprobe_eine_variante_ohne_aufgaben(self, tmp_path: Path) -> None:
        _run(m03_facts(oracle=True), "mockllm/q-think-low", tmp_path)
        _run(m02_knowledge_mc(oracle=True), "mockllm/q-think-low", tmp_path)
        assert "mockllm/q: an = think-low\n" in render_markdown(build(tmp_path))


class TestDenkmodusImTitel:
    """Jede Grafik sagt im Titel, ob mit oder ohne Denken gemessen wurde."""

    RENDERERS = (
        (render_svg_overall, "Gesamtscore je Modell"),
        (render_svg_matrix, "Score je Aufgabe und Modell"),
        (lambda r: render_svg_task(r, "m03_facts"), "M03 — Fakten-Extraktion"),
        (lambda r: render_svg_scatter(r, {}), "Gesamtscore nach Dateigröße"),
        (lambda r: render_svg_scatter(r, {}, "m03_facts"), "M03 — Fakten-Extraktion nach Dateigröße"),
    )

    @staticmethod
    def _title(svg: str) -> str:
        return ElementTree.fromstring(svg).find("{http://www.w3.org/2000/svg}title").text

    @pytest.mark.parametrize(("models", "suffix"), [
        (("a-think-off", "b-think-none"), " · ohne Denken"),
        (("a-think-on", "b-think-high"), " · mit Denken"),
        (("a-think-off", "a-think-on"), " · mit und ohne Denken"),
        # Ohne Endung ist der Modus unbekannt; dann sagt der Titel nichts.
        (("a", "b"), ""),
        (("a-think-off", "b"), ""),
    ])
    def test_titel(self, tmp_path: Path, models: tuple[str, ...], suffix: str) -> None:
        for model in models:
            _run(m03_facts(oracle=True), f"mockllm/{model}", tmp_path)
        report = build(tmp_path)
        for render, title in self.RENDERERS:
            svg = render(report)
            # Überschrift im Bild und <title> stimmen überein.
            assert self._title(svg) == title + suffix
            assert f">{title + suffix}</text>" in svg

    def test_breite_reicht_fuer_den_titel(self, tmp_path: Path) -> None:
        # Eine Aufgabe, ein kurzer Name: Die Tabelle allein wäre schmaler als der Titel.
        _run(m03_facts(oracle=True), "mockllm/a-think-off", tmp_path)
        svg = render_svg_matrix(build(tmp_path))
        width = float(ElementTree.fromstring(svg).get("width"))
        assert width >= len("Score je Aufgabe und Modell · ohne Denken") * 8.6


@pytest.fixture(scope="module")
def effort_dir(tmp_path_factory) -> Path:
    """zeta ohne Endung im Namen: M03 mit reasoning_effort none richtig, mit
    high falsch. Dazu derselbe richtige Lauf als Claude ohne Angabe."""
    target = tmp_path_factory.mktemp("effort-logs")
    _run(m03_facts(oracle=True), "mockllm/zeta", target, reasoning_effort="none")
    _run(m03_facts(oracle=True), "mockllm/zeta", target, solver=_wrong(), reasoning_effort="high")
    claude = _run(m03_facts(oracle=True), "mockllm/zeta", target)
    log = read_eval_log(str(claude))
    log.eval.model = "anthropic/claude-haiku-5-5"
    write_eval_log(log, str(claude))
    return target


@pytest.fixture(scope="module")
def budget_dir(tmp_path_factory) -> Path:
    """lambda-think-on: M03 ohne Grenze falsch, mit Grenze 1024 richtig;
    lambda-think-off mit derselben Angabe richtig."""
    target = tmp_path_factory.mktemp("budget-logs")
    _run(m03_facts(oracle=True), "mockllm/lambda-think-on", target, solver=_wrong())
    _run(m03_facts(oracle=True), "mockllm/lambda-think-on", target,
         extra_body={"thinking_budget_tokens": 1024})
    _run(m03_facts(oracle=True), "mockllm/lambda-think-off", target,
         extra_body={"thinking_budget_tokens": 1024})
    return target


class TestDenkgrenzeAusDerAnfrage:
    """Die Denkgrenze steht nur im Anfragerumpf; Läufe mit und ohne trennt der Name."""

    def test_mit_und_ohne_grenze_nicht_in_einer_zelle(self, budget_dir: Path) -> None:
        # Gegenprobe: unter einem Namen gäbe es eine Zelle mit dem
        # schlechteren Lauf, 0.0, statt eines Abbruchs.
        with pytest.raises(ReportError, match="mehrere Varianten von mockllm/lambda im Modus „an“"):
            build(budget_dir)

    def test_fussnote_nennt_die_grenze(self, budget_dir: Path, tmp_path: Path) -> None:
        # Nur die Läufe mit Angabe; „aus“ behält trotz Angabe seinen Namen.
        for log in budget_dir.glob("*.eval"):
            if read_eval_log(str(log), header_only=True).eval.model_generate_config.extra_body:
                shutil.copy(log, tmp_path)
        text = render_markdown(build(tmp_path))
        assert "mockllm/lambda: aus = think-off; an = think-on-budget1024" in text
        assert "-budget<n> = Denkgrenze von n Tokens je Antwort" in text

    def test_ohne_grenze_keine_erklaerung(self, effort_dir: Path) -> None:
        assert "-budget<n>" not in render_markdown(build(effort_dir))
class TestDenkmodusAusDemLog:
    """Cloud-Modelle heißen mit und ohne Denken gleich; der Modus steht im Log."""

    def test_zwei_modi_zwei_zellen(self, effort_dir: Path) -> None:
        # Gegenprobe: unter einem Namen stünde eine Zelle mit dem schlechteren
        # Lauf, 0.0 — der richtige Lauf ohne Denken wäre verschwunden.
        zeta = _mrow(combined_matrix(build(effort_dir)), "mockllm/zeta")
        assert {c.label: cell.value for c, cell in zeta.cells.items()} == {
            "m03_facts · aus": 1.0, "m03_facts · an": 0.0,
        }

    def test_fussnote_nennt_die_einstellung(self, effort_dir: Path) -> None:
        text = render_markdown(build(effort_dir))
        assert "mockllm/zeta: aus = think-none; an = think-high" in text

    def test_claude_ohne_angabe_steht_unter_an(self, effort_dir: Path) -> None:
        claude = _mrow(combined_matrix(build(effort_dir)), "anthropic/claude-haiku-5-5")
        assert [c.label for c in claude.cells] == ["m03_facts · an"]
        assert claude.variants == ("anthropic/claude-haiku-5-5-think-adaptive",)

    def test_laufdaten_unter_demselben_namen(self, effort_dir: Path) -> None:
        # Läse `run_stats` weiter `log.eval.model`, fänden die Laufdaten ihre
        # Zeile nicht: drei Läufe, aber nur zwei Namen.
        stats = json.loads(render_json(build(effort_dir)))["run_stats"]["cells"]
        assert sorted(stats) == [
            "anthropic/claude-haiku-5-5-think-adaptive",
            "mockllm/zeta-think-high",
            "mockllm/zeta-think-none",
        ]
