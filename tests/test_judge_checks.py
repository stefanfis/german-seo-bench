"""Einzelproben für den Titel-Judge: Prompt, Auslesen, Metriken.

Alles ohne Modell. Die Metriken werden mit von Hand gebauten `SampleScore`s
gefüttert, denn genau diese Form übergibt Inspect, auch mit den
Datensatz-Metadaten, aus denen die Metriken die menschliche Klasse lesen.
"""

import math

import pytest
from inspect_ai.scorer import SampleScore, Score

from german_seo_bench.checks.judge import (
    EXAMPLE_TITLE,
    UNUSABLE,
    USABLE,
    judge_prompt,
    parse_verdict,
)
from german_seo_bench.metrics import (
    NO_VERDICT,
    bad_recall,
    balanced_bad,
    false_alarm,
    unusable_rate,
)
from german_seo_bench.tasks.m01_judge import load_ratings


class TestParseVerdict:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("brauchbar", USABLE),
            ("unbrauchbar", UNUSABLE),
            ("  Unbrauchbar.\n", UNUSABLE),
            ("Urteil: brauchbar", USABLE),
            ("nicht brauchbar", UNUSABLE),
            ("unbrauchbar\n\nDer Titel ergibt keinen Sinn.", UNUSABLE),
            ("<think>brauchbar oder unbrauchbar?</think>\nbrauchbar", USABLE),
            ("unbrauchbar – wirklich unbrauchbar", UNUSABLE),
        ],
    )
    def test_eindeutiges_urteil(self, text: str, expected: str) -> None:
        assert parse_verdict(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Der Titel liest sich gut.",
            "eher brauchbar als unbrauchbar",
            "brauchbarer Titel",  # kein ganzes Wort
            "<think>unbrauchbar</think>",
            "5",
        ],
    )
    def test_kein_eindeutiges_urteil(self, text: str) -> None:
        assert parse_verdict(text) is None

    def test_unbrauchbar_ist_nicht_auch_brauchbar(self) -> None:
        """Gegenprobe zum Wortgrenzen-Muster: „unbrauchbar“ enthält „brauchbar“."""
        assert parse_verdict("unbrauchbar") == UNUSABLE


class TestPrompt:
    def test_titel_steht_drin(self) -> None:
        assert "Gartenbank aus Holz" in judge_prompt("Gartenbank aus Holz")

    def test_nennt_klassen_und_ausschluesse(self) -> None:
        text = judge_prompt("x")
        assert f"{USABLE} =" in text and f"{UNUSABLE} =" in text
        for excluded in ("Zeichenzahl", "Suchbegriffe", "Thema"):
            assert excluded in text

    def test_fragt_nach_sinn_nicht_nur_nach_stil(self) -> None:
        # Eine Frage nur nach Lesefluss bewertete sprachlich sauberen Unsinn
        # gut (siehe Modul-Docstring in checks/judge.py).
        text = judge_prompt("x")
        # Die Anweisung selbst, nicht die Klassenbeschreibung darunter: Dort
        # steht „Sinn“ ohnehin, und ein Test über den ganzen Text bliebe grün,
        # wenn die Frage wieder nur nach dem Stil fragte.
        instruction = text.split("Klassen:")[0]
        assert "Sinn" in instruction
        assert "stimmig" in instruction
        assert EXAMPLE_TITLE in text

    def test_kein_titel_ist_unbrauchbar(self) -> None:
        """Satz und Fragment gehören zur Klasse „unbrauchbar“.

        Geprüft wird die Beschreibung dieser Klasse, nicht der ganze Prompt.
        Stünde „Satz“ nur irgendwo, bliebe der Test auch grün, wenn es in die
        falsche Klasse rutschte.
        """
        unusable = judge_prompt("x").split(f"{UNUSABLE} =")[1].split("Titel: x")[0]
        assert "kein Titel" in unusable
        assert "Satz" in unusable and "Fragment" in unusable

    def test_prompt_beispiel_steht_in_keinem_datensatz(self) -> None:
        """Ein Beispiel aus den Daten verriete dem Judge die Antwort auf diesen Fall.

        Geprüft wird jedes Wort-Trigramm des Beispiels gegen jeden bewerteten
        Titel. Ein wörtlicher Vergleich allein übersähe eine Abwandlung.
        """
        def trigrams(text: str) -> set[tuple[str, ...]]:
            words = text.lower().replace(",", "").replace(":", "").split()
            return set(zip(words, words[1:], words[2:]))

        example = trigrams(EXAMPLE_TITLE)
        for sample in load_ratings():
            shared = example & trigrams(sample.metadata["title"])
            assert not shared, (sample.metadata["title"], shared)


def _scores(pairs: list[tuple[float, int]]) -> list[SampleScore]:
    """(Anteil unbrauchbar laut Judge, menschliche Klasse) je Datensatz, wie Inspect sie übergibt."""
    return [
        SampleScore(score=Score(value=share), sample_id=i, sample_metadata={"human_score": human})
        for i, (share, human) in enumerate(pairs)
    ]


class TestSchlechtErkannt:
    def test_perfekter_judge(self) -> None:
        assert balanced_bad()(_scores([(1.0, 1), (0.0, 3)])) == 1.0

    def test_mehrheit_der_epochen_entscheidet(self) -> None:
        # 2 von 3 Urteilen „unbrauchbar“: erkannt. 1 von 3: nicht.
        assert bad_recall()(_scores([(2 / 3, 1), (1 / 3, 1)])) == 0.5

    def test_gleichstand_verwirft_nicht(self) -> None:
        """Verworfen wird nur bei Mehrheit, nicht bei 1 zu 1."""
        assert bad_recall()(_scores([(0.5, 1)])) == 0.0

    def test_mensch_vielleicht_ist_grauzone(self) -> None:
        """Menschliche Zweien zählen weder als unbrauchbar noch als brauchbar."""
        base = _scores([(1.0, 1), (0.0, 3)])
        with_maybes = _scores([(1.0, 1), (0.0, 3), (0.0, 2), (1.0, 2)])
        assert balanced_bad()(base) == balanced_bad()(with_maybes) == 1.0

    def test_fehlalarm(self) -> None:
        assert false_alarm()(_scores([(1.0, 3), (0.0, 3)])) == 0.5

    def test_nie_oder_immer_verwerfen_ergibt_einhalb(self) -> None:
        never = _scores([(0.0, 1), (0.0, 3)])
        always = _scores([(1.0, 1), (1.0, 3)])
        assert balanced_bad()(never) == balanced_bad()(always) == 0.5

    def test_ohne_urteil_faellt_heraus(self) -> None:
        """Gegenprobe: −1 ist weder Freispruch noch Verwerfung."""
        assert balanced_bad()(_scores([(1.0, 1), (NO_VERDICT, 1), (0.0, 3)])) == 1.0

    def test_verwerfungsquote(self) -> None:
        scores = _scores([(1.0, 3), (0.0, 3), (0.0, 1), (NO_VERDICT, 1)])
        assert unusable_rate()(scores) == pytest.approx(1 / 3)

    def test_verwerfungsquote_ohne_urteil_ist_nan(self) -> None:
        assert math.isnan(unusable_rate()(_scores([(NO_VERDICT, 1)])))
