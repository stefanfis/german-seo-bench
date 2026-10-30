"""Zusicherungen über die Aufgabe M10 selbst.

Drei Dinge, die keine Textlogik sind:

1. Der Datensatz — jedes Bild muss im Manifest stehen, aus dem `media` es
   holt, und jedes Bild im Manifest muss benutzt werden. Fehlt ein Bild beim
   Start, bricht die Aufgabe mit Grund ab, statt mitten im Lauf zu scheitern
   und dort auszusehen wie ein Modell, das nicht antwortet.
2. Der Vertrag — die Referenz erreicht das Modell nicht, und die Sperrliste
   trifft weder die Musterlösung noch die Anweisung.
3. Der Oracle-Lauf, in-process, mitsamt Gegenproben.
"""

import json
import re
from importlib import resources
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ContentImage, ContentText

from german_seo_bench.checks import boilerplate_hit, format_ok, head_hit, is_german, phrase_hit
from german_seo_bench.dataset import load_dataset
from german_seo_bench.media import ENV_DIR, manifest, media_path
from conftest import assert_prompt_corridor
from german_seo_bench.tasks.m10_alt_text import (
    CHECKS,
    DATA,
    INSTRUCTIONS,
    LEN_HARD_MAX,
    LEN_MAX,
    LEN_MAX_PROMPT,
    LEN_MIN,
    LEN_MIN_PROMPT,
    m10_alt_text,
    prompt,
)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA, prompt)


@pytest.fixture(scope="module")
def samples(dataset) -> list[dict]:
    """Die Datensätze so, wie der Scorer sie sieht: Eingabe und Referenz."""
    return [{"id": sample.id, **sample.metadata} for sample in dataset]


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    """Testläufe schreiben ihre Logs ins Temporäre, nicht nach ./logs.

    Sonst liegt nach jedem Testlauf eine Datei zwischen den echten Messungen,
    die keine Messung ist.
    """
    return str(tmp_path_factory.mktemp("eval-logs"))


class TestDatensatz:
    def test_ids_sind_eindeutig(self, samples) -> None:
        ids = [s["id"] for s in samples]
        assert len(set(ids)) == len(ids)

    def test_jedes_bild_steht_im_manifest(self, samples) -> None:
        """Ohne Eintrag kann `media` das Bild nicht holen.

        Die Aufgabe meldete es dann bei jedem Start als fehlend, und niemand
        könnte es beschaffen.
        """
        listed = {entry.path for entry in manifest()}
        for sample in samples:
            assert sample["image"] in listed, sample["id"]

    def test_kein_bild_wird_doppelt_benutzt(self, samples) -> None:
        # Zwei Datensätze auf demselben Bild wären eine Messung, die wie zwei
        # aussieht.
        images = [s["image"] for s in samples]
        assert len(set(images)) == len(images)

    def test_jedes_bild_im_manifest_wird_benutzt(self, samples) -> None:
        """Kein Bild wird geholt, ohne dass ein Datensatz es benutzt.

        Die Gegenrichtung, und die praktisch wichtigere: Bilder kommen dazu,
        indem jemand sie ins Manifest schreibt. Bleibt der Datensatz dahinter
        zurück, misst die Aufgabe stillschweigend weniger, als das Manifest
        hergibt — und ein zu kleiner Datensatz läuft genauso sauber durch wie
        ein vollständiger.
        """
        listed = {e.path for e in manifest() if e.path.startswith("m10_alt_text/")}
        used = {sample["image"] for sample in samples}
        assert listed - used == set(), f"Bild ohne Datensatz: {sorted(listed - used)}"

    def test_manifest_verweist_auf_das_richtige_bild(self) -> None:
        # Dateiname, Pixabay-ID und Seite müssen dasselbe Bild meinen. Ein
        # vertippter Eintrag holte sonst ein fremdes Bild, und die Prüfsumme
        # fiele erst beim nächsten Neuanlegen auf.
        for entry in manifest():
            assert entry.source == "pixabay", entry.path
            assert entry.path == f"m10_alt_text/{entry.id}.jpg"
            assert entry.page_url.startswith("https://pixabay.com/")
            assert entry.page_url.rstrip("/").endswith(f"-{entry.id}"), entry.path
            assert re.fullmatch(r"[0-9a-f]{64}", entry.sha256), entry.path
            assert entry.author, entry.path

    def test_jeder_datensatz_hat_ein_hauptmotiv(self, samples) -> None:
        # Eine leere Liste — oder ein leerer Eintrag darin — macht `subject` zur
        # Prüfung, die immer besteht, ohne dass es auffiele.
        for sample in samples:
            assert sample["subject"], f"{sample['id']}: kein Hauptmotiv"
            assert all(p.strip() for p in sample["subject"]), f"{sample['id']}: leerer Eintrag"

    def test_jeder_datensatz_hat_einen_zusammenhang(self, samples) -> None:
        for sample in samples:
            assert sample["context"], f"{sample['id']}: kein Bildzusammenhang"
            assert all(p.strip() for p in sample["context"]), f"{sample['id']}: leerer Eintrag"

    def test_jeder_datensatz_hat_eine_sperrliste(self, samples) -> None:
        # `grounded` ist die Prüfung, um die es in dieser Aufgabe geht. Ein
        # Datensatz ohne Sperrliste besteht sie immer und misst sie nie.
        for sample in samples:
            assert sample["forbidden"], f"{sample['id']}: leere Sperrliste"

    def test_sperrwort_ist_nicht_zugleich_erlaubt(self, samples) -> None:
        """Ein Wort kann nicht zugleich verlangt und verboten sein.

        Stünde "Stadt" im Zusammenhang und in der Sperrliste, schlösse der
        Datensatz sich selbst aus: Ein Text, der `context` erfüllt, verfehlte
        `grounded`, und der Datensatz wäre unlösbar statt schwer. Das fiele
        erst im Lauf auf — und dann als Modellversagen.
        """
        for sample in samples:
            for phrase in sample["subject"] + sample["context"]:
                hit = phrase_hit(phrase, sample["forbidden"])
                assert hit is None, f"{sample['id']}: {phrase!r} trifft Sperrwort {hit!r}"

    def test_motiv_und_zusammenhang_sind_trennscharf(self, samples) -> None:
        """Keine Phrase darf zwei Prüfungen auf einmal erfüllen.

        Stünde dieselbe Phrase in `subject` und `context`, erfüllte ein Wort
        beide Spalten, und aus zwei Bedingungen würde stillschweigend eine. Eine
        Prüfung, die nichts mehr verlangt, meldet keinen Fehler — sie meldet
        einen Treffer.

        Geprüft wird in **beide** Richtungen und mit derselben Regel wie die
        Wertung (`head_hit`): Eine mehrgliedrige Phrase kann die andere
        enthalten ("Brandenburger Tor" trägt "Tor"), und ein Kompositum sein
        Grundwort ("Streitwagen" trägt "Wagen") — beides derselbe Fall.

        Ein Bestimmungswort zählt nicht — "Kamera" steckt in "Kameramann",
        aber nicht als Grundwort.
        """
        for sample in samples:
            for motif in sample["subject"]:
                for situation in sample["context"]:
                    assert head_hit(motif, [situation]) is None, (
                        f"{sample['id']}: Zusammenhang {situation!r} steckt in "
                        f"Motiv {motif!r}"
                    )
                    assert head_hit(situation, [motif]) is None, (
                        f"{sample['id']}: Motiv {motif!r} steckt in "
                        f"Zusammenhang {situation!r}"
                    )


class TestVertrag:
    def test_prompt_bekommt_nur_den_bildpfad(self) -> None:
        """Die Regel, an der alles hängt, von der Datenseite her geprüft.

        `load_dataset` gibt `prompt()` ausschließlich `input`. Stünde dort ein
        einziges Feld der Referenz, wäre die Antwort mitgeliefert — und zwar
        ohne dass es irgendwo aufflöge.
        """
        raw = load_dataset(DATA, lambda inputs: repr(sorted(inputs)))
        for sample in raw:
            assert sample.input == "['image']", sample.id

    def test_prompt_traegt_text_und_bild(self, dataset) -> None:
        """Ein Bild ohne Anweisung ist keine Aufgabe, eine Anweisung ohne Bild
        keine Bildaufgabe.

        Geprüft wird auch die Reihenfolge: Der Text steht vor dem Bild, und das
        ist Teil der Messbedingung (siehe Modul-Docstring der Aufgabe). Wer sie
        umdreht, misst etwas anderes — ohne dass sich eine Zahl im Log ändert,
        die darauf hinwiese.
        """
        for sample in dataset:
            assert isinstance(sample.input, list), sample.id
            assert len(sample.input) == 1, sample.id
            content = sample.input[0].content
            assert isinstance(content, list), sample.id
            assert isinstance(content[0], ContentText), sample.id
            assert isinstance(content[1], ContentImage), sample.id
            assert content[0].text == INSTRUCTIONS, sample.id
            assert content[1].image == media_path(sample.metadata["image"]), sample.id

    def test_anweisung_nennt_das_motiv_nicht(self, dataset) -> None:
        """Die Lehre aus M03, hier von der anderen Seite.

        Der Prompt sagt, WAS verlangt ist (Länge, Sprache, Format, keine
        Floskel) — und nichts darüber, was auf dem Bild zu sehen ist. Stünde
        auch nur ein Motiv- oder Zusammenhangswort in der Anweisung, maße
        `subject` Anweisungsbefolgung statt Sehvermögen, und die Spalte sähe
        gesättigt aus, ohne dass jemand den Grund fände.

        Geprüft wird über Teilzeichenketten und nicht mit `phrase_hit` —
        absichtlich strenger als die Wertung. Der Fall dahinter: Die Anweisung
        enthält "Zusammenhang", und solange "Hang" in einer Zusammenhangsliste
        stand, hätte ein Modell, das die Anweisung nachplappert, den Punkt dafür
        bekommen. Unter Wortgrenzen wäre das kein Treffer — die Nähe bleibt
        trotzdem eine schlechte Idee, weil sie bei der nächsten Erweiterung der
        Liste zum Treffer wird.
        """
        for sample in dataset:
            phrases = sample.metadata["subject"] + sample.metadata["context"]
            found = [p for p in phrases if p.lower() in INSTRUCTIONS.lower()]
            assert not found, f"{sample.id}: Anweisung nennt {found}"

    def test_sperrwort_trifft_die_anweisung_nicht(self, dataset) -> None:
        """Die Gegenprobe gegen ein zu kurzes oder zu allgemeines Sperrwort.

        Die Anweisung ist gewöhnliche deutsche Prosa; schlägt ein Sperrwort dort
        an, ist es für eine Sperrliste untauglich — jeder Text, der die
        Anweisung nachplappert, verlöre `grounded`.
        """
        for sample in dataset:
            hit = phrase_hit(INSTRUCTIONS, sample.metadata["forbidden"])
            assert hit is None, f"{sample.id}: {hit!r} trifft schon die Anweisung"

    def test_musterloesung_nicht_im_prompt(self, dataset) -> None:
        for sample in dataset:
            assert sample.metadata["oracle"] not in str(sample.input), sample.id

    def test_musterloesung_besteht_jede_pruefung_einzeln(self, samples) -> None:
        """Die Musterlösung gegen die sieben Prüfungen, ohne Eval-Rahmen.

        Der Oracle-Lauf weiter unten prüft dasselbe über den echten Scorer. Der
        Wert dieser Probe ist, dass sie beim Bauen eines Datensatzes sagt,
        WELCHE Prüfung die Musterlösung verfehlt — der Oracle-Lauf sagt nur,
        dass eine Zahl nicht 1.00 ist.
        """
        for sample in samples:
            text = sample["oracle"]
            assert LEN_MIN <= len(text) <= LEN_MAX, f"{sample['id']}: {len(text)} Zeichen"
            assert head_hit(text, sample["subject"]), f"{sample['id']}: Hauptmotiv nicht benannt"
            assert head_hit(text, sample["context"]), f"{sample['id']}: Zusammenhang nicht benannt"
            assert is_german(text), sample["id"]
            assert phrase_hit(text, sample["forbidden"]) is None, sample["id"]
            assert format_ok(text), sample["id"]
            assert boilerplate_hit(text) is None, sample["id"]

    def test_prompt_korridor_ist_enger_als_die_pruefung(self, samples) -> None:
        assert_prompt_corridor(
            "M10", LEN_MIN, LEN_MAX, LEN_MIN_PROMPT, LEN_MAX_PROMPT,
            [(s["id"], s["oracle"]) for s in samples],
        )

    def test_anweisung_nennt_den_engeren_korridor(self) -> None:
        # Die Konstanten allein genügen nicht: Sie könnten gesetzt sein, ohne
        # dass der Prompt sie benutzt — dann stünde dort weiter der
        # Prüfkorridor, und die Regel wäre reine Deko.
        assert f"{LEN_MIN_PROMPT} und {LEN_MAX_PROMPT}" in INSTRUCTIONS
        assert f"{LEN_MIN} und {LEN_MAX}" not in INSTRUCTIONS

    def test_kein_target(self, dataset) -> None:
        # M10 vergleicht nicht gegen eine richtige Antwort — zu einem Bild gibt
        # es viele richtige ALT-Texte. Ein gesetztes Target würde im Log eine
        # Wertung suggerieren, die es nicht gibt.
        for sample in dataset:
            assert not sample.target, sample.id

    def test_stufe_steht_nirgends(self) -> None:
        """M10 hat bewusst kein Stufenprofil — und soll keins vortäuschen.

        32 Bilder trügen fünf Stufen ohne Weiteres; was fehlt, ist die
        Messung. Eine Stufe ist hier ein Befund aus `itemcheck` über
        mindestens drei Modelle, keine Einschätzung beim Anlegen des
        Datensatzes.

        Trüge ein Datensatz trotzdem ein `level`, sähe die Datendatei aus wie
        die von M01 und M03, und der nächste Handgriff wäre,
        `profile("level")` in die Metriken zu schreiben — eine Spalte, die wie
        ein Schwierigkeitsprofil aussieht und eine Schätzung ist.
        """
        raw = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
        for record in json.loads(raw)["samples"]:
            assert "level" not in record["reference"], record["id"]
            assert "level" not in record["input"], record["id"]


# Ein ALT-Text zum Datensatz `pinguine`, der jede Prüfung außer der Länge
# sicher besteht: Motiv vorne, Zusammenhang (Ufer, Brandung, Meer), kein
# Sperrwort, keine Floskel.
_PINGUIN_LONG = (
    "Pinguine stehen in kleinen Gruppen auf felsigem Ufer, dahinter rollt die "
    "Brandung vor blauem Meer unter klarem Himmel bei hellem Sonnenlicht, weiter "
    "hinten liegen flache Steine im Wasser der Bucht"
)


def _pinguin_text(chars: int) -> str:
    text = _PINGUIN_LONG[:chars]
    # Ein Leerzeichen am Ende striche der Scorer weg; der Text wäre kürzer.
    return text[:-1] + "s" if text.endswith(" ") else text


def _score(dataset, sample_id: str, text: str):
    """Den Scorer direkt auf einen Text zu einem Datensatz anwenden."""
    import asyncio

    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import TaskState

    from german_seo_bench.tasks.m10_alt_text import alt_text_scorer

    sample = next(s for s in dataset if s.id == sample_id)
    state = TaskState(
        model="test", sample_id=sample_id, epoch=1,
        input=sample.input, messages=[], metadata=sample.metadata,
    )
    state.output = ModelOutput.from_content(model="test", content=text)
    return asyncio.run(alt_text_scorer()(state, None))


def _score_pinguin(dataset, text: str):
    return _score(dataset, "pinguine", text)


class TestGrundwort:
    """Motiv und Zusammenhang treffen als Grundwort, die Sperrliste nicht.

    Die Texte sind Antworten aus echten Läufen, die unter der strengen Regel
    durchfielen, obwohl sie das Bild richtig beschreiben.
    """

    @pytest.mark.parametrize(
        ("sample_id", "text"),
        [
            ("porsche", "Brauner Porsche 911 auf einer Landstraße vor einer trockenen Hügellandschaft mit kahlen Bäumen."),
            ("strandstuehle", "Rote und blaue Strandstühle im weißen Sand vor türkisblauem Meer und Bergen unter blauem Himmel."),
            ("bootshaus", "Eine rustikale Holzhütte auf Stelzen am Wasser, zu der ein langer Holzsteg durch grünes Laub führt."),
            ("ladestecker", "Blaue Elektroauto-Ladestation mit Stecker und Kabel vor einem blauen Hintergrund."),
            ("reichstag", "Das Reichstagsgebäude in Berlin unter blauem Himmel mit Menschen auf dem Platz davor."),
            ("schaf", "Ein weißes Lamm liegt im Gras am Fuße eines Baumstamms vor einem Bach mit dichtem Uferbewuchs."),
            ("quadriga", "Bronzene Siegesgöttin Victoria auf einem viergespannten Streitwagen vor blauem Himmel."),
            ("radrennen", "Radrennfahrer in Trikots auf einer asphaltierten Strecke, flankiert von Zuschauern und Begleitmotorrädern."),
            ("kassendisplay", "Ein digitaler Kassenmonitor zeigt den Text Kasse 1 geschlossen über einer Ladentheke."),
            ("erdfunkstelle", "Mehrere große weiße Radioteleskope auf einer grünen Wiese unter blauem Himmel."),
            ("keramikladen", "Ein Händler sitzt in einem Laden, der bis zur Decke mit bunten, handbemalten Keramiktellern gefüllt ist."),
            ("bootshaus", "Holzgebäude auf Pfählen am See, zugänglich über eine hölzerne Steganlage hinter Bäumen."),
            ("kuerbisse", "Vielfalt von Kürbissen unterschiedlicher Größe und Farbe auf einem Feld."),
        ],
    )
    def test_richtige_beschreibung_besteht(self, dataset, sample_id: str, text: str) -> None:
        score = _score(dataset, sample_id, text)
        assert score.value["score"] == 1, score.explanation

    def test_falsches_kompositum_scheitert_an_der_sperrliste(self, dataset) -> None:
        # "Seilbahn" trifft als Grundwort "Bahn" — die Sperrliste fängt es.
        text = "Rote Seilbahn fährt durch grüne Berglandschaft mit Dörfern und Wäldern."
        score = _score(dataset, "alpenbahn", text)
        assert score.value["subject"] == 1
        assert score.value["grounded"] == 0
        assert score.metadata["invented"] == "Seilbahn"

    def test_sperrliste_bleibt_wortgetreu(self, dataset) -> None:
        # "Neuschnee" ist kein "Schnee" im Sinne der Sperrliste: Dort gilt
        # die strenge Regel, sonst sperrte "Auto" auch "Elektroauto-Ladestation".
        base = "Pinguine stehen auf felsigem Ufer vor blauem Meer, {} liegt keiner."
        assert _score(dataset, "pinguine", base.format("Neuschnee")).value["grounded"] == 1
        # Gegenprobe: das Sperrwort selbst schlägt an.
        assert _score(dataset, "pinguine", base.format("Schnee")).value["grounded"] == 0


class TestLaengenabzug:
    @pytest.mark.parametrize(
        ("chars", "expected"),
        [
            # Beide Seiten jeder Stufengrenze — sonst fiele eine um eins
            # verschobene Stufe nicht auf.
            (60, 1),
            (125, 1),
            (126, 0.9),
            (135, 0.9),
            (136, 0.7),
            (145, 0.7),
            (146, 0.5),
            (155, 0.5),
        ],
    )
    def test_ueberlaenge_zieht_gestaffelt_ab(self, dataset, chars: int, expected: float) -> None:
        assert (LEN_MAX, LEN_HARD_MAX) == (125, 155)  # die Längen oben sind darauf gebaut
        text = _pinguin_text(chars)
        assert len(text) == chars
        score = _score_pinguin(dataset, text)
        assert all(score.value[check] == 1 for check in CHECKS), score.explanation
        assert score.value["score"] == expected
        assert score.value["length_ok"] == int(chars <= LEN_MAX)
        assert score.metadata["overlength"] == max(0, chars - LEN_MAX)
        if chars > LEN_MAX:
            assert f"{chars - LEN_MAX} Zeichen zu lang" in score.explanation

    @pytest.mark.parametrize("chars", [LEN_HARD_MAX + 1, len(_PINGUIN_LONG), LEN_MIN - 1])
    def test_ausserhalb_der_stufen_scheitert_die_laenge(self, dataset, chars: int) -> None:
        score = _score_pinguin(dataset, _pinguin_text(chars))
        assert score.value["length"] == 0
        assert score.value["length_ok"] == 0
        assert score.value["score"] == 0
        assert "gescheitert an: length" in score.explanation

    def test_abzug_nur_hinter_dem_tor(self, dataset) -> None:
        # Zu lang UND mit Floskel: Die Floskel kostet den Datensatz, der
        # Längenabzug kommt nicht noch obendrauf und macht ihn nicht negativ.
        score = _score_pinguin(dataset, "Das Bild zeigt " + _pinguin_text(120))
        assert score.value["opening"] == 0
        assert score.value["length_ok"] == 0
        assert score.value["score"] == 0


class TestBilderBeimStart:
    def test_ohne_bilder_bricht_die_aufgabe_mit_grund_ab(self) -> None:
        # conftest.py setzt ein leeres Medienverzeichnis.
        with pytest.raises(ValueError, match=r"Bilder fehlen \(32 von 32\).*german_seo_bench\.media"):
            m10_alt_text()

    def test_meldung_nennt_den_gesuchten_pfad(self, monkeypatch, tmp_path) -> None:
        # Wer aus dem falschen Ordner startet, muss an der Meldung sehen, wo
        # gesucht wurde — sonst sucht er den Fehler beim Holen.
        monkeypatch.delenv(ENV_DIR, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError) as error:
            m10_alt_text()
        assert str(tmp_path.resolve() / "bench-media") in str(error.value)
        assert ENV_DIR in str(error.value)

    def test_mit_bildern_zeigt_der_pfad_ins_medienverzeichnis(self, media_present) -> None:
        # Die Gegenprobe: Liegen die Dateien da, baut die Aufgabe, und jedes
        # Bild kommt aus dem Medienverzeichnis — nicht aus dem Paket.
        built = m10_alt_text()
        images = [
            part.image
            for sample in built.dataset
            for message in sample.input
            for part in message.content
            if isinstance(part, ContentImage)
        ]
        assert len(images) == len(built.dataset)
        assert all(Path(image).is_relative_to(media_present) for image in images)
        assert all(Path(image).is_file() for image in images)

    def test_oracle_braucht_keine_bilder(self) -> None:
        # Damit `uv run pytest` auf einem frischen Checkout ohne Netz läuft.
        built = m10_alt_text(oracle=True)
        assert all(isinstance(sample.input[0].content, str) for sample in built.dataset)


class TestOracle:
    """Der Smoke-Test der Wertung: Die Musterlösung muss 1.00 ergeben."""

    @pytest.fixture(scope="class")
    @classmethod
    def log(cls, log_dir: str):
        return inspect_eval(
            m10_alt_text(oracle=True), model="none/none", log_dir=log_dir
        )[0]

    def test_lauf_erfolgreich(self, log) -> None:
        assert log.status == "success", log.error

    def test_alle_datensaetze_bewertet(self, log, dataset) -> None:
        assert len(log.samples) == len(dataset)

    @pytest.mark.parametrize("check", ("score", "length_ok") + CHECKS)
    def test_metrik_ist_eins(self, log, check: str) -> None:
        score = next(s for s in log.results.scores if s.name == check)
        assert score.metrics["mean"].value == 1.0

    def test_bild_steht_in_der_wertung(self, log) -> None:
        """Ohne diese Zeile ist hinterher nicht zu LESEN, welches Bild es war.

        Inspect ersetzt den Bildpfad vor der Anfrage durch ein Data-URI und
        legt dieses im Log als Anhang ab; in der Eingabe steht danach
        `attachment://<hash>`. Die Bytes gehen dabei nicht verloren — der
        Dateiname schon. Er überlebt allein über `Score.metadata`, und ohne ihn
        müsste man ein Bild über seine Bytes im Repo wiederfinden, um einer
        Wertung anzusehen, welches Motiv sie betrifft.
        """
        for sample in log.samples:
            score = next(iter(sample.scores.values()))
            assert score.metadata["image"].startswith("m10_alt_text/"), sample.id

    def test_gegenprobe_scorer_unterscheidet(self, log_dir: str) -> None:
        # Ohne diese Probe wäre "alles 1.00" auch mit einem Scorer erreichbar,
        # der immer 1.00 liefert.
        broken = self._run(
            log_dir, _fixed_solver('**"Image of a beautiful landscape"**')
        )
        assert self._metric(broken, "score") == 0.0

    def test_gegenprobe_floskel_kostet_den_datensatz(self, log_dir: str) -> None:
        """Die Gegenprobe, auf die es in dieser Aufgabe ankommt.

        Ein ALT-Text, der inhaltlich stimmt und mit "Das Bild zeigt" anfängt,
        ist das häufigste Ergebnis eines Sprachmodells überhaupt. Er muss an
        `opening` scheitern und an sonst nichts — sonst wäre in der Tabelle
        nicht zu unterscheiden, ob ein Nachbearbeitungsschritt hilft.
        """
        floskel = self._run(log_dir, _wrapping_solver("Das Bild zeigt {}"))
        assert self._metric(floskel, "opening") == 0.0
        assert self._metric(floskel, "score") == 0.0
        # Alles andere muss unberührt bleiben.
        for check in ("subject", "context", "german", "grounded", "format"):
            assert self._metric(floskel, check) == 1.0, check

    def test_gegenprobe_erfundenes_kostet_den_datensatz(self, log_dir: str) -> None:
        """Dasselbe für die Prüfung, um die es in dieser Aufgabe geht.

        Angehängt wird je Datensatz ein Wort aus **seiner eigenen** Sperrliste
        und nicht ein für alle Bilder gemeinsames Klischee. Das ist der
        Unterschied zwischen einer scharfen und einer ungefähren Gegenprobe:
        Ein gemeinsames Wort steht nur in manchen Sperrlisten, und der Test
        könnte dann nur noch "nicht mehr 1.00" behaupten — womit er einen
        Scorer durchließe, der `grounded` bloß an einem von vier Datensätzen
        erkennt.
        """
        erfunden = self._run(log_dir, _forbidden_solver())
        assert self._metric(erfunden, "grounded") == 0.0
        assert self._metric(erfunden, "score") == 0.0
        # Alles andere muss unberührt bleiben — sonst wäre nicht gezeigt, dass
        # es die Sperrliste war, die angeschlagen hat.
        for check in ("length", "subject", "context", "german", "format", "opening"):
            assert self._metric(erfunden, check) == 1.0, check

    def test_gegenprobe_ein_wort_reicht_nicht(self, log_dir: str) -> None:
        # Die Untergrenze des Korridors. Ohne sie bestünde "Pinguine." die
        # Prüfung `subject` und sagte nichts.
        knapp = self._run(log_dir, _fixed_solver("Pinguine."))
        assert self._metric(knapp, "length") == 0.0
        assert self._metric(knapp, "score") == 0.0

    @staticmethod
    def _run(log_dir: str, solver):
        return inspect_eval(
            m10_alt_text(oracle=True), model="none/none", log_dir=log_dir, solver=solver
        )[0]

    @staticmethod
    def _metric(log, name: str) -> float:
        return next(s for s in log.results.scores if s.name == name).metrics["mean"].value


def _fixed_solver(content: str):
    """Antwortet auf jeden Datensatz mit derselben Zeichenkette."""
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def fixed() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(model="broken", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return fixed()


def _forbidden_solver():
    """Musterlösung plus ein Wort aus der Sperrliste DIESES Datensatzes.

    Der Satz drumherum ist deutsch und formatfrei, damit genau eine Prüfung
    bricht: `grounded`. Ein bloßes Anhängen des Wortes täte es auch, läse sich
    im Log aber wie ein Tippfehler statt wie eine erfundene Bildaussage.
    """
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def invented() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            word = state.metadata["forbidden"][0]
            content = f"{state.metadata['oracle']} Im Hintergrund {word}."
            state.output = ModelOutput.from_content(model="invented", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return invented()


def _wrapping_solver(template: str):
    """Antwortet mit der Musterlösung des Datensatzes, in `template` verpackt.

    `template` enthält ein `{}` an der Stelle, an der der ALT-Text steht. So
    lässt sich genau EINE Prüfung brechen und alle anderen intakt lassen —
    anders als mit einer festen Zeichenkette, die immer an mehreren scheitert.
    """
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def wrapping() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            content = template.replace("{}", state.metadata["oracle"])
            state.output = ModelOutput.from_content(model="wrapped", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return wrapping()
