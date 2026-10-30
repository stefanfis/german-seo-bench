"""Die A01-Dokumentation gegen die Daten gehalten.

Warum es diese Datei gibt: `docs/A01.md` nennt Zahlen, listet jede Adresse mit
ihren Befunden auf und verrät am Ende die Auflösung. Das ist für Menschen
geschrieben und veraltet lautlos — fügt jemand ein Item hinzu, bleibt die
Tabelle stehen und erzählt etwas Falsches. Ein Leser merkt es nicht, weil beides
plausibel aussieht.

Geprüft wird deshalb nicht der Text, sondern die **Übereinstimmung mit dem
Datensatz**: jede Zahl, jede Adresse, jedes Paar. Was hier nicht geprüft wird,
ist die Prosa — die bleibt Handarbeit.

Die technische Beschreibung von A01 steht in `docs/tasks/a01_browser_audit.md`.
`docs/A01.md` steht trotzdem daneben, weil sie ein anderes Publikum hat — die
Aufgabenbeschreibung erklärt, WARUM die Aufgabe so gebaut ist, `docs/A01.md`
erklärt einem SEO,
WAS auf der Prüflandschaft zu finden ist und wie er sie bei sich startet. Diese
Testdatei ist der Preis dafür: Zwei Orte mit Zahlen brauchen eine Klammer.
"""

import json
import re
from pathlib import Path

import pytest

from german_seo_bench.dataset import data_path
from german_seo_bench.tasks.a01_browser_audit import CAPABILITY_CLASSES, SITE_URL

DOKU = Path(__file__).resolve().parents[1] / "docs" / "A01.md"


@pytest.fixture(scope="module")
def text() -> str:
    return DOKU.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads(Path(data_path("a01_browser_audit.json")).read_text(
        encoding="utf-8"
    ))["samples"][0]


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(Path(data_path("a01_browser_audit.json")).read_text(
        encoding="utf-8"
    ))


def befund_tabelle(text: str) -> dict[str, set[str]]:
    """Die Auflösungstabelle „Befunde je Adresse" als Abbildung.

    Gelesen wird die Tabelle und nicht der Fließtext: Sie ist die Stelle, an der
    eine Drift inhaltlich falsch wird statt nur unscharf.
    """
    block = re.search(
        r"### Befunde je Adresse\n\n\| Adresse \| Befunde \|\n\|[-| ]+\|\n((?:\|.*\n)+)",
        text,
    )
    assert block, 'die Tabelle „Befunde je Adresse“ fehlt'
    gefunden: dict[str, set[str]] = {}
    for zeile in block.group(1).strip().splitlines():
        spalten = [s.strip() for s in zeile.strip("|").split("|")]
        seite = spalten[0].strip("*` ")
        issues = set(re.findall(r"`(\w+)`", spalten[1]))
        gefunden[seite] = issues
    return gefunden


class TestZahlen:
    def test_kopfzeile_nennt_den_aktuellen_zuschnitt(self, text, doc, sample) -> None:
        """Die fünf Zahlen ganz oben — sie werden am ehesten zitiert."""
        kopf = text.split("---", 1)[0]
        befunde = sample["reference"]["findings"]
        betroffen = {f["page"] for f in befunde}
        erwartet = {
            "Aufgabenversion": doc["version"],
            "Adressen": len(sample["input"]["pages"]),
            "Fehlertypen": len(sample["input"]["catalog"]),
            "Befunde": len(befunde),
            "Kontrollseiten": len(set(sample["input"]["pages"]) - betroffen),
        }
        for name, zahl in erwartet.items():
            assert re.search(rf"\b{zahl}\b[^\n]*{name}|{name}[^\n]*\b{zahl}\b", kopf), (
                f"die Kopfzeile nennt {name} nicht mit {zahl}: {kopf!r}"
            )

    def test_klassentabelle_stimmt_mit_der_referenz(self, text, sample) -> None:
        from collections import Counter

        klassen = sample["reference"]["capability_by_issue"]
        ist = Counter(klassen[f["issue"]] for f in sample["reference"]["findings"])
        block = re.search(
            r"\| Klasse \| genügt zum Finden \| Befunde \|\n\|[-| ]+\|\n((?:\|.*\n)+)",
            text,
        )
        assert block, "die Klassentabelle fehlt"
        gelesen = {}
        for zeile in block.group(1).strip().splitlines():
            spalten = [s.strip() for s in zeile.strip("|").split("|")]
            gelesen[spalten[0].strip("` ")] = int(spalten[2])
        assert gelesen == dict(ist), (gelesen, dict(ist))
        assert set(gelesen) == set(CAPABILITY_CLASSES)


class TestAufloesung:
    def test_jede_adresse_steht_mit_ihren_befunden_da(self, text, sample) -> None:
        """Die eigentliche Zusicherung: die Auflösung ist die Referenz.

        Eine Doku, die einen Befund zu viel oder zu wenig nennt, schickt einen
        Leser auf die Suche nach etwas, das es nicht gibt — oder lässt ihn
        glauben, er habe alles.
        """
        aus_daten: dict[str, set[str]] = {p: set() for p in sample["input"]["pages"]}
        for f in sample["reference"]["findings"]:
            aus_daten[f["page"]].add(f["issue"])
        assert befund_tabelle(text) == aus_daten

    def test_der_katalog_ist_vollstaendig_und_geschlossen(self, text, sample) -> None:
        ids = {e["issue"] for e in sample["input"]["catalog"]}
        block = re.search(r"\| Id \| Beschreibung \|\n\|[-| ]+\|\n((?:\|.*\n)+)", text)
        assert block, "die Katalogtabelle fehlt"
        gelesen = {
            re.match(r"\s*`(\w+)`", zeile.strip("|")).group(1)
            for zeile in block.group(1).strip().splitlines()
        }
        assert gelesen == ids, set(gelesen) ^ ids

    def test_die_seitenliste_ist_die_der_aufgabe(self, text, sample) -> None:
        block = re.search(r"### Die 26 Adressen\n\n```\n(.*?)```", text, re.DOTALL)
        assert block, "der Adressblock fehlt oder die Überschrift nennt eine andere Zahl"
        assert set(block.group(1).split()) == set(sample["input"]["pages"])

    def test_die_fallen_liegen_auf_seiten_die_es_gibt(self, text, sample) -> None:
        block = re.search(
            r"\| Seite \| Falle \| sieht aus wie \|\n\|[-| ]+\|\n((?:\|.*\n)+)", text
        )
        assert block, "die Fallentabelle fehlt"
        zeilen = block.group(1).strip().splitlines()
        seiten = [re.search(r"`([\w.-]+\.html)`", z).group(1) for z in zeilen]
        assert set(seiten) <= set(sample["input"]["pages"]), set(seiten)
        # Die Anzahl steht auch im Fließtext — beide müssen dasselbe sagen.
        assert f"{len(zeilen)} Fehlalarm-Fallen" in text, (
            f"Tabelle hat {len(zeilen)} Zeilen, der Text nennt eine andere Zahl"
        )

    def test_jede_falle_sagt_die_wahrheit_ueber_ihren_befund(self, text, sample) -> None:
        """Die Gegenrichtung — und sie kennt zwei Arten von Falle.

        Die übliche Falle sieht aus wie ein Befund und ist keiner: Dann darf die
        Seite diesen Befund nicht tragen. Es gibt aber auch die **umgekehrte**
        Falle, die dazu verleitet, einen echten Befund zu übersehen (auf
        `zubehoer.html` ist das nach `width`-Attribut größte Bild nicht das lazy
        geladene). Dort muss die Seite den Befund sehr wohl tragen.

        Erkannt wird die Richtung am Text der dritten Spalte: Fängt er mit
        „kein" an, ist es die umgekehrte. Ohne diese Unterscheidung wäre der
        Test entweder zu streng oder — schlimmer — stumm für beide Fälle.
        """
        befunde = {(f["page"], f["issue"]) for f in sample["reference"]["findings"]}
        block = re.search(
            r"\| Seite \| Falle \| sieht aus wie \|\n\|[-| ]+\|\n((?:\|.*\n)+)", text
        )
        geprueft = 0
        for zeile in block.group(1).strip().splitlines():
            seite = re.search(r"`([\w.-]+\.html)`", zeile).group(1)
            spalten = [s.strip() for s in zeile.strip("|").split("|")]
            umgekehrt = spalten[2].lower().startswith("kein ")
            for issue in re.findall(r"`(\w+)`", spalten[2]):
                geprueft += 1
                if umgekehrt:
                    assert (seite, issue) in befunde, (
                        f"{seite} wird als umgekehrte Falle für {issue} geführt "
                        "('obwohl es eines gibt'), trägt den Befund aber nicht"
                    )
                else:
                    assert (seite, issue) not in befunde, (
                        f"{seite} wird als Falle für {issue} geführt und trägt "
                        "den Befund tatsächlich"
                    )
        # Ohne das prüfte der Test nichts, sobald jemand die Rückfallzeichen
        # in der Tabelle anders setzt.
        assert geprueft >= 5, geprueft


class TestAnleitung:
    def test_die_startbefehle_stimmen(self, text) -> None:
        assert "uv run python -m german_seo_bench.testsite" in text
        assert "--port 8099" in text
        # Der Preis des hohen Ports gehört dazu, sonst führt die Anleitung in
        # eine Sitemap, die ins Leere zeigt.
        assert "sitemap.xml" in text and "ohne Port" in text

    def test_alle_drei_betriebssysteme_kommen_vor(self, text) -> None:
        for abschnitt in ("### Linux", "### macOS", "### Windows"):
            assert abschnitt in text, abschnitt
        # Je Betriebssystem der Weg zur hosts-Datei …
        assert "/etc/hosts" in text
        assert r"C:\Windows\System32\drivers\etc\hosts" in text
        # … und der zu Port 80.
        assert "ip_unprivileged_port_start" in text
        assert "ipconfig /flushdns" in text
        assert "dscacheutil -flushcache" in text

    def test_die_adresse_kommt_aus_der_aufgabe(self, text) -> None:
        host = SITE_URL.removeprefix("http://").rstrip("/")
        assert host in text
        # Und kein erfundener Name, der einem echten Host gehören könnte.
        assert "testsite.de" not in text
        assert "http://testsite/" not in text

    def test_die_spoiler_warnung_steht_vor_der_aufloesung(self, text) -> None:
        """Sonst ist die Doku für den, der selbst raten will, wertlos."""
        warnung = text.find("Achtung, Spoiler")
        # Ohne Abschnittsnummer gesucht: Die darf sich verschieben, wenn ein
        # Kapitel dazukommt — die Reihenfolge darf es nicht.
        aufloesung = text.find("Die Auflösung\n")
        assert aufloesung > 0, "die Überschrift „Die Auflösung“ fehlt"
        assert 0 < warnung < aufloesung, (warnung, aufloesung)
