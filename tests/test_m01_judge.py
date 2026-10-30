"""Zusicherungen über die Kalibrierungsaufgabe `m01_judge`.

Die mitgelieferte CSV ist bis zur Handbewertung leer. Die Läufe hier nutzen
deshalb eine kleine eigene Datei über `-T data=…`, denselben Weg, auf dem man
auch eine halb bewertete Arbeitsdatei prüft.
"""

import math
from importlib import resources
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from german_seo_bench.checks.judge import JUDGE_VERSION, judge_prompt
from german_seo_bench.tasks.m01_judge import COLUMNS, DATA, VERSION, load_ratings, m01_judge

RATED = """title,human_score
Gartenbank aus Holz: So wählen Sie das richtige Modell,3
"Birnen ernten, lagern und genießen",3
Pool im Sommer: pH-Wert und Umwälzung im Griff,3
Sommer Garten Pool pH Wert Umwälzung,2
Kalkrand Bad Reparatur einfach machen jetzt,1
Noch nicht bewerteter Titel,
"""


@pytest.fixture(scope="module")
def csv_file(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("judge") / "ratings.csv"
    path.write_text(RATED, encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("eval-logs"))


class TestDatensatz:
    def test_mitgelieferte_datei_hat_den_kopf(self) -> None:
        # Bis zur Handbewertung ohne Zeilen, aber mit dem Vertrag, den der
        # Export schreibt. `load_ratings` bricht bei falschem Kopf ab.
        first = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
        assert first.splitlines()[0] == ",".join(COLUMNS)

    def test_unbewertete_zeilen_fehlen(self, csv_file: str) -> None:
        dataset = load_ratings(csv_file)
        assert len(dataset) == 5
        assert all(s.metadata["title"] != "Noch nicht bewerteter Titel" for s in dataset)

    def test_prompt_ist_der_des_m01_judges(self, csv_file: str) -> None:
        sample = load_ratings(csv_file)[0]
        assert sample.input == judge_prompt(sample.metadata["title"])

    def test_ids_haengen_am_titel_nicht_an_der_zeile(self, csv_file: str, tmp_path: Path) -> None:
        lines = RATED.splitlines()
        reordered = tmp_path / "reordered.csv"
        reordered.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n", encoding="utf-8")
        ids = {s.metadata["title"]: s.id for s in load_ratings(csv_file)}
        assert ids == {s.metadata["title"]: s.id for s in load_ratings(str(reordered))}

    def test_ohne_bewertung_klare_meldung(self, tmp_path: Path) -> None:
        path = tmp_path / "unbewertet.csv"
        path.write_text("title,human_score\nA,\n", encoding="utf-8")
        with pytest.raises(ValueError, match="keine bewerteten Titel"):
            load_ratings(str(path))

    @pytest.mark.parametrize(
        "content, message",
        [
            ("title,score\nA,3\n", "Spalten"),
            (f"{','.join(COLUMNS)}\nA,4\n", "keine Klasse"),
            (f"{','.join(COLUMNS)}\nA,2.5\n", "keine Klasse"),
            (f"{','.join(COLUMNS)}\nA,brauchbar\n", "keine Klasse"),
            (f"{','.join(COLUMNS)}\nA,3\nA,1\n", "doppelt"),
        ],
    )
    def test_fehler_der_datei_brechen_ab(self, tmp_path: Path, content: str, message: str) -> None:
        path = tmp_path / "bad.csv"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_ratings(str(path))


def _answering(content: str) -> Solver:
    @solver
    def fixed() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(model="fixed", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return fixed()


def _metrics(log) -> dict[str, float]:
    return {
        f"{s.name}/{name.rsplit('/', 1)[-1]}": m.value
        for s in log.results.scores
        for name, m in s.metrics.items()
    }


class TestVersion:
    def test_ganze_zahl_wie_bei_jeder_aufgabe(self, csv_file: str) -> None:
        version = m01_judge(data=csv_file).version
        assert version == VERSION
        assert isinstance(version, int)

    def test_steigt_mit_dem_prompt(self) -> None:
        """Absichtlich als festes Paar.

        Wer `JUDGE_VERSION` hochzählt, landet hier und muss entscheiden, ob
        `VERSION` mitsteigt. Ohne diesen Stolperdraht misst die Aufgabe nach
        einem neuen Prompt einen anderen Judge unter derselben Versionsnummer.
        """
        assert (JUDGE_VERSION, VERSION) == (1, 1)


class TestOracle:
    """Die menschliche Bewertung als Urteil eingesetzt muss die Leitzahl 1.00 ergeben."""

    def test_oracle_ist_eins(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(oracle=True, data=csv_file), model="none/none",
                           log_dir=log_dir)[0]
        assert log.status == "success", log.error
        metrics = _metrics(log)
        assert metrics["verdict/balanced_bad"] == 1.0
        assert metrics["verdict/bad_recall"] == 1.0
        assert metrics["verdict/false_alarm"] == 0.0
        assert metrics["parsed/mean"] == 1.0

    def test_leitwert_ist_schlecht_erkannt(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(oracle=True, data=csv_file), model="none/none",
                           log_dir=log_dir)[0]
        headline = log.eval.headline_metric
        assert headline.metric == "german_seo_bench/balanced_bad"
        entry = next(s for s in log.results.scores if s.name == headline.score)
        assert headline.metric in entry.metrics

    def test_gegenprobe_konstantes_urteil(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           solver=_answering("brauchbar"))[0]
        metrics = _metrics(log)
        assert metrics["parsed/mean"] == 1.0
        # Nie „unbrauchbar“: nichts gefunden, kein Fehlalarm, also 0.5.
        assert metrics["verdict/bad_recall"] == 0.0
        assert metrics["verdict/false_alarm"] == 0.0
        assert metrics["verdict/balanced_bad"] == 0.5
        assert metrics["verdict/unusable_rate"] == 0.0

    def test_gegenprobe_immer_schlecht(self, csv_file: str, log_dir: str) -> None:
        """Strenge allein darf sich nicht auszahlen: immer 1 ergibt ebenfalls 0.5."""
        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           solver=_answering("unbrauchbar"))[0]
        metrics = _metrics(log)
        assert metrics["verdict/bad_recall"] == 1.0
        assert metrics["verdict/false_alarm"] == 1.0
        assert metrics["verdict/balanced_bad"] == 0.5
        assert metrics["verdict/unusable_rate"] == 1.0

    def test_gegenprobe_ohne_urteil(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           solver=_answering("Liest sich gut."))[0]
        metrics = _metrics(log)
        assert metrics["parsed/mean"] == 0.0
        assert math.isnan(metrics["verdict/balanced_bad"])


@solver
def _by_epoch(answers: dict[int, str]) -> Solver:
    """Antwortet je Epoche anders, damit die Reduktion etwas zu mitteln hat."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="fixed", content=answers[state.epoch])
        state.messages.append(state.output.message)
        return state

    return solve


class TestEpochen:
    """`--epochs 3`: drei Urteile je Titel, reduziert ohne die 0 für „kein Urteil“."""

    ANSWERS = {1: "unbrauchbar", 2: "Liest sich gut.", 3: "unbrauchbar"}

    def _reduced(self, log) -> list[dict]:
        return [s.value for s in log.reductions[0].samples]

    def test_fehlendes_urteil_wird_nicht_mitgemittelt(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           epochs=3, solver=_by_epoch(self.ANSWERS))[0]
        assert log.eval.config.epochs_reducer == ["verdict_share"]
        for value in self._reduced(log):
            # Mit dem −1 des fehlenden Urteils gemittelt wären es
            # (1 − 1 + 1) / 3 = 0.33, also keine Mehrheit mehr.
            assert value["verdict"] == 1.0
            assert value["parsed"] == pytest.approx(2 / 3)

    def test_gegenprobe_standard_reducer_mittelt_die_null(
        self, csv_file: str, log_dir: str
    ) -> None:
        """Belegt, dass es den eigenen Reducer braucht: Inspects `mean` mittelt das −1 mit."""
        from inspect_ai import Epochs

        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           epochs=Epochs(3, "mean"), solver=_by_epoch(self.ANSWERS))[0]
        assert all(v["verdict"] == pytest.approx(1 / 3) for v in self._reduced(log))

    def test_ohne_jedes_urteil_bleibt_kein_urteil(self, csv_file: str, log_dir: str) -> None:
        log = inspect_eval(m01_judge(data=csv_file), model="none/none", log_dir=log_dir,
                           epochs=2, solver=_by_epoch({1: "gut", 2: "schön"}))[0]
        assert all(v["verdict"] == -1 and v["parsed"] == 0.0 for v in self._reduced(log))
