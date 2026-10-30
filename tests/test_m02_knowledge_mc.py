"""M02: Datensatz, Auslesen des Buchstabens, Wertung und Oracle-Lauf."""

import collections
import json
from importlib import resources

import pytest
from inspect_ai import eval as inspect_eval

from german_seo_bench.checks.choice import (
    CHOICES,
    LETTERS,
    correct_letter,
    grade,
    parse_choice,
    validate_item,
)
from german_seo_bench.tasks.m02_knowledge_mc import (
    DATA,
    FLAGS,
    GROUPS,
    m02_knowledge_mc,
    prompt,
)
from conftest import group_metrics, group_names


@pytest.fixture(scope="module")
def raw() -> list[dict]:
    text = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
    return json.loads(text)["samples"]


@pytest.fixture(scope="module")
def items(raw) -> list[dict]:
    return [{"id": r["id"], **r["input"], **r["reference"]} for r in raw]


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("logs"))


# Fragen aus dem Grundbestand, die ohne Quelle aufgenommen sind. Siehe
# `test_jede_frage_hat_eine_quelle_ausser_dem_altbestand`.
LEGACY_WITHOUT_SOURCE = {
    "geo_de-fluss-dresden",
    "geo_de-fluss-schweinfurt",
    "geo_de-ilz",
    "geo_de-rb-dortmund",
    "geo_de-rb-mittelfranken",
    "geo_de-sieg-muendung",
    "geo_de-wertach",
    "geo_welt-iseosee",
    "geo_welt-nowosibirsk",
    "geschichte-bundespraesident-1",
    "geschichte-kanzler-2",
    "geschichte-moedlareuth",
    "marken-1860-fussball",
    "marken-4k",
    "marken-borussia",
    "marken-football-muenchen",
    "natwiss-eisen-neutronen",
    "natwiss-ganymed",
    "recht-bayern-bundesrat",
    "recht-gewaehrleistung-frist",
}


def _item(**changes) -> dict:
    item = {
        "id": "probe-ilz",
        "level": 3,
        "topic": "probe",
        "question": "In welchen Fluss mündet die Ilz?",
        "choices": ["Inn", "Donau", "Isar", "Regen", "Vils", "Naab", "Rott", "Elbe", "Rhein", "Moldau"],
        "answer": "Donau",
    }
    item.update(changes)
    return item


class TestDatensatz:
    def test_jedes_item_ist_gueltig(self, items) -> None:
        errors = {item["id"]: validate_item(item) for item in items}
        assert not {k: v for k, v in errors.items() if v}

    def test_ids_sind_eindeutig(self, items) -> None:
        ids = [item["id"] for item in items]
        assert len(ids) == len(set(ids))

    def test_nur_bekannte_felder(self, raw) -> None:
        for record in raw:
            assert set(record) == {"id", "input", "reference"}
            assert set(record["input"]) == {"question", "choices"}
            assert {"level", "topic", "answer"} <= set(record["reference"])
            assert set(record["reference"]) <= {"level", "topic", "answer", "source"}

    def test_jede_frage_hat_eine_quelle_ausser_dem_altbestand(self, items) -> None:
        """Neue Fragen kommen nur mit Quelle in den Datensatz.

        Der Altbestand ist von Hand geprüft und trägt keine Quelle. Die
        Liste darf nur schrumpfen: Wer eine Frage ohne Quelle hinzufügt,
        müsste sie hier eintragen — und genau das soll auffallen.
        """
        missing = {item["id"] for item in items if "source" not in item}
        assert missing <= LEGACY_WITHOUT_SOURCE, sorted(missing - LEGACY_WITHOUT_SOURCE)

    def test_loesungsbuchstaben_sind_verteilt(self, items) -> None:
        """Steht die Lösung gehäuft an einer Stelle, punktet ein Modell, das
        immer denselben Buchstaben nennt, über dem Rateniveau."""
        counts = collections.Counter(correct_letter(item) for item in items)
        expected = len(items) / CHOICES
        assert set(counts) == set(LETTERS), counts
        assert max(counts.values()) <= 2 * expected, counts

    def test_laenge_verraet_die_antwort_nicht(self, items) -> None:
        """Ist die richtige Option auffällig oft die längste, punktet „nimm die
        ausführlichste" über dem Rateniveau. Erwartet sind 10 %."""
        longest = [
            item["id"]
            for item in items
            if all(len(item["answer"]) > len(c) for c in item["choices"] if c != item["answer"])
        ]
        assert len(longest) <= 0.2 * len(items), longest


class TestSchemaPruefung:
    def test_probe_ist_gueltig(self) -> None:
        assert validate_item(_item()) == []

    @pytest.mark.parametrize(
        "changes, needle",
        [
            ({"choices": _item()["choices"][:9]}, "Optionen"),
            ({"answer": "Main"}, "nicht genau einmal"),
            ({"choices": ["Inn", "Donau", "Isar", "Regen", "Vils", "Naab", "Rott", "Elbe", "Rhein", "inn"]}, "Faltung"),
            ({"choices": ["Inn", "Donau", "Isar", "Regen", "Vils", "Naab", "Rott", "Elbe", "Rhein", "A"]}, "Lösungsbuchstabe"),
            ({"question": "In welchen Fluss mündet derzeit die Ilz?"}, "zeitrelativ"),
            ({"question": "Mündet die Ilz in die Donau?"}, "in der Frage"),
            ({"level": 6}, "level"),
            ({"topic": "reisen"}, "Thema"),
            ({"source": "http://example.org"}, "https"),
        ],
    )
    def test_verstoss_wird_gemeldet(self, changes, needle) -> None:
        errors = validate_item(_item(**changes))
        assert any(needle in e for e in errors), errors


class TestReferenzSchranke:
    def test_prompt_zeigt_frage_und_alle_optionen(self, raw) -> None:
        for record in raw:
            text = prompt(record["input"])
            assert text.startswith(record["input"]["question"])
            for i, choice in enumerate(record["input"]["choices"]):
                assert f"\n{LETTERS[i]}) {choice}\n" in text

    def test_musterloesung_ist_der_buchstabe_der_antwort(self) -> None:
        for sample in m02_knowledge_mc().dataset:
            m = sample.metadata
            assert m["choices"][LETTERS.index(m["oracle"])] == m["answer"]


class TestAuslesen:
    CHOICES_PROBE = _item()["choices"]

    @pytest.mark.parametrize(
        "answer",
        ["B", "b", "B)", "(B)", "B.", "**B**", "B) Donau", "B – Donau", "B, die Donau",
         "Antwort: B", "Die richtige Antwort ist B.", "Donau", "Donau."],
    )
    def test_festgelegt(self, answer: str) -> None:
        assert parse_choice(answer, self.CHOICES_PROBE) == "B"

    @pytest.mark.parametrize(
        "answer",
        ["", "A oder B", "A, B", "A/B", "B) oder C)", "Ich denke, es ist B",
         "I think it's B", "Die Donau oder der Inn", "weiß ich nicht"],
    )
    def test_nicht_festgelegt(self, answer: str) -> None:
        assert parse_choice(answer, self.CHOICES_PROBE) is None

    def test_buchstabe_geht_vor_text(self) -> None:
        # „A) Donau" nennt Buchstabe und Text, die nicht zusammenpassen. Gewertet
        # wird der Buchstabe — die Aufgabe verlangt ihn, nicht den Text.
        assert parse_choice("A) Donau", self.CHOICES_PROBE) == "A"


class TestWertung:
    def test_richtig(self) -> None:
        verdict = grade(_item(), "B")
        assert verdict.points == 1.0 and not verdict.invalid

    def test_falsch(self) -> None:
        verdict = grade(_item(), "A")
        assert verdict.points == 0.0 and not verdict.invalid
        assert "Inn" in verdict.reason and "Donau" in verdict.reason

    def test_unentschlossen(self) -> None:
        verdict = grade(_item(), "A oder B")
        assert verdict.points == 0.0 and verdict.invalid

    def test_beispiele_freier_antworten(self) -> None:
        """Was als freie Antwort an einer Wortliste scheitert, ist hier eindeutig."""
        item = _item(
            question="Wie hieß der zweite Bundeskanzler der Bundesrepublik Deutschland?",
            choices=["Konrad Adenauer", "Ludwig Erhard", "Kurt Georg Kiesinger", "Willy Brandt",
                     "Helmut Schmidt", "Helmut Kohl", "Theodor Heuss", "Heinrich Lübke",
                     "Franz Josef Strauß", "Erich Mende"],
            answer="Ludwig Erhard",
        )
        assert grade(item, "B").points == 1.0
        assert grade(item, "A").points == 0.0


class TestOracle:
    @pytest.fixture(scope="class")
    @classmethod
    def log(cls, log_dir: str):
        return inspect_eval(m02_knowledge_mc(oracle=True), model="none/none", log_dir=log_dir)[0]

    def test_lauf_erfolgreich(self, log) -> None:
        assert log.status == "success", log.error

    def test_alle_datensaetze_bewertet(self, log, items) -> None:
        assert len(log.samples) == len(items)

    def test_score_ist_eins(self, log) -> None:
        score = next(s for s in log.results.scores if s.name == "score")
        assert score.metrics["mean"].value == 1.0

    @pytest.mark.parametrize("flag", FLAGS)
    def test_flags_sind_null(self, log, flag: str) -> None:
        score = next(s for s in log.results.scores if s.name == flag)
        assert score.metrics["mean"].value == 0.0

    @pytest.mark.parametrize("group", GROUPS)
    def test_profile_im_log(self, log, items, group: str) -> None:
        assert group_names(log, group) == {str(item[group]) for item in items}
        columns = group_metrics(log, group)
        assert columns
        assert all(value == 1.0 for value in columns.values()), columns

    def test_gegenprobe_immer_derselbe_buchstabe(self, log_dir: str, items) -> None:
        """Immer „A" trifft genau die Fragen, deren Lösung bei A steht."""
        log = inspect_eval(
            m02_knowledge_mc(oracle=True), model="none/none", log_dir=log_dir,
            solver=_fixed_answer_solver("A"),
        )[0]
        score = next(s for s in log.results.scores if s.name == "score")
        expected = sum(correct_letter(item) == "A" for item in items) / len(items)
        assert score.metrics["mean"].value == pytest.approx(expected)
        assert score.metrics["mean"].value < 1.0

    def test_gegenprobe_ohne_buchstaben(self, log_dir: str) -> None:
        log = inspect_eval(
            m02_knowledge_mc(oracle=True), model="none/none", log_dir=log_dir,
            solver=_fixed_answer_solver("Das weiß ich nicht."),
        )[0]
        score = next(s for s in log.results.scores if s.name == "score")
        invalid = next(s for s in log.results.scores if s.name == "invalid")
        assert score.metrics["mean"].value == 0.0
        assert invalid.metrics["mean"].value == 1.0


def _fixed_answer_solver(answer: str):
    """Antwortet auf jeden Datensatz mit derselben Zeichenkette."""
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def fixed_answer() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(model="broken", content=answer)
            state.messages.append(state.output.message)
            return state

        return solve

    return fixed_answer()
