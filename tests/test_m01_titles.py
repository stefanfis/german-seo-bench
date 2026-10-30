"""Zusicherungen über die Aufgabe M01 selbst.

Zwei Dinge werden hier geprüft, die keine Textlogik sind:

1. Die Schranke zwischen Prompt und Referenz — das Modell darf die Lösung nicht
   im Prompt finden.
2. Der Oracle-Lauf — derselbe wie mit `-T oracle=true`, nur in-process.
"""

import pytest
from inspect_ai import eval as inspect_eval

from german_seo_bench.checks import STYLE_RULES, phrase_hit, phrase_position, style_violations
from german_seo_bench.dataset import load_dataset
from german_seo_bench.tasks.m01_titles import (
    CHECKS,
    DATA,
    GROUPS,
    LEN_MAX,
    LEN_MAX_PROMPT,
    LEN_MIN,
    LEN_MIN_PROMPT,
    KEYWORD_FRONT_MAX,
    KEYWORD_LATE_PENALTY,
    LEN_HARD_MAX,
    OVERLENGTH_PENALTY,
    SCORE_FLOOR,
    STYLE_PENALTY,
    m01_titles,
    prompt,
)
from conftest import assert_prompt_corridor, group_metrics, group_names


def _score_golf(dataset, title: str):
    """Den Scorer direkt auf einen Titel zum Datensatz `golf` anwenden."""
    import asyncio

    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import TaskState

    from german_seo_bench.tasks.m01_titles import title_scorer

    sample = next(s for s in dataset if s.id == "golf")
    state = TaskState(
        model="test", sample_id="golf", epoch=1,
        input=sample.input, messages=[], metadata=sample.metadata,
    )
    state.output = ModelOutput.from_content(model="test", content=title)
    return asyncio.run(title_scorer()(state, None))


def test_jede_stilregel_hat_einen_abzug() -> None:
    # Eine Regel ohne Eintrag ließe den Scorer mit KeyError abbrechen, ein
    # Eintrag ohne Regel wäre toter Code.
    assert set(STYLE_PENALTY) == set(STYLE_RULES)


def test_untergrenze_liegt_ueber_null() -> None:
    # Sonst stünde ein Titel ohne Prüfungsfehler bei 0 wie einer mit.
    assert 0 < SCORE_FLOOR < 1
    # Die Summe aller Abzüge übersteigt 1 — erst dadurch hat die Untergrenze
    # etwas zu tun. Fiele die Summe unter 1, wäre die Zusicherung unten leer.
    worst = sum(STYLE_PENALTY.values()) + KEYWORD_LATE_PENALTY + OVERLENGTH_PENALTY[-1][1]
    assert worst > 1


# Ein Titel zum Datensatz `golf`, der genau `chars` Zeichen lang ist: Keyword
# vorne, kein Stilverstoß, deutsch — er scheitert höchstens an der Länge.
_GOLF_LONG = (
    "Golf lernen für Anfänger: Platzreife, Kosten und Etikette im Verein mit "
    "Freunden und draußen auf dem Platz bei jedem Wetter und auch im Winter"
)


def _golf_title(chars: int) -> str:
    title = _GOLF_LONG[:chars]
    # Ein Leerzeichen am Ende striche der Scorer weg; der Titel wäre kürzer.
    return title[:-1] + "s" if title.endswith(" ") else title


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA, prompt)


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    """Testläufe schreiben ihre Logs ins Temporäre, nicht nach ./logs.

    Sonst liegt nach jedem Testlauf ein Lauf zwischen den echten Messungen,
    der keine Messung ist.
    """
    return str(tmp_path_factory.mktemp("eval-logs"))


class TestReferenzSchranke:
    """Was in `reference` steht, sieht das Modell nie."""

    def test_musterloesung_nicht_im_prompt(self, dataset) -> None:
        for sample in dataset:
            assert sample.metadata["oracle"] not in sample.input, sample.id

    def test_sperrwoerter_sind_im_titel_verboten_nicht_im_text(self, dataset) -> None:
        """Ein Sperrwort im Seitentext ist zulässig — im Titel nicht.

        Die Sperrliste prüft den TITEL. Steht ein Wort daraus auch im
        Seitentext, ist das kein Fehler: Der Titel darf es trotzdem nicht
        aufgreifen, weil es nicht zum Thema der Seite gehört. Der Scorer
        unterscheidet die beiden Herkünfte in der Begründung
        (`forbidden_echoed`), damit man ein Nachplappern nicht für ein
        Verständnisproblem hält.

        Was hier auffallen soll: eine Sperrliste, die zum Datensatz gar nicht
        passt — sie prüfte dann nichts.
        """
        for sample in dataset:
            assert sample.metadata["forbidden"], f"{sample.id}: leere Sperrliste"

    def test_sperrwort_trifft_die_anweisung_nicht(self, dataset) -> None:
        """Die Gegenprobe gegen ein zu allgemeines Sperrwort.

        Ein Sperrwort, das schon in der Aufgabenstellung vorkommt, lässt jeden
        Titel an `meaning` scheitern, der es aufgreift — und die Aussage wäre,
        dass die Modelle das Homonym reihenweise verfehlen.

        Geprüft wird gegen die ANWEISUNG, nicht gegen den Seitentext. Der darf
        ein Sperrwort sehr wohl enthalten — das ist in M01 sogar der
        interessante Fall (`forbidden_echoed`). Die Anweisung dagegen ist
        gewöhnliche deutsche Prosa: Schlägt ein Sperrwort dort an, ist es für
        eine Sperrliste untauglich.
        """
        for sample in dataset:
            text = sample.metadata["text"]
            anweisung = sample.input.replace(text, " ")
            hit = phrase_hit(anweisung, sample.metadata["forbidden"])
            assert hit is None, f"{sample.id}: {hit!r} trifft schon die Anweisung"

    def test_texte_sind_echter_seiteninhalt(self, dataset) -> None:
        """Der Seitentext darf keine Zusammenfassung sein.

        Ein kurzer Text, der das Thema im ersten Satz nennt, ist bereits die
        halbe Antwort — das Modell muss dann nur noch kürzen. Gemessen werden
        soll aber, ob es das Thema in echtem Seiteninhalt FINDET. Deshalb die
        Untergrenze: mehrere Absätze, Auszeichnung, Abschweifungen.
        """
        for sample in dataset:
            text = sample.metadata["text"]
            assert len(text) >= 1000, f"{sample.id}: nur {len(text)} Zeichen"
            assert text.count("<p>") >= 4, f"{sample.id}: zu wenige Absätze"

    def test_texte_enthalten_einen_fremden_teaser(self, dataset) -> None:
        # Der Teaser bewirbt einen ANDEREN Artikel. Wer daraus titelt, hat den
        # Seiteninhalt nicht vom Beiwerk getrennt — je nach Teaser scheitert
        # das an `keyword` (fremdes Thema) oder an `meaning` (falsche
        # Bedeutung des Homonyms). Ohne ihn fehlt der Aufgabe die
        # realistischste Falle aus dem Screaming-Frog-Alltag.
        for sample in dataset:
            assert 'class="teaser"' in sample.metadata["text"], sample.id

    def test_kein_keyword_im_prompt(self, dataset) -> None:
        """Das Zielkeyword erreicht das Modell nicht.

        Es vorzugeben wäre die halbe Antwort: Ein Modell baute schlicht
        `<Keyword>: <Überschrift>` und bestünde `keyword` immer. Das Keyword
        steht nur in `reference` — und `load_dataset` gibt `prompt()` ausschließlich
        `input`, kann es also gar nicht durchreichen.
        """
        for sample in dataset:
            assert "required" in sample.metadata, sample.id
            assert "keyword" not in sample.input.lower().split("\n")[2:4]

    def test_thema_ist_aus_dem_text_erschliessbar(self, dataset) -> None:
        """Mindestens eine Pflichtphrase muss im Seitentext stehen.

        Sonst wäre die Aufgabe nicht schwer, sondern unlösbar: Das Keyword
        steht nicht im Prompt, das Modell kann es nur aus dem Text nehmen.
        Unter der Wortgrenzen-Regel ist das streng — "Krebsarten" im Text
        deckt kein Keyword "Krebs" ab, die Form muss aufgezählt sein.
        """
        for sample in dataset:
            hit = phrase_hit(sample.metadata["text"], sample.metadata["required"])
            assert hit is not None, (
                f"{sample.id}: keine Pflichtphrase im Seitentext "
                f"({sample.metadata['required']})"
            )

    def test_musterloesung_traegt_eine_pflichtphrase(self, dataset) -> None:
        # Gegenprobe zur Prüfung selbst: Wäre `required` leer oder unpassend,
        # ginge `keyword` durch, ohne etwas zu verlangen.
        for sample in dataset:
            assert phrase_hit(
                sample.metadata["oracle"], sample.metadata["required"]
            ), f"{sample.id}: Musterlösung trägt keine Pflichtphrase"

    def test_pflichtphrase_ist_gesetzt(self, dataset) -> None:
        # Eine leere Liste — oder ein leerer Eintrag darin — macht `keyword` zur
        # Prüfung, die immer besteht.
        for sample in dataset:
            required = sample.metadata["required"]
            assert required, f"{sample.id}: keine Pflichtphrase"
            assert all(phrase.strip() for phrase in required), f"{sample.id}: leerer Eintrag"

    def test_pflichtphrase_kollidiert_nicht_mit_der_sperrliste(self, dataset) -> None:
        """Sonst ist der Datensatz nicht zu bestehen.

        Eine Phrase, die zugleich ein Sperrwort trifft, lässt `keyword` und
        `meaning` gegeneinander laufen: Wer das Keyword nimmt, verliert
        `meaning`. Unter der UND-Wertung wäre der Datensatz damit tot, und die
        Null sähe wie Modellversagen aus.

        Mit Teilwort-Treffern entstünde genau dieser Konflikt — "Glühbirne"
        trüge das Keyword "Birne", "Mausefalle" das Keyword "Maus". Er fiele
        nicht auf, weil er sich als `meaning`-Fehler tarnte.
        """
        for sample in dataset:
            for phrase in sample.metadata["required"]:
                hit = phrase_hit(phrase, sample.metadata["forbidden"])
                assert hit is None, f"{sample.id}: {phrase!r} trifft Sperrwort {hit!r}"

    def test_eine_verfehlte_pruefung_kostet_den_ganzen_datensatz(
        self, dataset
    ) -> None:
        """Die Gegenprobe zur UND-Wertung.

        Eine verfehlte Prüfung setzt `score` auf 0. Ohne diese Probe bestünde die Wertung
        auch als Mittelwert weiter — 4 von 5 Prüfungen ergäben 0.8, und der
        Unterschied fiele niemandem auf, weil der
        Oracle-Lauf in beiden Fällen 1.00 zeigt. Eine Musterlösung besteht ja
        alles; unterschieden wird erst an der unvollständigen Antwort.

        Zugleich die Zusicherung, dass die Diagnosespalten Quoten bleiben und
        keine Punkte: Trüge eine Spalte ihr Gewicht, stünde dort 2.0 statt 1.
        """
        import asyncio

        from inspect_ai.model import ModelOutput
        from inspect_ai.solver import TaskState

        from german_seo_bench.tasks.m01_titles import title_scorer

        sample = next(s for s in dataset if s.id == "golf")
        scorer_fn = title_scorer()

        async def run(title: str):
            state = TaskState(
                model="test", sample_id="golf", epoch=1,
                input=sample.input, messages=[], metadata=sample.metadata,
            )
            state.output = ModelOutput.from_content(model="test", content=title)
            return await scorer_fn(state, None)

        # Ein Titel, der genau eine Prüfung verfehlt (das Keyword).
        score = asyncio.run(run("Sport ausprobieren: Platzreife und Etikette"))

        for check in CHECKS:
            assert score.value[check] in (0, 1), f"{check}={score.value[check]}"
        assert score.value["keyword"] == 0
        assert sum(score.value[check] for check in CHECKS) == len(CHECKS) - 1
        assert score.value["score"] == 0

    def test_alle_pruefungen_bestanden_gibt_den_punkt(self, dataset) -> None:
        # Die andere Hälfte der Gegenprobe: Eine Wertung, die immer 0 liefert,
        # bestünde den Test darüber.
        import asyncio

        from inspect_ai.model import ModelOutput
        from inspect_ai.solver import TaskState

        from german_seo_bench.tasks.m01_titles import title_scorer

        sample = next(s for s in dataset if s.id == "golf")
        scorer_fn = title_scorer()

        async def run(title: str):
            state = TaskState(
                model="test", sample_id="golf", epoch=1,
                input=sample.input, messages=[], metadata=sample.metadata,
            )
            state.output = ModelOutput.from_content(model="test", content=title)
            return await scorer_fn(state, None)

        score = asyncio.run(run(sample.metadata["oracle"]))
        assert score.value["score"] == 1

    def test_musterloesungen_sind_stilfrei(self, dataset) -> None:
        # Sonst zöge der Oracle-Lauf selbst ab und läge unter 1.00 — und der
        # Abzug wäre nicht vom Fehler des Modells zu unterscheiden.
        for sample in dataset:
            assert style_violations(sample.metadata["oracle"]) == [], sample.id

    @pytest.mark.parametrize(
        ("title", "expected", "rules"),
        [
            # Besteht alle Prüfungen (Länge, "Golf", deutsch), aber:
            ("Golf-Einstieg-Platzreife-Kosten-Etikette-Anfänger", 0.8, ["spaces"]),
            ("Golf Lernen Anfänger: Platzreife, Kosten & Etikette", 0.9, ["title_case"]),
            ("einstieg in den Golf • Platzreife, Kosten & Etikette", 0.7, ["capital", "charset"]),
            # Ohne Verstoß der volle Punkt — die Gegenprobe zu den drei oben.
            ("Golf lernen für Anfänger: Platzreife, Kosten & Etikette", 1, []),
        ],
    )
    def test_stilverstoss_zieht_ab_statt_zu_nullen(
        self, dataset, title: str, expected: float, rules: list[str]
    ) -> None:
        score = _score_golf(dataset, title)
        assert all(score.value[check] == 1 for check in CHECKS), score.explanation
        assert score.value["score"] == expected
        assert score.value["style"] == int(not rules)
        assert score.metadata["style_violations"] == rules

    def test_musterloesungen_tragen_das_keyword_vorne(self, dataset) -> None:
        for sample in dataset:
            position = phrase_position(sample.metadata["oracle"], sample.metadata["required"])
            assert position is not None and position <= KEYWORD_FRONT_MAX, sample.id

    @pytest.mark.parametrize(
        ("title", "position", "expected"),
        [
            # Beide Seiten der Grenze, sonst fiele ein `>=` statt `>` nicht auf.
            ("Sport für Einsteiger: Kosten und Golf lernen in der Region", 33, 1),
            ("Sport für Einsteiger: Kosten beim Golf lernen in der Region", 34, 0.8),
            ("Golf für Anfänger: Platzreife, Kosten & Etikette", 0, 1),
        ],
    )
    def test_spaetes_keyword_zieht_ab(
        self, dataset, title: str, position: int, expected: float
    ) -> None:
        assert KEYWORD_FRONT_MAX == 33  # die Titel oben sind auf diese Grenze gebaut
        score = _score_golf(dataset, title)
        assert all(score.value[check] == 1 for check in CHECKS), score.explanation
        assert score.metadata["keyword_pos"] == position
        assert score.value["score"] == expected
        assert score.value["keyword_front"] == int(expected == 1)

    def test_spaetes_keyword_und_stilverstoss_ziehen_beide_ab(self, dataset) -> None:
        score = _score_golf(dataset, "Sport für Einsteiger: Kosten beim Golf lernen • Region")
        assert score.metadata["style_violations"] == ["charset"]
        assert score.value["score"] == round(1 - KEYWORD_LATE_PENALTY - STYLE_PENALTY["charset"], 2)
        assert "Keyword erst ab Zeichen 34" in score.explanation

    @pytest.mark.parametrize(
        ("chars", "expected"),
        [
            # Beide Seiten jeder Stufengrenze — sonst fiele eine um eins
            # verschobene Stufe nicht auf.
            (LEN_MIN, 1),
            (66, 1),
            (67, 0.9),
            (76, 0.9),
            (77, 0.7),
            (86, 0.7),
            (87, 0.5),
            (96, 0.5),
        ],
    )
    def test_ueberlaenge_zieht_gestaffelt_ab(self, dataset, chars: int, expected: float) -> None:
        assert (LEN_MAX, LEN_HARD_MAX) == (66, 96)  # die Längen oben sind darauf gebaut
        title = _golf_title(chars)
        assert len(title) == chars
        score = _score_golf(dataset, title)
        assert all(score.value[check] == 1 for check in CHECKS), score.explanation
        assert score.metadata["style_violations"] == []
        assert score.value["score"] == expected
        assert score.value["length_ok"] == int(chars <= LEN_MAX)
        assert score.metadata["overlength"] == max(0, chars - LEN_MAX)
        if chars > LEN_MAX:
            assert f"{chars - LEN_MAX} Zeichen zu lang" in score.explanation

    @pytest.mark.parametrize("chars", [LEN_HARD_MAX + 1, 120, LEN_MIN - 1])
    def test_ausserhalb_der_stufen_scheitert_die_laenge(self, dataset, chars: int) -> None:
        title = _golf_title(chars) if chars <= len(_GOLF_LONG) else _GOLF_LONG + " im Sommer" * 5
        score = _score_golf(dataset, title)
        assert score.value["length"] == 0
        assert score.value["length_ok"] == 0
        assert score.value["score"] == 0
        assert "gescheitert an: length" in score.explanation

    def test_untergrenze_greift(self, dataset) -> None:
        # Überlänge (−0.5), spätes Keyword (−0.2), Bindestrich-Kette (−0.2),
        # kleiner Anfang (−0.2): zusammen 1.1, und doch besteht der Titel das Tor.
        title = "sport-für-einsteiger-kosten-und-beim-Golf-lernen-in-der-region-mit-freunden-und-draußen-am-platz"
        score = _score_golf(dataset, title)
        assert all(score.value[check] == 1 for check in CHECKS), score.explanation
        assert len(title) > LEN_MAX + OVERLENGTH_PENALTY[-2][0]
        assert {"spaces", "capital"} <= set(score.metadata["style_violations"])
        assert score.metadata["keyword_pos"] > KEYWORD_FRONT_MAX
        assert score.value["score"] == SCORE_FLOOR

    def test_ohne_keyword_kein_keyword_front(self, dataset) -> None:
        score = _score_golf(dataset, "Sport ausprobieren: Platzreife und Etikette")
        assert score.value["keyword"] == 0
        assert score.value["keyword_front"] == 0
        assert score.value["score"] == 0

    def test_stilabzug_nur_hinter_dem_tor(self, dataset) -> None:
        # Ein Titel, der eine Prüfung verfehlt UND gegen Stilregeln verstößt,
        # bleibt bei 0 — kein negativer Wert, kein Abzug unter null.
        score = _score_golf(dataset, "sport-ausprobieren-platzreife-etikette")
        assert score.value["keyword"] == 0
        assert score.value["style"] == 0
        assert score.value["score"] == 0

    def test_jeder_datensatz_hat_eine_stufe(self, dataset) -> None:
        # Ohne Stufe fällt ein Datensatz aus dem Profil, ohne dass eine Spalte
        # fehlt — `grouped` bildet dann stillschweigend eine Gruppe "None".
        for sample in dataset:
            assert sample.metadata["level"] in (1, 2, 3, 4, 5), sample.id

    def test_jede_stufe_ist_besetzt(self, dataset) -> None:
        """Mindestens fünf Datensätze je Stufe.

        Eine Quote über zwei Items springt um 0,50 je Item und wäre eine
        Spalte, die eine Aussage suggeriert, die sie nicht trägt.
        """
        import collections

        counts = collections.Counter(s.metadata["level"] for s in dataset)
        assert set(counts) == {1, 2, 3, 4, 5}, sorted(counts)
        for level, count in sorted(counts.items()):
            assert count >= 5, f"Stufe {level} hat nur {count} Datensätze"

    def test_stufe_steht_auf_der_referenzseite(self) -> None:
        """Die Schwierigkeit erreicht das Modell nicht.

        Sie ist eine Eigenschaft der Messung, kein Hinweis für das Modell —
        wer weiß, dass eine Aufgabe schwer ist, liest anders. Geprüft wird an
        der Datendatei und nicht am fertigen Prompt: `load_dataset` übergibt
        `prompt()` ausschließlich den `input`-Teil, also ist "steht in
        `reference`" die Zusicherung, die trägt. Ein Test gegen den Prompttext
        prüft dagegen nur, ob die Ziffer zufällig vorkommt — und "Länge
        zwischen 40 und 60 Zeichen" enthält sie.
        """
        import json
        from importlib import resources

        raw = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
        for record in json.loads(raw)["samples"]:
            assert "level" not in record["input"], record["id"]
            assert "level" in record["reference"], record["id"]

    def test_prompt_korridor_ist_enger_als_die_pruefung(self, dataset) -> None:
        """Die Korridor-Regel, in beide Richtungen.

        Oben liegt der Abstand in der Prüfung (66), nicht im Prompt (60): Die
        29 Musterlösungen liegen zwischen 47 und 60 Zeichen. Eine
        Prompt-Obergrenze darunter ließe einige aus der eigenen Anweisung
        fallen — die Zusicherung `oracles` unten fängt das ab, falls jemand die
        Grenze ohne die Musterlösungen anfasst.
        """
        assert_prompt_corridor(
            "M01", LEN_MIN, LEN_MAX, LEN_MIN_PROMPT, LEN_MAX_PROMPT,
            [(s.id, s.metadata["oracle"]) for s in dataset],
        )

    def test_anweisung_nennt_den_engeren_korridor(self, dataset) -> None:
        # Die Konstanten allein genügen nicht: Sie könnten gesetzt sein, ohne
        # dass der Prompt sie benutzt.
        for sample in dataset:
            assert f"{LEN_MIN_PROMPT} und {LEN_MAX_PROMPT}" in sample.input
            break

    def test_kein_target(self, dataset) -> None:
        # M01 vergleicht nicht gegen eine richtige Antwort. Ein gesetztes
        # Target würde im Log eine Wertung suggerieren, die es nicht gibt.
        for sample in dataset:
            assert not sample.target, sample.id


class TestOracle:
    """Der Smoke-Test der Wertung: Die Musterlösung muss 1.00 ergeben."""

    @pytest.fixture(scope="class")
    @classmethod
    def log(cls, log_dir: str):
        return inspect_eval(m01_titles(oracle=True), model="none/none", log_dir=log_dir)[0]

    def test_lauf_erfolgreich(self, log) -> None:
        assert log.status == "success", log.error

    def test_alle_datensaetze_bewertet(self, log, dataset) -> None:
        assert len(log.samples) == len(dataset)

    @pytest.mark.parametrize("check", ("score", "style", "keyword_front", "length_ok") + CHECKS)
    def test_metrik_ist_eins(self, log, check: str) -> None:
        score = next(s for s in log.results.scores if s.name == check)
        assert score.metrics["mean"].value == 1.0

    @pytest.mark.parametrize("group", GROUPS)
    def test_profil_ist_vollstaendig(self, log, dataset, group: str) -> None:
        """Die Gruppennamen im Log gegen die im Datensatz — auf Gleichheit.

        Nicht "mindestens eine Spalte": Inspect entdoppelt Metriken über den
        Registry-Namen, und ein stiller Verlust sieht in der Tabelle aus wie
        eine Aufschlüsselung, die es nicht gibt.
        """
        expected = {str(sample.metadata[group]) for sample in dataset}
        assert group_names(log, group) == expected

    @pytest.mark.parametrize("group", GROUPS)
    def test_jede_gruppe_ist_eins(self, log, group: str) -> None:
        columns = group_metrics(log, group)
        assert columns, f"keine Spalte für {group!r} im Log"
        for name, value in columns.items():
            assert value == 1.0, name

    def test_gegenprobe_scorer_unterscheidet(self, log_dir: str) -> None:
        # Ohne diese Probe wäre "alles 1.00" auch mit einem Scorer erreichbar,
        # der immer 1.00 liefert.
        broken = inspect_eval(
            m01_titles(oracle=True),
            model="none/none",
            log_dir=log_dir,
            solver=_wrong_answer_solver(),
        )[0]
        mean = next(s for s in broken.results.scores if s.name == "score")
        assert mean.metrics["mean"].value < 1.0


def _wrong_answer_solver():
    """Antwortet auf jeden Datensatz mit demselben untauglichen Titel."""
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def wrong_answer() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(
                model="broken", content='**"Title: Best Garden Bench Ever"**'
            )
            state.messages.append(state.output.message)
            return state

        return solve

    return wrong_answer()

