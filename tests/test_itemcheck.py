"""Zusicherungen über die Trennschärfe-Auswertung.

Das Werkzeug entscheidet mit, welche Datensätze ausgetauscht und wie die Stufen
geschnitten werden. Ein Rechenfehler darin fällt nirgends auf — er erzeugt eine
plausible Liste, die niemand nachrechnet. Deshalb ist der Kern eine reine
Funktion, und deshalb steht hier mehr als ein Durchlauf.
"""

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput

from german_seo_bench.itemcheck import (
    MIN_MODELS,
    Observation,
    current_levels,
    read_logs,
    render,
    suggest_levels,
    summarize,
)
from german_seo_bench.tasks.m01_titles import m01_titles


def obs(model: str, item: str, *results: bool) -> list[Observation]:
    return [Observation(model=model, item=item, passed=r) for r in results]


class TestSummarize:
    def test_jedes_modell_zaehlt_gleich_schwer(self) -> None:
        """Der Punkt, an dem eine naive Auszählung falsch liegt.

        Ein Modell mit fünf Läufen darf die Aussage über ein Item nicht gegen
        ein Modell mit einem Lauf kippen. Hier löst A den Datensatz fünfmal,
        B verfehlt ihn einmal — das Mittel über die MODELLE ist 0.50, das
        Mittel über die VERSUCHE wäre 0.83.
        """
        stats = summarize(obs("a", "x", True, True, True, True, True) + obs("b", "x", False))
        assert stats[0].rate == pytest.approx(0.50)
        assert stats[0].attempts == 6

    def test_referenzverdacht_nur_wenn_niemand_trifft(self) -> None:
        stats = summarize(
            obs("a", "x", False) + obs("b", "x", False) + obs("c", "x", False)
        )
        assert stats[0].rate == 0.0
        assert stats[0].verdict == "Referenz?"

    def test_stumpf_wenn_alle_immer_treffen(self) -> None:
        stats = summarize(obs("a", "x", True) + obs("b", "x", True) + obs("c", "x", True))
        assert stats[0].verdict == "stumpf"

    def test_wackelig_schlaegt_die_anderen_befunde(self) -> None:
        # Ein Modell, das denselben Datensatz mal löst und mal nicht, macht die
        # Einzelmessung wertlos — das ist die wichtigere Aussage als die Quote.
        stats = summarize(
            obs("a", "x", True, False) + obs("b", "x", True) + obs("c", "x", True)
        )
        assert stats[0].wobbly == ["a"]
        assert stats[0].verdict == "wackelig"

    def test_unter_drei_modellen_kein_urteil(self) -> None:
        """Die Gegenprobe zur teuersten Fehlbedienung.

        Ein Datensatz, den EIN Modell nie löst, ist ein schweres Item und kein
        Referenzfehler. Spräche das Werkzeug hier ein Urteil, korrigierte
        jemand eine richtige Referenz.
        """
        stats = summarize(obs("a", "x", False) + obs("b", "x", False))
        assert stats[0].rate == 0.0
        assert stats[0].models < MIN_MODELS
        assert stats[0].verdict == ""

    def test_reihenfolge_ist_stabil(self) -> None:
        # Gleichstand fällt nach Name. Sonst ergäbe derselbe Datenbestand zwei
        # verschiedene Stufenvorschläge, je nach Reihenfolge der Log-Dateien.
        first = summarize(obs("a", "b_item", True) + obs("a", "a_item", True))
        second = summarize(obs("a", "a_item", True) + obs("a", "b_item", True))
        assert [s.item for s in first] == [s.item for s in second] == ["a_item", "b_item"]


class TestSuggestLevels:
    def test_baender_sind_gleich_gross(self) -> None:
        stats = summarize([Observation("m", f"i{n:02d}", n > 4) for n in range(10)])
        levels = suggest_levels(stats, bands=5)
        assert sorted(levels.values()) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]

    def test_rest_landet_in_den_vorderen_baendern(self) -> None:
        # 29 Datensätze auf 5 Bänder: 6/6/6/6/5, nicht 5/5/5/5/9. Ein Restband
        # mit neun Items wäre keine Stufe mehr, sondern ein Sammelbecken.
        stats = summarize([Observation("m", f"i{n:02d}", True) for n in range(29)])
        sizes = [sum(1 for v in suggest_levels(stats).values() if v == lv) for lv in range(1, 6)]
        assert sizes == [6, 6, 6, 6, 5]

    def test_schwerstes_item_kommt_in_die_hoechste_stufe(self) -> None:
        stats = summarize(
            obs("m", "leicht", True) + obs("m", "mittel", True, False) + obs("m", "schwer", False)
        )
        levels = suggest_levels(stats, bands=3)
        assert levels["leicht"] == 1 and levels["schwer"] == 3

    def test_leere_eingabe_stuerzt_nicht_ab(self) -> None:
        assert suggest_levels([]) == {}


class TestCurrentLevels:
    def test_stufen_kommen_aus_der_datendatei(self) -> None:
        # Bewusst aus der heutigen Datei und nicht aus dem Log: Ein Log trägt
        # die Einteilung zur Laufzeit, und die Frage lautet, ob die heutige zu
        # den Messungen passt.
        levels = current_levels("m01_titles")
        assert set(levels.values()) == {1, 2, 3, 4, 5}

    def test_unbekannte_aufgabe_ergibt_leere_zuordnung(self) -> None:
        assert current_levels("m99_gibt_es_nicht") == {}


class TestLogsLesen:
    """Der Durchlauf von der Log-Datei bis zur Tabelle — ohne Netz."""

    @pytest.fixture(scope="class")
    @classmethod
    def logs(cls, tmp_path_factory):
        log_dir = tmp_path_factory.mktemp("itemcheck-logs")
        # Zwei "Modelle" mit verschiedenen Antworten: eines antwortet brauchbar
        # (die Musterlösung des ersten Datensatzes), eines unbrauchbar.
        for answer in ("Gartenbank aus Holz: robuste Sitzbank für den Garten", "nope"):
            inspect_eval(
                m01_titles(),
                model="mockllm/model",
                model_args={
                    "custom_outputs": (
                        ModelOutput.from_content(model="mockllm", content=answer)
                        for _ in range(100)
                    )
                },
                log_dir=str(log_dir),
                limit=3,
            )
        # Ein Oracle-Lauf daneben, der NICHT mitgezählt werden darf.
        inspect_eval(
            m01_titles(oracle=True), model="none/none", log_dir=str(log_dir), limit=3
        )
        return sorted(log_dir.glob("*.eval"))

    def test_drei_logs_geschrieben(self, logs) -> None:
        assert len(logs) == 3

    def test_oracle_laeufe_zaehlen_nicht_mit(self, logs) -> None:
        """Sonst meldete jeder Datensatz "stumpf".

        Ein Oracle-Lauf ist per Konstruktion 1.00. Zählte er mit, hübschte er
        jede Quote auf — und zwar am stärksten dort, wo am wenigsten echte
        Läufe vorliegen.
        """
        collected = read_logs(logs, "m01_titles")
        models = {o.model for obs_list in collected.values() for o in obs_list}
        assert models == {"mockllm/model"}

    def test_nach_aufgabenversion_getrennt(self, logs) -> None:
        # Zahlen aus zwei Aufgabenversionen unter einem Namen zusammenzuzählen
        # ist der Fehler, gegen den `Task.version` überhaupt existiert.
        collected = read_logs(logs, "m01_titles")
        assert all(isinstance(version, int) for _, version in collected)
        assert len(collected) == 1

    def test_tabelle_nennt_die_zahl_der_modelle(self, logs) -> None:
        collected = read_logs(logs, "m01_titles")
        key, observations = next(iter(collected.items()))
        text = render(key, observations)
        assert "1 Modelle" in text
        # Mit einem Modell darf kein Befund ausgesprochen werden.
        assert "zu wenig für ein Urteil" in text
