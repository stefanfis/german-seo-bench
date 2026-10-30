"""Zusicherungen über `german_seo_bench.export_titles`, gegen echte M01-Logs.

Die Logs stammen aus zwei Oracle-Läufen (dieselben Titel zweimal, also
Dubletten) und zwei Läufen mit untauglicher Antwort: einmal leer, einmal ein
englischer Titel, an dem `german` scheitert. In der CSV dürfen nur die
Musterlösungen stehen, jede genau einmal.
"""

import csv
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from german_seo_bench.export_titles import collect_titles, main
from german_seo_bench.report import select_logs
from german_seo_bench.tasks.m01_judge import COLUMNS, load_ratings
from german_seo_bench.tasks.m01_titles import m01_titles

LIMIT = 3
ENGLISH = "The best garden bench for your outdoor living space today"


def _answering(content: str) -> Solver:
    @solver
    def fixed() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(model="fixed", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return fixed()


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("m01-logs")
    for _ in range(2):
        inspect_eval(m01_titles(oracle=True), model="none/none", log_dir=str(target), limit=LIMIT)
    for content in ("", ENGLISH):
        inspect_eval(m01_titles(oracle=True), model="none/none", log_dir=str(target),
                     limit=LIMIT, solver=_answering(content))
    return target


@pytest.fixture(scope="module")
def oracle_titles(log_dir: Path) -> set[str]:
    # Aus den Metadaten und nicht aus „dem ersten Log“: Laufen zwei Läufe in
    # derselben Sekunde an, entscheidet das Zufallssuffix im Dateinamen über
    # die Reihenfolge, und das erste Log könnte der englische Lauf sein.
    log = read_eval_log(str(next(log_dir.glob("*.eval"))))
    return {s.metadata["oracle"] for s in log.samples}


def test_nur_bestandene_titel_jeder_einmal(log_dir: Path, oracle_titles: set[str]) -> None:
    titles, counts = collect_titles(select_logs(log_dir))
    assert sorted(titles) == sorted(oracle_titles)
    assert ENGLISH not in titles
    assert counts == {
        "logs": 4,
        "logs_without_result": 0,
        "answers": 4 * LIMIT,
        "empty": LIMIT,
        "failed_checks": LIMIT,
        "unique": LIMIT,
    }


def test_englischer_titel_scheitert_wirklich_an_german(log_dir: Path) -> None:
    """Gegenprobe zum Filter: Der Test oben hätte sonst nichts bewiesen."""
    for path in log_dir.glob("*.eval"):
        log = read_eval_log(str(path))
        for sample in log.samples:
            if sample.output.completion == ENGLISH:
                assert sample.scores["title_scorer"].value["german"] == 0
                return
    pytest.fail("kein Lauf mit englischem Titel gefunden")


def test_reihenfolge_ist_fest(log_dir: Path) -> None:
    assert collect_titles(select_logs(log_dir))[0] == collect_titles(select_logs(log_dir))[0]


def test_csv_ist_lesbar_fuer_die_kalibrierung(log_dir: Path, tmp_path: Path,
                                              oracle_titles: set[str]) -> None:
    out = tmp_path / "titles.csv"
    assert main([str(log_dir), "--out", str(out)]) == 0
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == COLUMNS
    assert {row[0] for row in rows[1:]} == oracle_titles
    assert all(row[1] == "" for row in rows[1:])
    # Ist die erste Zeile bewertet, liest die Kalibrierung die Datei.
    rows[1][1] = "3"
    with out.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    assert len(load_ratings(str(out))) == 1


def test_ueberschreibt_keine_vorhandene_datei(log_dir: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "bewertet.csv"
    out.write_text("title,human_score\nA,5\n", encoding="utf-8")
    assert main([str(log_dir), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "title,human_score\nA,5\n"
    assert "nicht überschrieben" in capsys.readouterr().err
