"""Zusicherungen über `german_seo_bench.judge`, den getrennten Judge-Schritt, gegen echte M01-Logs.

Das Judge-Modell ist ein Mock. Gepatcht wird `get_model` im Modul, denn über
einen Modellnamen allein lässt sich mockllm keine feste Antwort mitgeben, und
genau einen Namen bekommt `--judge`.
"""

import hashlib
import itertools
import json
import shutil
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, get_model

import german_seo_bench.judge as judge_module
from german_seo_bench.checks.judge import JUDGE_VERSION
from german_seo_bench.export_titles import collect_titles
from german_seo_bench.report import build, select_logs
from german_seo_bench.tasks.m01_titles import m01_titles
from german_seo_bench.tasks.m03_facts import m03_facts


LIMIT = 3
CANDIDATE = "mockllm/kandidat"


@pytest.fixture(scope="module")
def source_log(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("m01")
    log = inspect_eval(m01_titles(oracle=True), model=CANDIDATE, log_dir=str(target),
                       limit=LIMIT, display="none")[0]
    return Path(log.location)


@pytest.fixture
def log_path(source_log: Path, tmp_path: Path) -> Path:
    """Je Test eine eigene Kopie, damit Sidecars sich nicht in die Quere kommen."""
    copy = tmp_path / source_log.name
    shutil.copy(source_log, copy)
    return copy


@pytest.fixture
def answers(monkeypatch):
    """`answers({"mockllm/a": "brauchbar"})` lässt diese Judges fest antworten."""

    def install(mapping: dict[str, str]) -> None:
        def fake(model=None, **kwargs):
            if model in mapping:
                outputs = itertools.repeat(ModelOutput.from_content(model, mapping[model]))
                return get_model("mockllm/model", custom_outputs=outputs)
            return get_model(model, **kwargs)

        monkeypatch.setattr(judge_module, "get_model", fake)

    return install


def _sidecar(log_path: Path) -> dict:
    return json.loads(judge_module.sidecar_path(log_path).read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestSidecar:
    def test_liegt_neben_dem_log_und_das_log_bleibt_unberuehrt(self, log_path, answers) -> None:
        answers({"mockllm/a": "Urteil: unbrauchbar"})
        before = _digest(log_path)
        path, _, _ = judge_module.run(log_path, "mockllm/a")
        assert path == log_path.with_suffix(".judges.json")
        assert path.exists()
        assert _digest(log_path) == before

    def test_inhalt(self, log_path, answers) -> None:
        answers({"mockllm/a": "Urteil: unbrauchbar"})
        judge_module.run(log_path, "mockllm/a")
        data = _sidecar(log_path)
        assert data["model"] == CANDIDATE
        assert data["task"] == "m01_titles"
        entry = data["judges"]["mockllm/a"]
        assert entry["judge_version"] == JUDGE_VERSION
        assert entry["summary"] == {
            "unusable_rate": 1.0, "parsed": 1.0, "judged": LIMIT, "titles": LIMIT,
        }
        assert len(entry["samples"]) == LIMIT
        assert all(s["verdict"] == "unbrauchbar" and s["title"] for s in entry["samples"])
        assert "endpoint" in entry and "server" in entry

    def test_rechte_wie_eine_gewoehnliche_datei(self, log_path, answers, tmp_path) -> None:
        """Nicht die 0600 von mkstemp, sondern was open() unter der umask ergibt."""
        answers({"mockllm/a": "brauchbar"})
        path, _, _ = judge_module.run(log_path, "mockllm/a")
        plain = tmp_path / "gewoehnlich.txt"
        plain.write_text("x")
        assert path.stat().st_mode & 0o777 == plain.stat().st_mode & 0o777

    def test_zweiter_judge_kommt_dazu(self, log_path, answers) -> None:
        answers({"mockllm/a": "brauchbar", "mockllm/b": "unbrauchbar"})
        judge_module.run(log_path, "mockllm/a")
        judge_module.run(log_path, "mockllm/b")
        judges = _sidecar(log_path)["judges"]
        assert set(judges) == {"mockllm/a", "mockllm/b"}
        assert judges["mockllm/a"]["summary"]["unusable_rate"] == 0.0
        assert judges["mockllm/b"]["summary"]["unusable_rate"] == 1.0

    def test_derselbe_judge_ersetzt_sein_urteil(self, log_path, answers) -> None:
        answers({"mockllm/a": "brauchbar"})
        judge_module.run(log_path, "mockllm/a")
        answers({"mockllm/a": "unbrauchbar"})
        _, _, replaced = judge_module.run(log_path, "mockllm/a")
        assert replaced
        assert _sidecar(log_path)["judges"]["mockllm/a"]["summary"]["unusable_rate"] == 1.0

    def test_ohne_urteil_ist_null_nicht_freispruch(self, log_path, answers) -> None:
        """Ein fehlendes Urteil ist kein Freispruch."""
        answers({"mockllm/a": "Liest sich gut."})
        judge_module.run(log_path, "mockllm/a")
        entry = _sidecar(log_path)["judges"]["mockllm/a"]
        assert all(s["verdict"] is None for s in entry["samples"])
        assert entry["summary"]["unusable_rate"] is None
        assert entry["summary"]["parsed"] == 0.0

    def test_anteil_nur_ueber_abgegebene_urteile(self) -> None:
        samples = [
            {"title": "A", "verdict": "unbrauchbar"},
            {"title": "B", "verdict": "brauchbar"},
            {"title": "C", "verdict": None},
            {"title": "", "verdict": None},  # leerer Titel zählt gar nicht
        ]
        # Mit dem fehlenden Urteil als 0 wäre es 2.0.
        assert judge_module.summarize(samples) == {
            # Mit dem fehlenden Urteil als „brauchbar“ wäre es 1/3.
            "unusable_rate": 0.5, "parsed": 2 / 3, "judged": 2, "titles": 3,
        }


class TestFortschritt:
    def test_jeder_titel_meldet_sich_einmal(self, log_path, answers) -> None:
        answers({"mockllm/a": "brauchbar"})
        calls = []
        judge_module.run(log_path, "mockllm/a", progress=lambda *args: calls.append(args))
        assert [done for done, _, _ in calls] == list(range(1, LIMIT + 1))
        assert all(total == LIMIT for _, total, _ in calls)
        assert all(result["verdict"] == "brauchbar" for _, _, result in calls)

    def test_anzeige_auf_stderr_stdout_bleibt_zusammenfassung(
        self, log_path, answers, capsys
    ) -> None:
        answers({"mockllm/a": "Liest sich gut."})
        assert judge_module.main([str(log_path), "--judge", "mockllm/a"]) == 0
        out, err = capsys.readouterr()
        lines = [line for line in err.splitlines() if line.strip().startswith("[")]
        assert len(lines) == LIMIT
        assert lines[-1].strip().startswith(f"[{LIMIT}/{LIMIT}] ?")
        assert "[" not in out

    def test_zeichen_fuer_leeren_titel(self, capsys) -> None:
        judge_module.print_progress(1, 2, {"title": "", "verdict": None})
        assert capsys.readouterr().err.strip() == "[1/2] ·  (leer)"


class TestAbbrueche:
    def test_richter_ist_nicht_der_kandidat(self, log_path) -> None:
        with pytest.raises(judge_module.SelfJudgeError):
            judge_module.run(log_path, CANDIDATE)
        assert not judge_module.sidecar_path(log_path).exists()

    def test_sidecar_eines_anderen_laufs(self, log_path, answers) -> None:
        judge_module.sidecar_path(log_path).write_text(
            json.dumps({"eval_id": "fremd", "judges": {}}), encoding="utf-8"
        )
        answers({"mockllm/a": "brauchbar"})
        with pytest.raises(judge_module.JudgeError, match="anderen Lauf"):
            judge_module.run(log_path, "mockllm/a")

    def test_nur_m01(self, tmp_path) -> None:
        log = inspect_eval(m03_facts(oracle=True), model=CANDIDATE, log_dir=str(tmp_path),
                           limit=1, display="none")[0]
        with pytest.raises(judge_module.JudgeError, match="wertet nur m01_titles"):
            judge_module.run(Path(log.location), "mockllm/a")

    def test_main_rueckgabewerte(self, log_path, answers, capsys) -> None:
        answers({"mockllm/a": "brauchbar"})
        assert judge_module.main([str(log_path), "--judge", "mockllm/a"]) == 0
        assert judge_module.main([str(log_path), "--judge", CANDIDATE]) == 3
        assert "Übersprungen" in capsys.readouterr().err


class TestNachbarn:
    def test_auswertung_und_export_ignorieren_den_sidecar(self, log_path, answers) -> None:
        answers({"mockllm/a": "brauchbar"})
        judge_module.run(log_path, "mockllm/a")
        assert select_logs(log_path.parent) == [log_path]
        assert build(log_path.parent).rows[0].cells["m01_titles"].n == 1
        titles, counts = collect_titles(select_logs(log_path.parent, "*m01-titles*"))
        assert counts["logs"] == 1
