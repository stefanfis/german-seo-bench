"""Zusicherungen über `uv run german-seo-bench`.

Der Sinn der Hilfe ist, dass sie stimmt. Eine Liste, die eine Aufgabe
verschweigt, schickt den Leser zur falschen Auswahl; ein Beispiel mit einem
Aufgabennamen, den es nicht mehr gibt, bricht beim ersten Kopieren ab. Beides
fällt beim Lesen der Datei nicht auf — deshalb steht es hier.
"""

import subprocess
import sys

import pytest

from german_seo_bench import _registry
from german_seo_bench.help import EXAMPLE, help_text, main, task_cards


@pytest.fixture(scope="module", autouse=True)
def _media(media_present):
    """Die Hilfe so, wie sie nach dem Holen der Bilder aussieht.

    Ohne Bilder ist M10 nicht startbar; das prüft `TestOhneBilder`.
    """
    return media_present


@pytest.fixture(scope="module")
def cards():
    return task_cards()


@pytest.fixture(scope="module")
def text():
    return help_text()


class TestAufgabenliste:
    """Die Liste wird erzeugt, nicht gepflegt — hier steht, woraus."""

    def test_jede_aufgabe_des_pakets_steht_drin(self, cards) -> None:
        """Gegen die Liste, die Inspect selbst benutzt.

        `_registry.__all__` ist der Bestand, den der Entry-Point bekanntmacht.
        Fehlt dort eine Aufgabe, ist sie unter `uvx` gar nicht aufrufbar; fehlt
        sie in der Hilfe, findet sie niemand. Geprüft wird auf Gleichheit, damit
        beide Richtungen auffallen.
        """
        tasks = {name for name in _registry.__all__ if name != "local"}
        assert {card.name.removeprefix("german_seo_bench/") for card in cards} == tasks

    def test_name_ist_der_name_zum_tippen(self, cards) -> None:
        """Der angezeigte Name muss der Registry-Name sein.

        Sonst zeigt die Hilfe etwas an, das `inspect eval` nicht auflöst. Der
        Vergleich geht gegen Inspects eigene Auskunft, nicht gegen eine zweite
        Ableitung derselben Annahme.
        """
        from inspect_ai._util.registry import registry_info

        import importlib

        for card in cards:
            module = card.name.removeprefix("german_seo_bench/")
            fn = getattr(importlib.import_module(f"german_seo_bench.tasks.{module}"), module)
            assert registry_info(fn).name == card.name

    def test_anzahl_der_datensaetze_stimmt(self, cards) -> None:
        # Eine falsche Zahl ist schlimmer als keine: Sie sieht aus wie eine
        # Angabe über den Umfang der Messung.
        import importlib

        for card in cards:
            if card.unavailable:
                continue  # siehe test_jede_aufgabe_ist_startbar
            module = card.name.removeprefix("german_seo_bench/")
            built = getattr(importlib.import_module(f"german_seo_bench.tasks.{module}"), module)()
            assert card.samples == len(built.dataset), card.name
            assert card.samples > 0, card.name

    def test_jede_aufgabe_ist_startbar(self, cards) -> None:
        """Die Hilfe zeigt eine nicht baubare Aufgabe mit Grund, statt abzubrechen.

        Diese Nachsicht gilt dem, was beim Nutzer fehlt (die Bilder von M10,
        eine leere eigene Bewertungsdatei), und darf nicht zur Gewohnheit
        werden: Ausgeliefert wird nur, was startet.
        """
        assert {card.name: card.unavailable for card in cards if card.unavailable} == {}

    def test_bildaufgaben_sind_als_solche_erkannt(self, cards) -> None:
        """Die Gegenprobe zur Ableitung am Datensatz.

        Eine Erkennung, die immer `False` liefert, bestünde einen Test, der nur
        M01 prüft — und eine, die immer `True` liefert, einen, der nur M10
        prüft. Deshalb beide Seiten.
        """
        marked = {card.name for card in cards if card.needs_image_model}
        assert "german_seo_bench/m10_alt_text" in marked
        assert "german_seo_bench/m01_titles" not in marked


class TestOhneBilder:
    """Ein frischer Checkout: M10 steht mit Grund in der Liste, der Rest startet."""

    def test_m10_nennt_den_grund(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("GERMAN_SEO_BENCH_MEDIA", str(tmp_path))
        unavailable = {card.name: card.unavailable for card in task_cards() if card.unavailable}
        assert list(unavailable) == ["german_seo_bench/m10_alt_text"]
        assert unavailable["german_seo_bench/m10_alt_text"].startswith("Bilder fehlen (32 von 32)")

    def test_eine_fehlende_datei_genuegt(self, monkeypatch, media_present) -> None:
        # Die Gegenprobe zur Prüfung auf das ganze Verzeichnis: Fehlt nur ein
        # Bild, liefe der Lauf sonst an und scheiterte mitten drin.
        first = next(media_present.rglob("*.jpg"))
        moved = first.with_suffix(".bak")
        first.rename(moved)
        try:
            card = next(c for c in task_cards() if c.name.endswith("m10_alt_text"))
            assert card.unavailable.startswith("Bilder fehlen (1 von 32)")
        finally:
            moved.rename(first)


class TestHilfetext:
    def test_jede_aufgabe_wird_genannt(self, text, cards) -> None:
        for card in cards:
            assert card.name in text
            assert card.display_name in text

    def test_beispiele_nennen_nur_echte_aufgaben(self, text, cards) -> None:
        """Kein Beispiel darf einen Aufgabennamen tragen, den es nicht gibt.

        Genau das passiert beim Umbenennen oder Entfernen einer Aufgabe: Der
        Listenteil zieht automatisch nach, die Beispieltexte nicht.
        """
        known = {card.name for card in cards}
        genannt = {
            word.strip("\\")
            for word in text.split()
            if word.startswith("german_seo_bench/")
        }
        assert genannt <= known, genannt - known
        assert EXAMPLE in known

    def test_nennt_die_pflichtbefehle(self, text) -> None:
        # Die drei Aufrufe, ohne die man nicht anfangen kann: Prüfen, Messen,
        # Ansehen. Dazu der Oracle-Lauf, an dem jede Zahl hängt.
        for command in (
            "uv run pytest",
            "uv run inspect eval",
            "uv run inspect view",
            "-T oracle=true --model none/none",
        ):
            assert command in text

    def test_zeilen_passen_ins_terminal(self, text) -> None:
        # Umbrechende Beispielzeilen sind nicht kopierbar. 88 Zeichen, die
        # Einrückung eingerechnet (`WIDTH` in help.py).
        zu_lang = [line for line in text.splitlines() if len(line) > 88]
        assert not zu_lang, zu_lang


class TestAufruf:
    """Das Konsolenskript selbst — einmal über den echten Weg."""

    def test_ohne_argument_kommt_die_hilfe(self) -> None:
        done = subprocess.run(
            [sys.executable, "-c", "from german_seo_bench.help import main; main()"],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0
        assert "Verfügbare Aufgaben" in done.stdout

    @pytest.mark.parametrize("argument", ("help", "--help", "-h"))
    def test_help_ist_mitgemeint(self, argument: str, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["german-seo-bench", argument])
        main()
        assert "Verfügbare Aufgaben" in capsys.readouterr().out

    def test_unbekanntes_argument_bricht_ab(self, monkeypatch, capsys) -> None:
        """Die Gegenprobe: Der Befehl misst nichts und tut auch nicht so.

        Ein `german_seo_bench eval …`, das still die Hilfe zeigt, sähe wie ein Lauf aus,
        der nichts gemessen hat.
        """
        monkeypatch.setattr(sys, "argv", ["german-seo-bench", "eval"])
        with pytest.raises(SystemExit) as abbruch:
            main()
        assert abbruch.value.code == 2
        captured = capsys.readouterr()
        assert "Verfügbare Aufgaben" not in captured.out
        assert "inspect eval" in captured.err
