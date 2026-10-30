"""Gegenproben zu `checks/alt_text.py` — ohne Inspect, ohne Modell.

Zu jeder Zusicherung gehört der Fall, in dem sie anschlagen muss. Eine Prüfung,
die nicht anschlägt, wenn man sie bricht, ist keine.
"""

import pytest

from german_seo_bench.checks import phrase_hit
from german_seo_bench.checks.alt_text import boilerplate_hit


class TestBoilerplate:
    """Der Floskel-Vorspann, an dem ALT-Texte aus Sprachmodellen scheitern."""

    @pytest.mark.parametrize(
        "text",
        [
            "Bild von drei Pinguinen auf einem Felsen",
            "Ein Bild von drei Pinguinen",
            "Das Bild zeigt drei Pinguine",
            "Dieses Bild zeigt drei Pinguine",
            "Bilder von Pinguinen am Meer",
            "Foto von drei Pinguinen",
            "Ein Foto einer Pferdekutsche",
            "Fotos einer Pferdekutsche",
            "Fotografie einer Pferdekutsche",
            "Die Aufnahme zeigt eine Pferdekutsche",
            "Abbildung einer Pferdekutsche",
            "Grafik mit drei Pinguinen",
            "Grafiken mit drei Pinguinen",
            "Illustration eines Wohnzimmers",
            "Screenshot eines Wohnzimmers",
            "Image of three penguins",
            "A picture of three penguins",
            # Etiketten: dasselbe Problem, nur dass das Modell seine Antwort
            # beschriftet statt sie einzuleiten.
            "ALT-Text: Drei Pinguine am Meer",
            "Alt Text: Drei Pinguine am Meer",
            "Bildbeschreibung: Drei Pinguine am Meer",
            "Antwort: Drei Pinguine am Meer",
            # Verpackung DAVOR. Die fällt schon `format_ok` zur Last — ohne das
            # Überspringen der Satzzeichen stünde in der Begründung aber nur
            # die Verpackung und nicht die Floskel darunter.
            '"Bild von drei Pinguinen"',
            "**Das Foto zeigt drei Pinguine**",
        ],
    )
    def test_floskel_wird_erkannt(self, text: str) -> None:
        assert boilerplate_hit(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "Drei Pinguine stehen auf einem Felsen am Meer",
            "Pferdekutsche vor bunten Giebelhäusern am Alten Markt",
            "Wohnzimmer mit grauem Sofa und Fernseher",
            # Der Nachbar-Lookahead. Ohne ihn schlüge "bild" hier an, und ein
            # völlig richtiger ALT-Text wäre an der Floskel-Prüfung
            # gescheitert.
            "Bildschirm eines Fernsehers auf einem weißen Sideboard",
            "Bilderrahmen an einer grauen Wand über dem Sofa",
            "Aufnahmegerät auf einem Stativ im Studio",
            "Beschreibungstafel neben einem Gehege im Park",
            # Das Stichwort mitten im Satz ist keine Floskel — der Vorspann ist
            # nur am Anfang einer.
            "Drei Pinguine, im Hintergrund ein Foto der Küste",
        ],
    )
    def test_ordentlicher_alt_text_bleibt_unbehelligt(self, text: str) -> None:
        assert boilerplate_hit(text) is None

    def test_gefunden_wird_der_wortlaut(self) -> None:
        # Nicht nur True: In der Begründung soll stehen, WOMIT der Text anfing.
        assert boilerplate_hit("Das Bild zeigt drei Pinguine") == "Das Bild"
        assert boilerplate_hit("Bild von Pinguinen") == "Bild"


class TestMotivUndZusammenhang:
    """Motiv und Zusammenhang prüft M10 mit `phrase_hit` — ohne eigene Funktion.

    Die Regel selbst ist in `test_checks.py` durchgeprüft. Hier steht nur, was
    sie für M10 bedeutet: Mehrere Phrasen je Liste fangen Synonyme ab, und
    Komposita zählen nur, wenn sie aufgezählt sind.
    """

    SUBJECT = ["Sofa", "Couch", "Ecksofa"]
    CONTEXT = ["Wohnzimmer", "Fernseher", "Küche"]

    def test_motiv_und_zusammenhang_getroffen(self) -> None:
        text = "Graues Sofa im Wohnzimmer"
        assert phrase_hit(text, self.SUBJECT) == "Sofa"
        assert phrase_hit(text, self.CONTEXT) == "Wohnzimmer"

    def test_synonym_zaehlt_genauso(self) -> None:
        # Der Sinn mehrerer Phrasen: "Sofa" und "Couch" sind dasselbe
        # Möbelstück, und auf einer Form zu bestehen hieße Vokabellotterie
        # messen statt Sehvermögen.
        assert phrase_hit("Graue Couch im Wohnzimmer", self.SUBJECT) == "Couch"

    def test_kompositum_nur_wenn_aufgezaehlt(self) -> None:
        """Die härtere Hälfte der Regel für M10.

        Deutsche Bildbeschreibungen bestehen zu großen Teilen aus
        zusammengesetzten Wörtern. Es trifft nur, was in der Liste steht — "Ecksofa" also ja, "Schlafsofa" nein.
        """
        assert phrase_hit("Graues Ecksofa im Wohnzimmer", self.SUBJECT) == "Ecksofa"
        assert phrase_hit("Graues Schlafsofa im Wohnzimmer", self.SUBJECT) is None

    def test_fehlendes_motiv_faellt_auf(self) -> None:
        assert phrase_hit("Blick in ein Wohnzimmer", self.SUBJECT) is None

    def test_fehlender_zusammenhang_faellt_auf(self) -> None:
        assert phrase_hit("Ein graues Sofa", self.CONTEXT) is None
