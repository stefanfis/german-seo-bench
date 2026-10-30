"""Gegenproben zu den Feldprüfungen von M03.

Zu jeder Zusicherung gehört der Fall, in dem sie anschlagen muss. Eine Prüfung,
die nicht anschlägt, wenn man sie bricht, ist keine — deshalb steht zu jedem
"das zählt" hier ein "und das zählt nicht".

Der Schwerpunkt liegt auf zwei Stellen, an denen eine zu milde Prüfung zu gute
Zahlen erzeugen würde: der Typprüfung (`true` im Preisfeld, `1` im
Bool-Feld) und der Rückgewinnung des JSON aus einer verpackten Antwort.
"""

import pytest

from german_seo_bench.checks.facts import (
    FIELDS,
    as_number,
    extract_json_object,
    grade_facts,
    is_empty,
    matches,
    numbers,
    price_hint,
)

TRUTH = {
    "name": "ErgoFlex",
    "brand": "SitzWohl",
    "price_eur": 199.95,
    "color": "Anthrazit",
    "material": "Polyester",
    "sku": "SW-4471",
    "in_stock": True,
}

DISTRACTORS = [
    {"value": 249.00, "kind": "Streichpreis"},
    {"value": 4.6, "kind": "Sternebewertung"},
]


def answer(**overrides) -> str:
    """Eine richtige Antwort als JSON-Text, feldweise verbiegbar."""
    import json

    return json.dumps({**TRUTH, **overrides}, ensure_ascii=False)


class TestAsNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (199.95, 199.95),
            (199, 199.0),
            ("199.95", 199.95),  # Zahl als Zeichenkette: übernommen, siehe Docstring
            ("  199.95 ", 199.95),
        ],
    )
    def test_zahl(self, value, expected: float) -> None:
        assert as_number(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", ["1.299,00", "199,95", "abc", None, [], {}])
    def test_keine_zahl(self, value) -> None:
        # "1.299,00" ist der wichtigste Fall: Genau diese Umrechnung ist die
        # Aufgabe. Sie durchzulassen hieße, die Prüfung abzuschaffen.
        assert as_number(value) is None

    def test_bool_ist_keine_zahl(self) -> None:
        # Python führt bool als int. Ohne diesen Ausschluss würde ein Modell,
        # das `true` ins Preisfeld schreibt, mit 1.00 Euro gewertet.
        assert as_number(True) is None
        assert as_number(False) is None


class TestIsEmpty:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_leer(self, value) -> None:
        assert is_empty(value)

    @pytest.mark.parametrize("value", ["null", "n/a", "-", "keine", 0, False])
    def test_nicht_leer(self, value) -> None:
        # "null" als WORT ist eine Angabe über die Lücke, keine Lücke. Für ein
        # nachgelagertes Skript ist der Unterschied erheblich.
        assert not is_empty(value)


class TestMatches:
    def test_word_nimmt_umgebung_hin(self) -> None:
        assert matches("word", "TitanCook", "Pfanne TitanCook")
        assert matches("word", "Stahl", "rostfreier Stahl")

    def test_word_nur_als_ganzes_wort(self) -> None:
        # Sonst zählte "Stahlbeton" als "Stahl" und jede Teilzeichenkette als
        # Treffer.
        assert not matches("word", "Stahl", "Stahlbeton")
        assert not matches("word", "Ripstop-Nylon", "Nylon")

    def test_exact_ist_zeichengleich(self) -> None:
        assert matches("exact", "Anthrazit", "Anthrazit")
        assert matches("exact", "Anthrazit", "  Anthrazit  ")

    def test_exact_unterscheidet_gross_und_klein(self) -> None:
        # Die bewusst strenge Regel — Begründung im Modul-Docstring von
        # `checks/facts.py`.
        assert not matches("exact", "Anthrazit", "anthrazit")

    def test_exact_haelt_die_artikelnummer_zusammen(self) -> None:
        assert not matches("exact", "NP-2255-A", "NP2255A")
        assert not matches("exact", "SW-4471", "Art.-Nr. SW-4471")

    def test_number_mit_cent_toleranz(self) -> None:
        assert matches("number", 199.95, 199.95)
        assert matches("number", 199.95, "199.95")
        assert not matches("number", 199.95, 199.99)
        assert not matches("number", 1299.00, "1.299,00")

    def test_bool_verlangt_echtes_bool(self) -> None:
        assert matches("bool", True, True)
        assert not matches("bool", True, False)
        # Ohne die Typprüfung wäre 1 == True und "true" eine Zeichenkette, die
        # wie ein Bool aussieht. Beides ist nicht das verlangte Feld.
        assert not matches("bool", True, 1)
        assert not matches("bool", True, "true")
        assert not matches("bool", False, "nein")

    def test_null_verlangt_leer(self) -> None:
        assert matches("exact", None, None)
        assert matches("exact", None, "")
        assert not matches("exact", None, "SitzWohl")

    def test_wert_wo_null_erwartet_ist_falsch(self) -> None:
        # Der interessanteste Fehler der Aufgabe: Ein Modell erfindet einen
        # Hersteller, wo keiner genannt ist. Genau das soll null aufdecken.
        assert not matches("exact", None, "Unbekannt")

    def test_unbekannte_regel_fliegt_auf(self) -> None:
        with pytest.raises(ValueError):
            matches("gibtsnicht", "a", "a")


class TestExtractJsonObject:
    def test_nacktes_objekt(self) -> None:
        got, error = extract_json_object('{"a": 1}')
        assert (got, error) == ({"a": 1}, None)

    def test_im_codeblock(self) -> None:
        got, _ = extract_json_object('Hier:\n```json\n{"a": 1}\n```\nFertig.')
        assert got == {"a": 1}

    def test_codeblock_ohne_sprache(self) -> None:
        got, _ = extract_json_object('```\n{"a": 1}\n```')
        assert got == {"a": 1}

    def test_mit_vorrede_und_nachrede(self) -> None:
        got, _ = extract_json_object('Hier sind die Fakten: {"a": 1} — alles klar?')
        assert got == {"a": 1}

    def test_verschachtelte_klammern(self) -> None:
        got, _ = extract_json_object('{"a": {"b": [1, 2]}, "c": "}"}')
        assert got == {"a": {"b": [1, 2]}, "c": "}"}

    def test_einelementiges_array_wird_ausgepackt(self) -> None:
        # Ein Objekt in einem Array verpackt. Keine Extraktionsschwäche, also auch kein Grund für null Punkte.
        got, _ = extract_json_object('[{"a": 1}]')
        assert got == {"a": 1}

    def test_array_mit_mehreren_objekten_wird_abgelehnt(self) -> None:
        # Hier hat das Modell nicht verpackt, sondern etwas anderes getan als
        # verlangt. Welches Objekt gemeint war, wäre geraten.
        got, error = extract_json_object('[{"a": 1}, {"a": 2}]')
        assert got is None
        assert "2 Objekten" in error

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "Ich kann das nicht.", '{"a": 1', '{"a": }', "[1, 2, 3]", "42"],
    )
    def test_kein_objekt(self, text: str) -> None:
        got, error = extract_json_object(text)
        assert got is None
        assert error


class TestGradeFacts:
    def test_richtige_antwort(self) -> None:
        verdict = grade_facts(TRUTH, answer(), DISTRACTORS)
        assert verdict.score == 1.0
        assert verdict.parsed and verdict.complete
        assert verdict.reason is None
        assert verdict.wrong == {}

    def test_ein_falsches_feld_kostet_den_ganzen_datensatz(self) -> None:
        """Die Gegenprobe zur Alles-oder-nichts-Wertung.

        Sechs von sieben richtigen Feldern ergeben keinen Datensatz, der zu
        sechs Siebteln in einen Produktfeed geht — er muss angefasst werden.
        Die Feldspalte sagt weiterhin, WELCHES Faktum fehlte.
        """
        verdict = grade_facts(TRUTH, answer(color="Blau"), DISTRACTORS)
        assert verdict.score == 0.0
        assert sum(verdict.fields.values()) == len(verdict.fields) - 1
        assert verdict.fields["color"] is False
        assert verdict.wrong == {"color": "Blau"}
        assert "color" in verdict.reason

    def test_fehlender_schluessel_kostet_nur_sein_feld(self) -> None:
        """Ein fehlender Schlüssel verwirft nicht die ganze Antwort.

        Sonst hätte ein Modell mit sechs richtigen Feldern null.
        """
        import json

        incomplete = {k: v for k, v in TRUTH.items() if k != "color"}
        verdict = grade_facts(TRUTH, json.dumps(incomplete), DISTRACTORS)
        assert verdict.score == 0.0
        # Der Unterschied zu "sieben falsche Felder" steht in den Feldspalten
        # und in `complete`, nicht im Score: sechs Felder stimmen.
        assert sum(verdict.fields.values()) == len(verdict.fields) - 1
        assert verdict.parsed
        assert not verdict.complete
        assert verdict.missing == ("color",)
        assert "color" in verdict.reason

    def test_kein_json_ist_kein_einziges_feld(self) -> None:
        verdict = grade_facts(TRUTH, "Das kann ich nicht extrahieren.", DISTRACTORS)
        assert verdict.score == 0.0
        assert not verdict.parsed
        assert not verdict.complete
        assert not any(verdict.fields.values())

    def test_alle_felder_werden_berichtet(self) -> None:
        # Sonst fehlte in der Metriktabelle eine Spalte, und Inspect würde die
        # Metrik für einen Schlüssel verlangen, den der Score nicht hat.
        verdict = grade_facts(TRUTH, answer(), DISTRACTORS)
        assert set(verdict.fields) == set(FIELDS)


class TestPriceHint:
    def test_streichpreis_wird_benannt(self) -> None:
        assert price_hint(249.00, DISTRACTORS) == "Streichpreis"

    def test_sterne_werden_benannt(self) -> None:
        assert price_hint(4.6, DISTRACTORS) == "Sternebewertung"

    def test_unbekannte_zahl_bleibt_unbenannt(self) -> None:
        # Eine Zahl, die im Text nicht vorkommt, ist erfunden — und genau das
        # sagt ein fehlender Hinweis aus. Etwas zu raten wäre schlechter.
        assert price_hint(12345.0, DISTRACTORS) is None

    def test_ohne_distraktorliste(self) -> None:
        assert price_hint(249.00, None) is None

    def test_wird_im_urteil_mitgeliefert(self) -> None:
        verdict = grade_facts(TRUTH, answer(price_eur=249.00), DISTRACTORS)
        assert verdict.price_hint == "Streichpreis"

    def test_bei_richtigem_preis_kein_hinweis(self) -> None:
        assert grade_facts(TRUTH, answer(), DISTRACTORS).price_hint is None


class TestNumbers:
    """Die Zahlenerkennung, mit der der Datensatz gegen seine Texte geprüft wird."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1989", [1989.0]),
            ("1.299,00 Euro", [1299.0]),
            ("1,5 Prozent", [1.5]),
            ("3.9", [3.9]),  # kein Tausendertrenner: keine Dreiergruppe
            ("12.000", [12000.0]),
            ("Am 3. September 2081", [3.0, 2081.0]),
            ("keine Zahl", []),
        ],
    )
    def test_erkennung(self, text: str, expected: list) -> None:
        assert numbers(text) == expected
