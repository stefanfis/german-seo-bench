"""Gegenproben zu den Textprüfungen.

Zu jeder Zusicherung gehört der Fall, in dem sie anschlagen muss. Eine Prüfung,
die nicht anschlägt, wenn man sie bricht, ist keine — deshalb steht zu jedem
"das besteht" hier ein "und das besteht nicht".

`phrase_hit` ist die Regel, nach der M01 und M10 suchen: eine Phrase aus einer
Liste, an Wortgrenzen, buchstabengetreu. `fold` und `word_hit` sind die
tolerantere Fassung, mit der M02 und M03 vergleichen; `normalize` ist ihre
Grundlage.
"""

import pytest

from german_seo_bench.checks import (
    STYLE_RULES,
    fold,
    format_ok,
    head_hit,
    is_german,
    normalize,
    phrase_hit,
    phrase_position,
    style_violations,
    word_hit,
)


class TestNormalize:
    def test_umlaute_werden_aufgeloest(self) -> None:
        assert normalize("Käse") == normalize("Kaese")

    def test_grossschreibung_egal(self) -> None:
        assert normalize("GARTENBANK") == normalize("gartenbank")

    def test_satzzeichen_verschwinden(self) -> None:
        assert normalize("Maus: Test, 2026!") == "maus test 2026"


class TestFold:
    def test_umlaute_werden_aufgeloest(self) -> None:
        assert fold("Käse") == fold("Kaese")

    def test_umlaut_wird_nicht_zur_nackten_vokal(self) -> None:
        """Die Regression, gegen die das NFC in `fold` steht.

        NFKD zerlegt "ä" in "a" plus kombinierendes Trema. Ohne das
        Zusammensetzen danach bliebe "kase" übrig, und "Käse" wäre nicht mehr
        gleich "Kaese" — die Umlautgleichheit wäre still weg.
        """
        assert fold("Käse") == "kaese"

    def test_tiefstellung_wird_zur_ziffer(self) -> None:
        assert fold("CH₄") == fold("CH4") == "ch4"

    def test_gegenprobe_ohne_faltung_geht_die_ziffer_verloren(self) -> None:
        # Der Grund, warum `fold` überhaupt existiert und nicht direkt gegen
        # `normalize` verglichen wird: Dort verschwindet die tiefgestellte
        # Ziffer ganz, und eine richtig geschriebene Summenformel zählte nicht.
        assert normalize("CH₄") != normalize("CH4")

    def test_bindestrich_wird_leerzeichen(self) -> None:
        assert fold("Inter-City") == "inter city"


class TestWordHit:
    @pytest.mark.parametrize(
        "answer,term",
        [
            ("In den Inn", "Inn"),
            ("In München", "Muenchen"),  # Umlaut auf beiden Seiten gefaltet
            ("Am Ob", "Ob"),
            ("Der Große Arber", "Arber"),
            ("Großer Arber", "Grosser Arber"),  # ß und ss auf beiden Seiten
        ],
    )
    def test_treffer(self, answer: str, term: str) -> None:
        assert word_hit(fold(answer), term)

    @pytest.mark.parametrize(
        "answer,term",
        [
            ("In Innsbruck", "Inn"),  # Wortgrenze links
            ("Das liegt innerhalb", "Inn"),  # Wortgrenze rechts
            ("Die Isar", "Inn"),
        ],
    )
    def test_kein_treffer(self, answer: str, term: str) -> None:
        assert not word_hit(fold(answer), term)

    def test_keine_flexionstoleranz(self) -> None:
        """Keine geratenen Endungen, und das ist hier wesentlich.

        Referenzen enthalten auch sehr kurze Begriffe. "Inn" plus Endung
        ergibt "innen", und ein Begriff, der auf jedes "von innen" anspringt,
        trifft, wo nichts zu treffen ist.
        """
        assert not word_hit(fold("Von innen betrachtet"), "Inn")


class TestIsGerman:
    @pytest.mark.parametrize(
        "title",
        [
            "Golf lernen für Anfänger: Regeln und erste Tipps",  # Stufe 1: Umlaut
            "Strom sparen im Haushalt: die besten Tipps",  # Stufe 2: Funktionswort
        ],
    )
    def test_harte_signale(self, title: str) -> None:
        assert is_german(title)

    # Echte Modellantworten, bei denen weder Umlaut noch
    # Funktionswort greift — hier entscheidet allein der Detektor. Das sind
    # rund 15 % aller Titel und damit die Fälle, die bei einem Wechsel des
    # Detektors als Erstes kippen. Wer ihn tauscht, sieht es hier.
    @pytest.mark.parametrize(
        "title",
        [
            "Birne anbauen: Sorten, Ernte & Lagerung",
            "Guten Ton aufnehmen: Mikrofon, Pegel & Nachbearbeitung",
            "Historisches Schloss: Architektur, Geschichte & Ausflugstipps",
            "Leiter: Anlege- & Stehleiter – Material & Traglast",
            "Maus Test: Ergonomische & kabellose Modelle",
            "Maus: kabellose Ergonomie, DPI & Akkulaufzeit",
            "Schloss: Geschichte, Architektur & Besucherinfos",
            "Steuer: Betriebsausgaben, Vorauszahlungen & Fristen",
            "Ton verbessern: Podcast & Video Aufnahmetipps",
            "Zug verpasst? Rechte, Erstattung & Umbuchung",
        ],
    )
    def test_nur_detektor_entscheidet(self, title: str) -> None:
        assert is_german(title)

    # Die Gegenprobe: Dieselben Themen auf Englisch. Ohne sie wäre die Prüfung
    # auch mit einem `is_german`, das immer True liefert, grün.
    @pytest.mark.parametrize(
        "title",
        [
            "Best Garden Bench Made of Solid Wood",
            "How to Learn Golf as a Complete Beginner",
            "Wireless Mouse Test: Ergonomic Office Models",
            "Pear Growing Guide: Varieties, Harvest, Storage",
            "Castle Tour: Architecture, History and Visitor Info",
            "Train Delayed? Rights, Refunds and Rebooking",
            "Tax Saving Tips for Freelancers and the Self-Employed",
            "Good Sound for Podcasts: Microphone and Editing",
        ],
    )
    def test_nicht_deutsch(self, title: str) -> None:
        assert not is_german(title)


class TestFormatOk:
    def test_sauberer_titel(self) -> None:
        assert format_ok("Gartenbank aus Holz: robuste Sitzbank für den Garten")

    @pytest.mark.parametrize(
        "title",
        [
            "",
            "**Gartenbank aus Holz**",  # Markdown
            "„Gartenbank aus Holz“",  # typografische Anführungszeichen
            '"Gartenbank aus Holz"',  # gerade Anführungszeichen
            "Titel: Gartenbank aus Holz",  # Einleitung
            "SEO-Title: Gartenbank aus Holz",
            "Gartenbank aus Holz 🌿",  # Emoji
            "Gartenbank aus Holz\nZweite Zeile",  # mehrzeilig
        ],
    )
    def test_abgelehnt(self, title: str) -> None:
        assert not format_ok(title)



class TestStyleViolations:
    """Je Regel ein Titel, der sie bricht, und einer, der knapp davor bleibt."""

    @pytest.mark.parametrize(
        "title",
        [
            "Gartenbank aus Holz: robuste Sitzbank für den Garten",
            # Lauter Substantive, aber durch Trenner gegliedert — richtig
            # geschrieben, darf nicht als Title Case gelten.
            "Mandelentzündung: Symptome, Ursachen & Behandlung",
            "Gartenbank Materialien & Pflege",  # nur zwei in Folge
            "5 Tipps für die Gartenbank aus Holz",  # Ziffer am Anfang
            "Strom sparen im Haushalt – 30 % weniger Kosten",
            "Golf für Anfänger: Platzreife, Kosten & Etikette!",
            # Ein kleingeschriebenes Wort genügt im Abschnitt — sonst träfe
            # die Höflichkeitsform "Sie Ihr" als drei Großbuchstaben in Folge.
            "Zugverspätung: Wann bekommen Sie Ihr Geld zurück",
        ],
    )
    def test_sauber(self, title: str) -> None:
        assert style_violations(title) == []

    @pytest.mark.parametrize(
        ("title", "rule"),
        [
            ("Sommer-Mosquito-Stich-Vorbeugung", "spaces"),
            ("Mückenstich-Vorbeugung im-Sommer", "spaces"),  # ein Leerzeichen
            ("kleine Reparaturen im Bad: Kalkrand und Geräusch", "capital"),
            ("- Gartenbank aus Holz: robuste Sitzbank", "capital"),
            ("Igel Richtig Füttern", "title_case"),
            ("Golf Anfänger Einstieg Platzreife Kosten Schläger", "title_case"),
            ("Ratgeber: Igel Richtig Füttern", "title_case"),  # nach Trenner
            ("Gartenbank aus Holz • robuste Sitzbank", "charset"),
            ("Gartenbank aus Holz → robuste Sitzbank", "charset"),
            ("Gartenbank aus Holz: robust, schön…", "charset"),
            ("Gartenbank aus Holz; robuste Sitzbank", "charset"),
            ("Eine Gartenbank aus Holz hält viele Jahre.", "sentence_end"),
        ],
    )
    def test_verstoss(self, title: str, rule: str) -> None:
        assert rule in style_violations(title)

    def test_reihenfolge_folgt_style_rules(self) -> None:
        # Vier Regeln zugleich (`spaces` und `title_case` schließen sich aus:
        # drei Wörter brauchen zwei Leerzeichen). Die Liste kommt in fester
        # Reihenfolge, damit dieselbe Antwort im Log dieselbe Begründung trägt.
        expected = [rule for rule in STYLE_RULES if rule != "spaces"]
        assert style_violations("igel•Pflege: Richtig Füttern Im Herbst.") == expected

PEAR = ["Birnen ernten", "Birnenernte"]


class TestHeadHit:
    """Die Grundwort-Regel für Motiv und Zusammenhang in M10."""

    def test_grundwort_trifft(self) -> None:
        assert head_hit("Bunte Herbstkürbisse auf dem Feld", ["Kürbisse"]) == "Kürbisse"
        assert head_hit("Porsche auf einer Landstraße", ["Straße"]) == "Straße"
        # Gegenprobe: Die strenge Regel trifft dort nicht.
        assert phrase_hit("Bunte Herbstkürbisse auf dem Feld", ["Kürbisse"]) is None

    def test_ganzes_wort_trifft_weiterhin(self) -> None:
        assert head_hit("Ein Zug im Tal", ["Zug"]) == "Zug"

    def test_nur_am_wortende(self) -> None:
        # Ein Kürbisfeld ist kein Kürbis, ein Bahnhof keine Bahn.
        assert head_hit("Ein Kürbisfeld im Herbst", ["Kürbis"]) is None
        assert head_hit("Ein Bahnhof im Tal", ["Bahn"]) is None

    def test_keine_geratenen_endungen(self) -> None:
        assert head_hit("Haufen von Herbstkürbissen", ["Kürbis", "Kürbisse"]) is None
        assert head_hit("Haufen von Herbstkürbissen", ["Kürbissen"]) == "Kürbissen"

    def test_schreibweise_zaehlt_am_wortanfang(self) -> None:
        # Klein steht das Listenwort nur im Inneren eines Kompositums.
        assert head_hit("Drei pinguine am Strand", ["Pinguine"]) is None
        assert head_hit("Drei Pinguine am Strand", ["Pinguine"]) == "Pinguine"

    def test_der_preis_falsche_komposita_treffen(self) -> None:
        # Deshalb gehört "Seilbahn" beim Zug in die Sperrliste.
        assert head_hit("Rote Seilbahn am Berg", ["Bahn"]) == "Bahn"

    def test_mehrere_woerter(self) -> None:
        assert head_hit("Blick auf das Brandenburger Tor", ["Brandenburger Tor"]) == "Brandenburger Tor"


class TestPhrasePosition:
    """Wo das Thema im Titel beginnt — nach derselben Suchregel wie `phrase_hit`."""

    def test_frueheste_stelle_nicht_erste_listenphrase(self) -> None:
        # `phrase_hit` meldet hier "Wasserhahn" (erste der Liste, Stelle 13);
        # gefragt ist aber, wie weit vorne das Thema steht.
        title = "Hahn tropft: Wasserhahn reparieren"
        assert phrase_hit(title, ["Wasserhahn", "Hahn"]) == "Wasserhahn"
        assert phrase_position(title, ["Wasserhahn", "Hahn"]) == 0

    def test_stelle_hinten(self) -> None:
        assert phrase_position("Kleine Reparaturen im Bad: Hahn abdichten", ["Hahn"]) == 27

    def test_kein_treffer(self) -> None:
        assert phrase_position("Kleine Reparaturen im Bad", ["Hahn", "Wasserhahn"]) is None

    def test_wortgrenzen_wie_phrase_hit(self) -> None:
        # Ein Teilwort ist kein Treffer — auch nicht für die Position. Sonst
        # stünde ein Keyword "vorne", das `keyword` gar nicht gefunden hat.
        assert phrase_position("Hahnenfuß im Garten: Hahn im Bad", ["Hahn"]) == 21

    def test_leerraum_verschiebt_nicht(self) -> None:
        assert phrase_position("Bad  reparieren:   Hahn", ["Hahn"]) == phrase_position(
            "Bad reparieren: Hahn", ["Hahn"]
        )


class TestPhraseHit:
    """Die eine Suchregel: eine Phrase aus der Liste, an Wortgrenzen, wörtlich."""

    def test_eine_phrase_genuegt(self) -> None:
        assert phrase_hit("Birnenernte im Oktober: so gehts", PEAR) == "Birnenernte"

    def test_mehrwortphrase_trifft_zusammenhaengend(self) -> None:
        assert phrase_hit("Birnen ernten und lagern", PEAR) == "Birnen ernten"

    @pytest.mark.parametrize(
        "title",
        [
            "Birnen einfach ernten",  # Wörter nicht zusammenhängend
            "Birnen: Ernte leicht gemacht",  # andere Wortform
            "Birne im Garten: jetzt ernten",  # Singular, nicht zusammenhängend
        ],
    )
    def test_nur_die_aufgezaehlten_formen_zaehlen(self, title: str) -> None:
        """Die Härte der Regel, ausdrücklich festgehalten.

        Alle drei Titel sind vertretbar und fallen trotzdem durch. Das ist der
        Handel: eine Regel, die jeder nachrechnen kann, statt einer, die jeden
        Einzelfall fair behandelt. Wer eine Form zulassen will, schreibt sie in
        `required`.
        """
        assert phrase_hit(title, PEAR) is None

    def test_rueckgabe_ist_die_getroffene_phrase(self) -> None:
        # Nicht `True`: Die Begründung im Log soll sagen, WAS getroffen hat.
        assert phrase_hit("Jetzt Birnen ernten", PEAR) == "Birnen ernten"

    def test_reihenfolge_der_liste_entscheidet(self) -> None:
        text = "Birnenernte: Birnen ernten im Oktober"
        assert phrase_hit(text, PEAR) == "Birnen ernten"
        assert phrase_hit(text, list(reversed(PEAR))) == "Birnenernte"

    @pytest.mark.parametrize(
        "title",
        [
            "Birnenernten leicht gemacht",  # Endung angehängt
            "Vorbirnenernte im September",  # Wortanfang belegt
            "Glühbirne wechseln: Fassung und Watt",  # Teilwort zählt nicht
        ],
    )
    def test_wortgrenzen(self, title: str) -> None:
        assert phrase_hit(title, PEAR + ["Birne"]) is None

    def test_bindestrich_ist_eine_wortgrenze(self) -> None:
        # Sonst fiele "Cookie-Einwilligung" aus einem Keyword "Cookie" heraus.
        assert phrase_hit("Cookie-Einwilligung richtig umsetzen", ["Cookie"]) == "Cookie"

    @pytest.mark.parametrize(
        "title",
        [
            "birnenernte im oktober",  # alles klein
            "BIRNENERNTE: Jetzt vorbereiten!",  # alles groß
        ],
    )
    def test_gross_und_kleinschreibung_zaehlt(self, title: str) -> None:
        """Im Deutschen trägt die Schreibung Bedeutung.

        "buchen" und "Buchen" sind verschiedene Dinge, "Ernte" und "ernte"
        Substantiv und Verbform. Ein durchgehend groß geschriebener Titel ist
        außerdem auch als SEO-Titel keine gute Wahl.
        """
        assert phrase_hit(title, PEAR) is None

    def test_umlaute_werden_nicht_aufgeloest(self) -> None:
        # Anders als `normalize`: "Kaese" ist hier kein Treffer für "Käse".
        assert phrase_hit("Kaese richtig lagern", ["Käse"]) is None
        assert phrase_hit("Käse richtig lagern", ["Käse"]) == "Käse"

    def test_zerlegter_umlaut_trifft_trotzdem(self) -> None:
        # NFC vor dem Vergleich: "a" + kombinierendes Trema sieht wie "ä" aus
        # und muss deshalb auch wie "ä" zählen. Sonst hinge ein Treffer daran,
        # wie der Text kodiert ist.
        assert phrase_hit("Ka\u0308se richtig lagern", ["Käse"]) == "Käse"

    def test_leerraum_wird_eingeebnet(self) -> None:
        # Ein Zeilenumbruch zwischen den Wörtern ist kein anderer Titel.
        assert phrase_hit("Jetzt Birnen\n  ernten", PEAR) == "Birnen ernten"

    def test_leere_liste_und_leere_phrase(self) -> None:
        # Eine leere Liste trifft nichts — und ein leerer Eintrag darf nicht
        # alles treffen, sonst bestünde `keyword` durch einen Datenfehler.
        assert phrase_hit("Birnenernte", []) is None
        assert phrase_hit("Birnenernte", ["", " "]) is None
