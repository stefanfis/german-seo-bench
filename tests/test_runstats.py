"""Zusicherungen über die Laufdaten im Bericht (`german_seo_bench.runstats`).

Grundlage sind echte Logs aus Oracle-Läufen. Was ein Lauf ohne Server nicht
enthält — Modellaufrufe mit der Rohantwort von llama-server, die Serverabfrage
aus `/props` —, wird in genau der Form eingesetzt, in der Inspect und
`server.py` es schreiben. Jede Rechenregel hat ihre Gegenprobe als Zahl, die
bei der naheliegenden falschen Regel herauskäme.
"""

from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalLog, read_eval_log, write_eval_log
from inspect_ai.model import GenerateConfig, ModelCall, ModelOutput, ModelUsage

from german_seo_bench.report import build, render_json, render_runstats, write_outputs
from german_seo_bench.runstats import StatsCell, run_stats, sampling_by_model, sampling_text
from german_seo_bench.tasks.m03_facts import m03_facts

# So meldet llama-server die Vorgaben: float32, also 0.7 als 0.699999988079071.
PARAMS = {
    "temperature": 0.699999988079071,
    "top_k": 20,
    "top_p": 0.800000011920929,
    "min_p": 0.0,
    "repeat_penalty": 1.0,
    "presence_penalty": 0.0,
    "dry_multiplier": 0.0,
}


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("runstats-logs")
    log = inspect_eval(m03_facts(oracle=True), model="mockllm/alpha", log_dir=str(target),
                       limit=2, display="none")[0]
    assert log.status == "success", log.error
    return Path(log.location)


def _call(total: int, timings: dict | None) -> ModelEvent:
    """Ein Modellaufruf wie im Log; ohne `timings` wie jenseits der ersten fünf Aufrufe."""
    output = ModelOutput.from_content(model="m", content="x")
    output.usage = ModelUsage(input_tokens=total - 1, output_tokens=1, total_tokens=total)
    call = ModelCall(request={}, response={"timings": timings}) if timings else None
    return ModelEvent(model="m", input=[], tools=[], tool_choice="none",
                      config=GenerateConfig(), output=output, call=call)


def _timings(prompt_n: int, prompt_ms: float, predicted_n: int, predicted_ms: float) -> dict:
    return {"cache_n": 0, "prompt_n": prompt_n, "prompt_ms": prompt_ms,
            "predicted_n": predicted_n, "predicted_ms": predicted_ms,
            "prompt_per_second": prompt_n / prompt_ms * 1000,
            "predicted_per_second": predicted_n / predicted_ms * 1000}


def _log(source: Path, events: list[list[ModelEvent]], params: dict | None = PARAMS,
         n_ctx: int = 8192) -> EvalLog:
    """Das Oracle-Log mit eigenen Modellaufrufen und Serverangaben je Datensatz."""
    log = read_eval_log(str(source))
    for sample, mine in zip(log.samples, events):
        sample.events = [*sample.events, *mine]
        if params is not None:
            sample.metadata["server"] = {"params": params, "n_ctx": n_ctx}
    return log


class TestTempo:
    def test_nach_tokens_gewichtet_nicht_mittel_der_raten(self, source: Path) -> None:
        # 2 Tokens in 0,1 s (20/s) und 400 Tokens in 10 s (40/s): gewichtet
        # 402 / 10,1 s = 39,8/s. Das Mittel der Raten wäre 30/s.
        log = _log(source, [[_call(10, _timings(8, 100, 2, 100))],
                            [_call(10, _timings(8, 100, 400, 10_000))]])
        rate = StatsCell((run_stats(log, "x"),)).generation_rate
        assert rate == pytest.approx(402 / 10.1)
        assert rate != pytest.approx(30)

    def test_prompt_rate_ebenso(self, source: Path) -> None:
        log = _log(source, [[_call(10, _timings(100, 1000, 1, 10))],
                            [_call(10, _timings(9_900, 9_000, 1, 10))]])
        assert StatsCell((run_stats(log, "x"),)).prompt_rate == pytest.approx(1000)

    def test_ueber_laeufe_ebenso_gewichtet(self, source: Path) -> None:
        short = run_stats(_log(source, [[_call(10, _timings(8, 100, 2, 100))], []]), "a")
        long = run_stats(_log(source, [[_call(10, _timings(8, 100, 400, 10_000))], []]), "b")
        assert StatsCell((short, long)).generation_rate == pytest.approx(402 / 10.1)

    def test_aufrufe_ohne_timings_zaehlen_nicht_mit(self, source: Path) -> None:
        log = _log(source, [[_call(10, _timings(8, 100, 50, 1000)), _call(10, None)], []])
        cell = StatsCell((run_stats(log, "x"),))
        assert cell.generation_rate == pytest.approx(50)
        assert (cell.timed_calls, cell.calls) == (1, 2)
        assert cell.partly_timed

    def test_gegenprobe_alle_aufrufe_gemessen_ist_nicht_markiert(self, source: Path) -> None:
        log = _log(source, [[_call(10, _timings(8, 100, 50, 1000))], []])
        assert not StatsCell((run_stats(log, "x"),)).partly_timed

    def test_ohne_timings_keine_rate(self, source: Path) -> None:
        log = _log(source, [[_call(10, None)], []])
        cell = StatsCell((run_stats(log, "x"),))
        assert cell.generation_rate is None and cell.prompt_rate is None


class TestKontext:
    def test_groesster_aufruf_auch_ohne_timings(self, source: Path) -> None:
        # Der größte Aufruf hat keine Rohantwort mehr (jenseits der ersten
        # fünf) — er zählt trotzdem, über `usage`.
        log = _log(source, [[_call(500, _timings(8, 100, 2, 100)), _call(7000, None)],
                            [_call(900, None)]])
        assert run_stats(log, "x").max_context == 7000

    def test_ausgeschoepftes_fenster_wird_erkannt(self, source: Path) -> None:
        log = _log(source, [[_call(8192, None)], []], n_ctx=8192)
        assert StatsCell((run_stats(log, "x"),)).context_full

    def test_gegenprobe_ein_token_darunter_ist_nicht_voll(self, source: Path) -> None:
        log = _log(source, [[_call(8191, None)], []], n_ctx=8192)
        assert not StatsCell((run_stats(log, "x"),)).context_full


class TestSampling:
    def test_float32_wird_gerundet_und_neutrales_weggelassen(self) -> None:
        assert sampling_text(PARAMS) == "temp 0.7 · top_p 0.8 · top_k 20 · min_p 0"

    def test_wirkende_strafe_steht_dabei(self) -> None:
        assert sampling_text({**PARAMS, "repeat_penalty": 1.1}).endswith("repeat_penalty 1.1")

    def test_wert_des_laufs_ueberschreibt_den_server(self, source: Path) -> None:
        log = _log(source, [[], []])
        log.plan.config = GenerateConfig(temperature=0.0)
        assert run_stats(log, "x").sampling == ("temp 0 · top_p 0.8 · top_k 20 · min_p 0",)

    def test_gegenprobe_ohne_wert_des_laufs_gilt_der_server(self, source: Path) -> None:
        assert run_stats(_log(source, [[], []]), "x").sampling[0].startswith("temp 0.7 ")

    def test_ohne_serverabfrage_keine_angabe(self, source: Path) -> None:
        assert run_stats(_log(source, [[], []], params=None), "x").sampling == ()

    def test_wechsel_wird_nicht_verdeckt(self, source: Path) -> None:
        first = run_stats(_log(source, [[], []]), "a")
        second = run_stats(_log(source, [[], []], params={**PARAMS, "temperature": 1.0}), "b")
        settings = sampling_by_model([first, second])["mockllm/alpha"]
        assert len(settings) == 2


class TestLaufzeit:
    def test_aus_start_und_ende_des_laufs(self, source: Path) -> None:
        log = _log(source, [[], []])
        log.stats.started_at = "2026-10-20T13:00:00+00:00"
        log.stats.completed_at = "2026-10-20T13:02:30+00:00"
        assert run_stats(log, "x").seconds == 150

    def test_median_nicht_mittel(self, source: Path) -> None:
        runs = []
        for seconds in (60, 70, 600):
            log = _log(source, [[], []])
            log.stats.started_at = "2026-10-20T13:00:00+00:00"
            log.stats.completed_at = f"2026-10-20T13:{seconds // 60:02d}:{seconds % 60:02d}+00:00"
            runs.append(run_stats(log, "x"))
        assert StatsCell(tuple(runs)).seconds == 70


class TestBericht:
    def test_abschnitt_aus_den_logs(self, source: Path, tmp_path: Path) -> None:
        log = _log(source, [[_call(8192, _timings(8, 100, 50, 1000)), _call(10, None)], []])
        write_eval_log(log, str(tmp_path / source.name))
        report = build(tmp_path)
        text = render_runstats(report)
        assert "## Laufdaten" in text
        assert "temp 0.7 · top_p 0.8 · top_k 20 · min_p 0" in text
        assert "| mockllm/alpha | 50.0¹ | 50.0¹ |" in text
        assert "8192²" in text
        stats = render_json(report)
        assert '"generation_tokens_per_s": 50.0' in stats and '"context_full": true' in stats

    def test_ohne_serverdaten_striche_statt_zahlen(self, source: Path, tmp_path: Path) -> None:
        log = read_eval_log(str(source))
        write_eval_log(log, str(tmp_path / source.name))
        text = render_runstats(build(tmp_path))
        assert "| mockllm/alpha | — |" in text
        assert "¹" not in text and "²" not in text

    def test_steht_in_report_md(self, source: Path, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        write_eval_log(read_eval_log(str(source)), str(logs / source.name))
        written = write_outputs(build(logs), tmp_path / "out")
        markdown = next(p for p in written if p.name == "report.md").read_text()
        assert markdown.index("## Laufdaten") < markdown.index("Einzelergebnisse")
