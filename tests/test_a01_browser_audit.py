"""Zusicherungen über die Agent-Aufgabe A01 selbst.

Vier Dinge werden hier geprüft, und keines davon braucht Docker:

1. Die Schranke zwischen Prompt und Referenz — im Prompt steht der Katalog, aber
   nicht, WELCHE Seite welchen Fehler hat.
2. Die Referenz gegen die ausgelieferte Site. Die Paare entstehen aus einem
   Render-Capture (`python -m german_seo_bench.reference`) und liegen als
   Datei vor. Diese Datei kann veralten, wenn jemand `site/` anfasst — für jeden Befund, der im
   Quelltext entscheidbar ist, wird deshalb der Mechanismus nachgewiesen, der
   ihn erzeugt.
3. Die Topologie: dass `compose.yaml` das hergibt, was der Prompt und die
   Wertung annehmen (Dienstname, read-only-Mount, kein Weg nach draußen).
4. Der Oracle-Lauf — derselbe wie bei den M-Aufgaben, nur ohne Container.
"""

import json
import re
from importlib import resources
from pathlib import Path

import pytest
import yaml
from inspect_ai import eval as inspect_eval

from german_seo_bench.checks import as_findings
from german_seo_bench.config import MAX_TOKENS
from german_seo_bench.dataset import data_path, load_dataset
from german_seo_bench.tasks.a01_browser_audit import (
    AGENT_GOOSE,
    ANSWER_MAX_TOKENS,
    CAPABILITY_CLASSES,
    AGENT_REACT,
    AGENTS,
    ANSWER_FILE,
    CHROME_PATH,
    COMPOSE,
    DATA,
    MCP_ARGS,
    MCP_COMMAND,
    MESSAGE_LIMIT,
    MODEL_CALL_TIMEOUT,
    SITE_URL,
    TIME_LIMIT,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    STORE_CLI,
    STORE_REPORT,
    a01_browser_audit,
    audit_report,
    capability_f1,
    extract_markdown,
    assessment_html,
    evaluate_audit,
    insert_assessment,
    report_html,
    run_notes,
    report_prompt,
    goose_command,
    goose_env,
    prompt,
)

# Die Seiten, die keinen Befund haben dürfen — die Kontrolle gegen
# Falschmeldungen, und deshalb namentlich festgehalten:
#
#   blog.html      schlicht sauber
#   ratgeber.html  trägt alle Warnsignale ohne Fehler
#   filter.html    ein KORREKTES noindex und ein korrekter Canonical
#   shop.html      eine einzelne, richtige 301
#
# Ohne die letzten beiden kostete ein pauschales "meldet alles, was nach Muster aussieht" nichts mehr, und
# Precision misst nichts.
CONTROL_PAGES = ("blog.html", "ratgeber.html", "filter.html", "shop.html")

# Seiten ohne eigene Datei: Was unter dieser Adresse passiert, entscheidet die
# nginx-Konfiguration (Weiterleitung oder kontextabhängige Auslieferung).
VIRTUAL_PAGES = ("umzug.html", "shop.html", "preise.html", "tipps.html",
                 "schulen.html", "archiv.html")

# Die Fassungen der kontextabhängig ausgelieferten Seiten. Sie sind keine
# eigenen Seiten und stehen deshalb nicht in der Seitenliste. Das Suffix
# entscheidet, welche Bedingung sie bedienen:
#
#   -visitor / -bot     die Kennung (cloaked_content)
#   -first / -again     die Besuchszahl (noindex_on_second_visit)
VARIANTS = ("preise-visitor.html", "preise-bot.html",
            "schulen-visitor.html", "schulen-bot.html",
            "tipps-first.html", "tipps-again.html",
            "archiv-first.html", "archiv-again.html")


@pytest.fixture(scope="module")
def doc() -> dict:
    raw = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
    return json.loads(raw)


@pytest.fixture(scope="module")
def sample(doc) -> dict:
    assert len(doc["samples"]) == 1, "A01 ist ein Auftrag über die ganze Site"
    return doc["samples"][0]


@pytest.fixture(scope="module")
def site() -> Path:
    return Path(data_path("a01_browser_audit/site"))


@pytest.fixture(scope="module")
def stylesheets(site: Path) -> dict[str, str]:
    """Die beiden Stylesheets, aus deren Zusammenspiel `hidden_by_css` entsteht.

    Sie werden zusammen gelesen, weil der Befund nur zusammen existiert: Jede
    Datei für sich ist harmlos.
    """
    return {
        name: (site / name).read_text(encoding="utf-8")
        for name in ("style.css", "layout.css")
    }


@pytest.fixture(scope="module")
def nginx() -> str:
    return Path(data_path("a01_browser_audit/testsite.nginx.conf")).read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="module")
def compose_messlauf() -> dict:
    """Die Topologie des Messlaufs — zum Vergleich mit der lokalen."""
    return yaml.safe_load(Path(data_path(COMPOSE)).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA, prompt)


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("eval-logs"))


def quelltext(site: Path, page: str) -> str:
    return (site / page).read_text(encoding="utf-8")


def quellen(site: Path, page: str) -> list[str]:
    """Der Quelltext einer Seite — auch dann, wenn sie keine Datei ist.

    Sechs Adressen der Prüflandschaft haben keine eigene Datei: vier werden in
    je zwei Fassungen ausgeliefert (nach Kennung oder nach Besuchszahl),
    `umzug.html` und `shop.html` sind Weiterleitungen. Eine Prüfung, die stumpf
    `site / page` liest, stürbe dort — oder, schlimmer, übersähe die Fassung,
    um die es geht.

    Zugeordnet wird über den Dateinamen und nicht über eine zweite Liste:
    `preise.html` → `preise-*.html`. Eine Aufzählung je Seite liefe beim
    Hinzufügen neuer Items sofort auseinander.
    """
    stamm = page.removesuffix(".html")
    fassungen = [name for name in VARIANTS if name.startswith(stamm + "-")]
    if fassungen:
        return [quelltext(site, name) for name in fassungen]
    if (site / page).exists():
        return [quelltext(site, page)]
    return []


def titel(html: str) -> str:
    treffer = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    return " ".join(treffer.group(1).split()) if treffer else ""


def interne_links(html: str) -> set[str]:
    return set(re.findall(r'<a [^>]*href="([\w.-]+\.html)"', html)) | set(
        re.findall(r'<link [^>]*href="([\w.-]+\.html)"', html)
    )


class TestDatensatz:
    def test_seitenliste_ist_die_ausgelieferte_site(self, sample, site, nginx) -> None:
        """Jede genannte Seite muss es geben, und jede vorhandene genannt sein.

        Sonst nennt der Prompt eine URL, die 404 liefert — oder verschweigt eine
        Seite, auf der ein Befund liegt. Zwei Quellen sind beteiligt: die Dateien in `site/` und die Adressen, die der Server
        selbst beantwortet (Weiterleitung, kontextabhängige Auslieferung). Die
        Fassungen der gecloakten Seite sind keine Seiten.
        """
        dateien = {p.name for p in site.glob("*.html")} - set(VARIANTS)
        aus_nginx = set(re.findall(r"location\s*=\s*/([\w.-]+\.html)", nginx))
        # `umzug-zwischen.html` ist nur ein Zwischenschritt der Kette und keine
        # Adresse, die jemand prüfen soll — sie steht in keiner Verlinkung.
        aus_nginx -= {"umzug-zwischen.html"}
        assert sorted(sample["input"]["pages"]) == sorted(dateien | aus_nginx)

    def test_seiten_ohne_datei_beantwortet_der_server(self, sample, site, nginx) -> None:
        """Die Adressen, hinter denen keine Datei liegt.

        Läge dort eine, entschiede sie mit — und zwar unsichtbar: Eine Datei
        `preise.html` würde bei einem Tippfehler in der Konfiguration
        ausgeliefert, und das Cloaking verschwände, ohne dass eine Zahl es
        verrät.
        """
        for page in VIRTUAL_PAGES:
            assert page in sample["input"]["pages"], page
            assert not (site / page).exists(), f"{page} hat doch eine Datei"
            assert f"location = /{page}" in nginx, page

    def test_katalog_ist_geschlossen_und_eindeutig(self, sample) -> None:
        issues = [entry["issue"] for entry in sample["input"]["catalog"]]
        assert len(issues) == len(set(issues)), "doppelte Id im Katalog"
        assert all(entry["description"].strip() for entry in sample["input"]["catalog"])

    def test_der_katalog_schliesst_den_selbstverweis_aus(self, sample) -> None:
        """`canonical_to_noindex` muss die ANDERE Seite nennen.

        Auf `filter.html` — der Kontrollseite mit korrektem noindex und
        korrektem Selbst-Canonical — wäre eine Beschreibung wie „zeigt auf eine
        Seite, die selbst noindex trägt" wörtlich erfüllt; gemeint ist aber der
        SEO-Fehler
        „Seite A verweist auf Seite B, und B ist noindex", und genau so rechnet
        der Auditor (`reference/audit.py` schließt Selbstverweise aus). Eine
        Beschreibung, die zwei Lesarten zulässt, misst das Lesen und nicht das
        Können.
        """
        beschreibung = next(
            e["description"]
            for e in sample["input"]["catalog"]
            if e["issue"] == "canonical_to_noindex"
        )
        assert "andere" in beschreibung.lower(), beschreibung

    def test_referenz_bleibt_im_katalog_und_auf_den_seiten(self, sample) -> None:
        pages = set(sample["input"]["pages"])
        issues = {entry["issue"] for entry in sample["input"]["catalog"]}
        for finding in sample["reference"]["findings"]:
            assert finding["page"] in pages, finding
            assert finding["issue"] in issues, finding

    def test_referenz_hat_keine_doppelten_paare(self, sample) -> None:
        findings = sample["reference"]["findings"]
        assert len(as_findings(findings)) == len(findings)

    def test_kontrollseiten_tragen_keinen_befund(self, sample) -> None:
        """Ohne sie misst die Aufgabe nur Recall.

        Eine Site, auf der jede Seite einen Fehler hat, belohnt einen Agenten,
        der pauschal meldet. Die beiden sauberen Seiten sind der Preis dafür,
        dass Precision überhaupt etwas bedeutet.
        """
        betroffen = {f["page"] for f in sample["reference"]["findings"]}
        for page in CONTROL_PAGES:
            assert page in sample["input"]["pages"], page
            assert page not in betroffen, f"{page} soll sauber sein"

    def test_ein_problemtyp_kommt_zweimal_vor(self, sample) -> None:
        # Die Falle gegen "ein Treffer je Katalogeintrag, dann aufhören".
        paare = as_findings(sample["reference"]["findings"])
        mehrfach = [
            issue
            for issue in {i for _, i in paare}
            if len([p for p, i in paare if i == issue]) > 1
        ]
        assert "noindex_after_render" in mehrfach, mehrfach

    def test_jede_nebendatei_wird_gebraucht(self, site, sample) -> None:
        """Was ausgeliefert wird, muss einen Zweck haben.

        Die Nebendateien (CSS, JS, SVG) sind keine Seiten und werden nicht
        gewertet — sie existieren, damit die Requests, an denen die Befunde
        hängen, echte Requests sind. Eine Datei, die niemand anfordert, wäre
        Beiwerk in der Prüflandschaft, das ein Agent trotzdem untersucht.
        """
        # Gelesen wird ALLES, was ausgeliefert wird — auch `sitemap.xml` und
        # `robots.txt`, und die Verweise darin sind echte
        # Verweise.
        inhalte = {
            datei.name: datei.read_text(encoding="utf-8")
            for datei in sorted(site.iterdir())
        }
        for extra in site.iterdir():
            if extra.suffix == ".html":
                continue
            # `robots.txt` verlinkt niemand: Ein Crawler holt sie an ihrem
            # festen Ort. Sie ist hier der Einstieg zur `sitemap.xml` und damit
            # selbst der Grund für die Ausnahme.
            if extra.name == "robots.txt":
                continue
            # Gesucht wird in allen ANDEREN Dateien — eine Datei, die nur sich
            # selbst nennt, ist nicht angefordert.
            assert any(
                extra.name in text
                for name, text in inhalte.items()
                if name != extra.name
            ), extra.name


class TestReferenzGegenSite:
    """Für jeden Befund der Mechanismus, der ihn erzeugt — im Quelltext.

    Das ist keine zweite Wertung, sondern der Wachhund über die Datei: Die
    Referenz stammt aus einem Render-Lauf, den german_seo_bench nicht wiederholt. Fasst
    jemand `site/` an, ohne die Referenz nachzuziehen, messen alle folgenden
    Läufe gegen eine Site, die es nicht mehr gibt — und das Ergebnis sähe
    völlig unauffällig aus.

    Geprüft wird der MARKER, nicht der gerenderte Zustand. Was nur ein Browser
    entscheiden kann (CLS-Wert, Blockadezeit in Millisekunden, welches Bild der
    LCP-Kandidat ist), bleibt draußen — dafür gibt es hier keinen Ersatz.
    """

    MARKERS = {
        "title_changed_by_js": r"document\.title\s*=",
        "noindex_after_render": r"content\s*=\s*['\"]noindex",
        "content_requires_js": r"innerHTML\s*=",
        "canonical_only_in_render": r"rel\s*=\s*'canonical'",
        "h1_added_by_js": r"createElement\('h1'\)",
        "client_side_redirect": r"location\.(replace|href)",
        "lazy_lcp_image": r'loading="lazy"',
        "layout_shift_after_load": r"setTimeout",
        "interaction_blocked": r"while \(Date\.now\(\) <",
        "link_without_href": r"<a onclick=",
        "infinite_scroll_no_pagination": r"addEventListener\('scroll'",
    }

    def test_jeder_befund_hat_seinen_marker_in_der_seite(self, sample, site) -> None:
        for finding in sample["reference"]["findings"]:
            marker = self.MARKERS.get(finding["issue"])
            if marker is None:
                continue
            quelle = "\n".join(quellen(site, finding["page"]))
            assert re.search(marker, quelle), (
                f"{finding['page']}: Marker für {finding['issue']} fehlt "
                f"({marker}) — Referenz und Site sind auseinandergelaufen"
            )

    def test_blockierendes_skript_steht_wirklich_vor_dem_css(self, site) -> None:
        kopf = quelltext(site, "assets.html").split("</head>")[0]
        skript = re.search(r'<script src="[^"]+"(?![^>]*(async|defer))', kopf)
        assert skript, "assets.html: kein blockierendes Skript im <head>"
        assert skript.start() < kopf.index("<link rel=\"stylesheet\""), (
            "assets.html: das Skript steht nicht mehr vor dem Stylesheet"
        )

    def test_nachgeladenes_skript_steht_nur_im_javascript(self, site) -> None:
        # script_loads_script: `tracker.js` darf im HTML NICHT vorkommen, sonst
        # ist der Befund im Quelltext sichtbar und der Browser unnötig.
        assert "tracker.js" in quelltext(site, "app.js")
        assert "tracker.js" not in quelltext(site, "assets.html")

    def test_kaputte_ressource_ist_wirklich_kaputt(self, sample, site) -> None:
        # broken_resource: Der Pfad muss ins Leere zeigen. Läge die Datei da,
        # antwortete der Server mit 200 und der Befund verschwände. Geprüft
        # wird jede Seite, die den Befund trägt — sonst hält die Zusicherung
        # nur für die erste.
        betroffen = [
            f["page"]
            for f in sample["reference"]["findings"]
            if f["issue"] == "broken_resource"
        ]
        assert betroffen
        for page in betroffen:
            treffer = re.search(r'<img src="(/[^"]+)"', quelltext(site, page))
            assert treffer, f"{page}: kein absoluter Bildpfad mehr"
            assert not (site / treffer.group(1).lstrip("/")).exists(), page

    def test_keine_seite_kuendigt_ihren_befund_an(self, sample, site) -> None:
        """Ein Befund darf nicht aus dem Seitentext ablesbar sein.

        Ein Satz wie „Weitere Motive werden beim Scrollen geladen." auf
        `scroll.html` machte `infinite_scroll_no_pagination` aus einem Snapshot
        erschließbar, ohne zu scrollen. Ein Modell bekäme den Punkt für eine
        Fähigkeit, die es nicht gezeigt hat.

        Geprüft wird nur der **sichtbare Text**: Skripte und Attribute müssen den
        Mechanismus tragen, sonst gäbe es den Befund nicht — `loading="lazy"` ist
        die Ursache, keine Ankündigung.

        Nicht in der Liste steht „umgezogen" auf `alt.html`. Eine umgezogene Seite
        darf das sagen, und der Befund `client_side_redirect` verlangt keine
        besondere Fähigkeit: Wer die Seite lädt, sieht eine andere Ziel-URL. Die
        Abkürzung, um die es hier geht, ist die, die eine **verlangte Handlung**
        ersetzt.
        """
        verraeter = {
            "infinite_scroll_no_pagination": ("beim Scrollen", "nachgeladen", "Infinite"),
            "interaction_blocked": ("blockiert", "dauert lange", "langsam"),
            "lazy_lcp_image": ("lazy", "verzögert geladen"),
            "layout_shift_after_load": ("verschiebt", "springt"),
            "noindex_after_render": ("noindex", "nicht indexiert"),
            "client_side_redirect": ("weitergeleitet", "Weiterleitung"),
            # Dieselbe Regel für die seitenübergreifenden Befunde: Der Mechanismus
            # gehört ins Markup, in die Serverkonfiguration oder ins Skript —
            # nie in den Fließtext.
            "duplicate_title": ("doppelter Titel", "gleicher Titel", "Duplicate"),
            "canonical_to_noindex": ("noindex", "Canonical"),
            "pagination_404": ("404", "gibt es nicht", "fehlt"),
            "orphan_page_in_sitemap": ("verwaist", "sitemap", "nicht verlinkt"),
            "cloaked_content": ("Cloaking", "Googlebot", "Crawler", "Suchmaschine"),
            "redirect_chain": ("Weiterleitung", "umgezogen", "Kette"),
            # Der Textblock selbst steht im Quelltext und muss es —
            # er IST der Befund. Was nicht dastehen darf, ist ein Hinweis auf
            # seinen Zustand.
            "hidden_by_css": ("unsichtbar", "versteckt", "ausgeblendet",
                              "hidden", "display: none"),
        }
        for finding in sample["reference"]["findings"]:
            for wort in verraeter.get(finding["issue"], ()):
                for quelle in quellen(site, finding["page"]):
                    ohne_skript = re.sub(
                        r"<script.*?</script>", " ", quelle, flags=re.DOTALL
                    )
                    sichtbar = re.sub(r"<[^>]+>", " ", ohne_skript)
                    assert wort.lower() not in sichtbar.lower(), (
                    f"{finding['page']} kündigt {finding['issue']} im Text an "
                    f"({wort!r}) — der Befund ist ohne die geprüfte Fähigkeit "
                    "ablesbar"
                )

    # Wörter, die es in der Prüflandschaft nicht geben darf. Sie gehören in
    # die Tests und in die Doku, nicht in etwas, das über HTTP herauskommt.
    VERRAETER = (
        "Falle", "Fehlalarm", "Schwelle", "Befund", "Referenz", "Benchmark",
        "Prüflandschaft", "Aufgabenversion", "Capture", "Oracle",
        "Precision", "Recall",
    )

    def test_keine_ausgelieferte_datei_verraet_den_benchmark(self, sample, site) -> None:
        """Alles unter `site/` geht über HTTP heraus — auch Kommentare.

        Ein Agent liest auch Kommentare in `sitemap.xml`, HTML und CSS — und
        zitiert sie in seiner Überlegung. Ein Kommentar, der einen Befund beim
        Namen nennt oder die **Schwellen** dokumentiert, gegen die gewertet wird
        (200 ms, CLS 0,1), verrät die Lösung.

        `test_keine_seite_kuendigt_ihren_befund_an` konnte das nicht fangen — er
        prüft nur HTML-Seiten und nur deren **sichtbaren** Text, also gerade
        nicht Kommentare, nicht XML und nicht CSS.

        Die Regel „Kommentare begründen die Entscheidung" gilt für den
        Benchmark-Code. `site/` ist kein Code, sondern **Messgegenstand**.
        """
        katalog = {e["issue"] for e in sample["input"]["catalog"]}
        for datei in sorted(site.rglob("*")):
            if not datei.is_file():
                continue
            try:
                inhalt = datei.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # Binärdateien liest kein Modell als Text
            for issue in katalog:
                assert issue not in inhalt, (
                    f"{datei.name} nennt die Katalog-Id {issue!r} — ein Agent, "
                    "der die Datei liest, bekommt den Befund geschenkt"
                )
            for wort in self.VERRAETER:
                assert not re.search(rf"\b{wort}\b", inhalt, re.IGNORECASE), (
                    f"{datei.name} enthält {wort!r} — das ist Vokabular des "
                    "Benchmarks und gehört nicht in etwas, das ausgeliefert wird"
                )

    def test_kontrollseiten_tragen_die_warnsignale_ohne_fehler(self, site) -> None:
        """`ratgeber.html` ist die schwierigste Seite der Aufgabe.

        Sie trägt lazy geladene Bilder, einen Button und ein Skript — alles
        Muster, die auf anderen Seiten zu Befunden führen. Fehlt eines davon,
        verliert die Aufgabe ihre Falle gegen Agenten, die Muster statt Zustände
        melden, ohne dass eine Zahl sich ändert.
        """
        quelle = quelltext(site, "ratgeber.html")
        assert 'loading="lazy"' in quelle, "keine lazy geladenen Bilder mehr"
        assert "<button" in quelle, "kein Bedienelement mehr"
        assert re.search(r"<script src=\"[^\"]+\" defer>", quelle), (
            "das Skript ist nicht mehr defer — dann ist die Seite kein "
            "Kontrollfall, sondern ein Befund"
        )
        assert quelle.index("<link rel=\"stylesheet\"") < quelle.index("<script src="), (
            "das Skript steht jetzt vor dem CSS"
        )


class TestSeitenuebergreifendeBefunde:
    """Die Befunde, die eine EINZELNE Seite nicht hergibt.

    Warum es sie gibt: Mit lauter Einzelseiten-Befunden ließe sich A01 mit
    einem Messprogramm je Seite lösen, ohne den Browser zu bedienen. Ein
    Harness, das Seite für Seite läuft, findet die folgenden Befunde
    strukturell nicht, egal wie gut es die einzelne Seite vermisst.

    Alle fünf sind im Quelltext entscheidbar. Deshalb steht hier kein
    Render-Lauf, sondern der Nachweis des Mechanismus — dieselbe Rolle wie
    `TestReferenzGegenSite` für die Einzelseiten-Befunde.
    """

    def test_doppelte_titel_sind_genau_die_gemeldeten(self, sample, site) -> None:
        """Und keine zwei anderen Seiten teilen versehentlich einen Titel.

        Die Gegenprobe ist hier die halbe Prüfung: Ein zufällig doppelter Titel
        auf zwei anderen Seiten wäre ein Befund, den die Referenz nicht kennt —
        das Modell läge richtig und verlöre trotzdem Precision.
        """
        nach_titel: dict[str, list[str]] = {}
        for page in sample["input"]["pages"]:
            for quelle in quellen(site, page):
                nach_titel.setdefault(titel(quelle), []).append(page)
        doppelt = {
            t: sorted(set(seiten)) for t, seiten in nach_titel.items() if len(set(seiten)) > 1
        }
        gemeldet = sorted(
            f["page"]
            for f in sample["reference"]["findings"]
            if f["issue"] == "duplicate_title"
        )
        assert list(doppelt.values()) == [gemeldet], doppelt

    def test_canonical_zeigt_auf_eine_noindex_seite(self, sample, site) -> None:
        for finding in sample["reference"]["findings"]:
            if finding["issue"] != "canonical_to_noindex":
                continue
            quelle = quelltext(site, finding["page"])
            ziel = re.search(r'<link rel="canonical" href="[^"]*/([\w.-]+\.html)"', quelle)
            assert ziel, f"{finding['page']}: kein Canonical auf eine Seite dieser Site"
            zielseite = quelltext(site, ziel.group(1))
            assert re.search(r'name="robots" content="[^"]*noindex', zielseite), (
                f"{ziel.group(1)} trägt kein noindex mehr — dann ist der "
                "Canonical in Ordnung und der Befund weg"
            )
            # Und die Kehrseite: Das Ziel muss das noindex STATISCH tragen.
            # Käme es per JS, wäre der Befund vom Render-Zeitpunkt abhängig.
            assert "robots" not in zielseite.split("<body")[1]

    def test_weiterleitungskette_hat_mehr_als_einen_sprung(self, sample, nginx) -> None:
        """Die Kette, und die Abgrenzung zur richtigen Weiterleitung.

        Ein Sprung ist kein Befund (`shop.html`), zwei sind einer. Beide stehen
        in derselben Datei — wer eine davon anfasst, muss die andere ansehen.
        """
        sprünge = dict(
            re.findall(r"location = /([\w.-]+\.html)\s*{\s*return 301 /([\w.-]+\.html)", nginx)
        )
        for finding in sample["reference"]["findings"]:
            if finding["issue"] != "redirect_chain":
                continue
            start = finding["page"]
            assert start in sprünge, f"{start} leitet gar nicht weiter"
            zwischen = sprünge[start]
            assert zwischen in sprünge, (
                f"{start} springt direkt auf {zwischen} — das ist eine richtige "
                "Weiterleitung und kein Kettenbefund"
            )

    def test_paginierung_zeigt_auf_eine_seite_ohne_antwort(self, sample, site, nginx) -> None:
        for finding in sample["reference"]["findings"]:
            if finding["issue"] != "pagination_404":
                continue
            quelle = quelltext(site, finding["page"])
            kaputt = [
                ziel
                for ziel in interne_links(quelle)
                if not (site / ziel).exists() and f"location = /{ziel}" not in nginx
            ]
            assert kaputt, (
                f"{finding['page']}: jeder Paginierungslink antwortet — "
                "dann gibt es den Befund nicht mehr"
            )

    def test_verwaiste_seite_steht_nur_in_der_sitemap(self, sample, site) -> None:
        """Genau eine Seite ohne eingehenden Link — und alle anderen mit.

        Der zweite Teil ist der wichtigere: Ohne ihn wäre jede nicht verlinkte
        Seite ein Befund, den die Referenz nicht kennt. Die Verlinkung von
        `home.html` aus ist deshalb Teil der Prüflandschaft und keine Zierde.
        """
        sitemap = quelltext(site, "sitemap.xml")
        angemeldet = set(re.findall(r"<loc>[^<]*/([\w.-]+\.html)</loc>", sitemap))
        assert angemeldet, "die sitemap.xml meldet keine Seite mehr an"

        verlinkt: set[str] = set()
        for page in sample["input"]["pages"]:
            for quelle in quellen(site, page):
                verlinkt |= {ziel for ziel in interne_links(quelle) if ziel != page}

        verwaist = sorted(angemeldet - verlinkt)
        gemeldet = sorted(
            f["page"]
            for f in sample["reference"]["findings"]
            if f["issue"] == "orphan_page_in_sitemap"
        )
        assert verwaist == gemeldet, (
            f"verwaist laut Site: {verwaist}, laut Referenz: {gemeldet}"
        )

    def test_cloaking_liefert_zwei_verschiedene_fassungen(self, sample, site, nginx) -> None:
        gecloakt = sorted(
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "cloaked_content"
        )
        assert len(gecloakt) >= 2, (
            f"nur {gecloakt} — die Klasse `request` braucht mehr als einen "
            "Befund, sonst ist ihr Recall ein Münzwurf"
        )
        for page in gecloakt:
            stamm = page.removesuffix(".html")
            besucher, bot = (
                quelltext(site, f"{stamm}-{teil}.html") for teil in ("visitor", "bot")
            )
            assert titel(besucher) != titel(bot), f"{page}: gleicher Titel"
            assert len(bot) > len(besucher), (
                f"{page}: die Crawler-Fassung ist nicht die auffällige"
            )
            # Jede gecloakte Adresse braucht eine EXPLIZITE Cache-Vorgabe.
            # Ohne sie entscheidet die heuristische Frische, und die rechnet
            # der Browser aus dem Dateialter — das Item hinge am Auscheckdatum
            # des Repos.
            block = nginx.split(f"location = /{page}")[1].split("}")[0]
            assert re.search(r'add_header\s+Cache-Control\s+"', block), (
                f"{page}: keine explizite Cache-Vorgabe — dann entscheidet "
                "das Dateialter, ob die zweite Anfrage den Server erreicht"
            )
        # Der Schalter selbst: Es muss der User-Agent sein, der entscheidet.
        # Wäre es etwas anderes (eine IP, ein Cookie), wäre der Befund über
        # `emulate` nicht erreichbar und die Aufgabe unlösbar statt schwer.
        assert re.search(r"map\s+\$http_user_agent\s+\$variant", nginx)
        assert re.search(r"googlebot", nginx, re.IGNORECASE)
        gemeldet = {
            f["page"]
            for f in sample["reference"]["findings"]
            if f["issue"] == "cloaked_content"
        }
        for page in gemeldet:
            assert f"location = /{page}" in nginx, page

    def test_der_zweitbesuch_haengt_am_cookie_und_nicht_am_cache(self, sample, site, nginx) -> None:
        """`noindex_on_second_visit` — und die umgekehrte Cache-Vorgabe.

        Die Regel steht in der Serverkonfiguration, nicht im Quelltext: Beim
        ersten Abruf setzt der Server ein Cookie und liefert die indexierbare
        Fassung, beim zweiten steht ein `noindex` im Kopf. Wer die Adresse
        genau einmal ansieht, sieht davon nichts — das ist der Sinn der Klasse
        `second_load`.

        `no-store` ist hier Pflicht und damit genau umgekehrt zur gecloakten
        Seite: Dort IST der Cache die Falle, hier würde er die Bedingung
        ersetzen — der zweite Abruf käme aus dem Cache, das Cookie käme nie zum
        Zug, und gemessen wäre wieder nur die Abrufreihenfolge.
        """
        gemeldet = {
            f["page"]
            for f in sample["reference"]["findings"]
            if f["issue"] == "noindex_on_second_visit"
        }
        assert len(gemeldet) >= 2, (
            f"nur {sorted(gemeldet)} — dieselbe Rechnung wie bei `request`: Mit "
            "einem Befund ist der Recall dieser Klasse ein Münzwurf"
        )
        cookie_namen = set()
        for page in sorted(gemeldet):
            block = nginx.split(f"location = /{page}")[1].split("}")[0]
            cookie = re.search(r'Set-Cookie\s+"(\w+)=', block)
            assert cookie, f"{page}: kein Cookie, kein Zweitbesuch"
            cookie_namen.add(cookie.group(1))
            assert re.search(r'Cache-Control\s+"?no-store', block), (
                f"{page}: ohne no-store entscheidet der Cache statt des Cookies"
            )
            # Die Auslieferung muss an GENAU diesem Cookie hängen — nicht an
            # irgendeinem. Ein `"$cookie" in nginx` als Beleg wäre ab dem
            # zweiten Item wertlos, weil es global irgendwo zutrifft.
            schalter = re.search(r"try_files\s+/\w+(\$\w+)\.html", block)
            assert schalter, f"{page}: die Fassung wird nicht über eine Variable gewählt"
            assert re.search(
                rf"map\s+\$cookie_{cookie.group(1)}\s+{re.escape(schalter.group(1))}",
                nginx,
            ), (
                f"{page}: {schalter.group(1)} kommt nicht aus "
                f"$cookie_{cookie.group(1)} — dann schaltet ein anderes Cookie"
            )
            erst, wieder = (quelltext(site, f"{page[:-5]}-{teil}.html")
                            for teil in ("first", "again"))
            assert "noindex" not in erst, "die erste Fassung trägt schon noindex"
            assert "noindex" in wieder, "die zweite Fassung trägt kein noindex"
        assert len(cookie_namen) == len(gemeldet), (
            f"die Zweitbesuch-Items teilen ein Cookie ({cookie_namen}) — dann "
            "schaltet der Besuch der einen Adresse die andere mit um, und der "
            "Befund hängt an der Abrufreihenfolge statt an der Auslieferung"
        )

    def test_ein_cookie_allein_ist_kein_zweitbesuch(self, sample, site, nginx) -> None:
        """Die Falle zum Zweitbesuch — `galerie.html`.

        Der Server setzt dort ein Cookie und liefert trotzdem immer dieselbe
        Datei aus. Wer aus „hier kommt ein Set-Cookie" auf „sitzungsabhängige
        Auslieferung" schließt, ohne die zweite Antwort mit der ersten zu
        vergleichen, meldet hier falsch. Ein Cookie ist ein Hinweis, kein Beleg.
        """
        falle = "galerie.html"
        block = nginx.split(f"location = /{falle}")[1].split("}")[0]
        assert "Set-Cookie" in block, f"{falle} setzt kein Cookie mehr"
        # Dieselbe Datei für beide Fälle — keine Variable im try_files.
        assert re.search(rf"try_files\s+/{falle}\s+=404", block), (
            f"{falle} liefert jetzt Fassungen aus — dann ist die Falle ein Befund"
        )
        assert "$" not in block.split("try_files")[1], (
            f"{falle}: im try_files steht eine Variable"
        )
        assert falle not in {
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "noindex_on_second_visit"
        }

    def test_die_cache_falle_ist_deterministisch(self, nginx) -> None:
        """Die gecloakte Seite ist absichtlich cachebar — aber berechenbar.

        Die Falle selbst ist gewollt: Wer die Seite erst als Besucher holt und
        danach als Crawler, bekommt aus dem Browser-Cache die alte Fassung. Das
        ist der Fehler, den ein technischer SEO in der Praxis macht, und damit
        ein Befund über das Modell — kein Messartefakt.

        Was NICHT sein darf, ist die heuristische Frische: Ohne expliziten
        `Cache-Control`-Header rechnet der Browser sie aus dem Alter der Datei,
        und dann hängt die Falle am Auscheckdatum des Repos: einige Tage alte
        Dateien werden gecacht, gerade gespeicherte nicht. Deshalb eine feste Frist, mindestens so lang
        wie ein Lauf höchstens dauert.

        Ein `Vary: User-Agent` würde die Falle aufheben; es darf deshalb nicht
        dastehen.
        """
        block = nginx.split("location = /preise.html")[1].split("}")[0]
        frist = re.search(r'add_header\s+Cache-Control\s+"?max-age=(\d+)', block)
        assert frist, (
            "keine feste Cache-Frist auf der gecloakten Seite — dann "
            "entscheidet das Dateialter, ob die Falle zuschnappt"
        )
        assert int(frist.group(1)) >= TIME_LIMIT, (
            f"Frist {frist.group(1)} s ist kürzer als ein Lauf dauern darf "
            f"(TIME_LIMIT {TIME_LIMIT} s) — dann hängt die Falle an der Uhr"
        )
        assert "Vary" not in block, (
            "Vary: User-Agent hebt die Falle auf — dann prüft das Item nur "
            "noch, ob der Agent den User-Agent wechselt"
        )

    @pytest.mark.parametrize("seite", ["tipps.html", "archiv.html", "galerie.html"])
    def test_die_cookies_ueberdauern_einen_lauf(self, nginx, seite) -> None:
        """Ein Cookie, das mitten im Lauf abläuft, macht den Zweitbesuch zum Erstbesuch.

        Wer `tipps.html` früh und erst eine Stunde später wieder abruft, sähe
        sonst zweimal die indexierbare Fassung, und der Befund hinge an der
        Uhr statt an der Gewohnheit des Agenten. Bei `galerie.html` gilt
        dasselbe, damit sich die Falle genauso verhält wie die echten
        Zweitbesuche.
        """
        block = nginx.split(f"location = /{seite}")[1].split("}")[0]
        frist = re.search(r"Set-Cookie\s+\"[^\"]*Max-Age=(\d+)", block)
        assert frist, f"{seite}: Cookie ohne feste Lebensdauer"
        assert int(frist.group(1)) >= TIME_LIMIT, (
            f"{seite}: Cookie gilt {frist.group(1)} s, ein Lauf darf "
            f"{TIME_LIMIT} s dauern"
        )


class TestVerstecktePerCss:
    """Der Mechanismus hinter `hidden_by_css` — verteilt auf drei Stellen.

    Das Item steht und fällt damit, dass die Unsichtbarkeit **nicht** aus einer
    Datei ablesbar ist. Sie entsteht aus drei Teilen, die einzeln harmlos sind:

      1. `style.css`   `.rand` ist ausgeblendet, seine Kinder bringen die
                       Sichtbarkeit selbst mit — für sich gelesen also sichtbar.
      2. `layout.css`  eine Media Query gibt dem Textblock `visibility: inherit`
                       zurück; er erbt damit wieder die Ausblendung des
                       Containers. Für sich gelesen sieht das nach einem
                       Zurücksetzen aus.
      3. Die Seite     `zubehoer.html` trägt DIESELBE Klasse an DERSELBEN
                       Stelle und ist sichtbar, weil der Container eine zweite
                       Klasse hat. Wer aus dem Markup schließt, meldet beide.

    Geprüft wird hier der Mechanismus, nicht der gerenderte Zustand — den
    entscheidet `reference/audit.py` aus dem Capture. Diese Klasse ist der
    Wachhund darüber, dass niemand einen der drei Teile herausnimmt, ohne dass
    eine Zahl sich ändert.
    """

    def test_die_ausblendung_steht_in_keiner_einzelnen_datei(
        self, sample, site, stylesheets
    ) -> None:
        betroffen = [
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "hidden_by_css"
        ]
        assert betroffen, "kein hidden_by_css mehr in der Referenz"
        for page in betroffen:
            quelle = quelltext(site, page)
            # Im Markup steht nur die Klasse — kein Stil, der etwas ausblendet.
            assert 'class="einschub"' in quelle, page
            assert "<style" not in quelle, f"{page} bringt eigenes CSS mit"
            assert not re.search(r"style\s*=", quelle), f"{page} hat Inline-Stil"
            for name in ("style.css", "layout.css"):
                assert f'href="{name}"' in quelle, f"{page} lädt {name} nicht"
        # Teil 1 und Teil 2, jeder in seiner Datei.
        assert re.search(r"\.rand\s*{[^}]*visibility:\s*hidden", stylesheets["style.css"]), (
            "style.css blendet die Randspalte nicht mehr aus"
        )
        assert re.search(r"\.rand\s*>\s*\*\s*{[^}]*visibility:\s*visible",
                         stylesheets["style.css"]), (
            "ohne die Ausnahme für die Kinder ist die Ausblendung schon in "
            "style.css allein ablesbar"
        )
        assert re.search(r"\.rand\s*>\s*\.einschub\s*{[^}]*visibility:\s*inherit",
                         stylesheets["layout.css"]), (
            "layout.css gibt die Ausblendung nicht mehr an den Textblock weiter"
        )

    def test_die_media_query_greift_beim_messviewport(self, stylesheets) -> None:
        """Und zwar gerechnet, nicht geglaubt.

        Die Grenze steht in `em`. In einer Media Query ist ein `em` immer 16
        Pixel — die Schriftgröße der Wurzel ändert daran nichts. Wer das
        verwechselt, hält die Regel für unwirksam; genau darum steht sie so da.
        """
        treffer = re.search(r"@media\s*\(min-width:\s*([\d.]+)em\)",
                            stylesheets["layout.css"])
        assert treffer, "die Media Query ist weg oder rechnet nicht mehr in em"
        grenze = float(treffer.group(1)) * 16
        assert grenze <= VIEWPORT_WIDTH, (
            f"die Query greift erst ab {grenze:.0f} px, gemessen wird bei "
            f"{VIEWPORT_WIDTH} px — dann ist der Textblock sichtbar"
        )
        # Und die Gegenrichtung: Bei schmalem Fenster darf sie NICHT greifen,
        # sonst ist der Befund keine Eigenschaft des Messviewports mehr.
        assert grenze > 375, f"die Query greift schon bei {grenze:.0f} px"

    def test_die_zwillingsseite_traegt_dieselbe_klasse_und_ist_sichtbar(
        self, sample, site, stylesheets
    ) -> None:
        """Die Falle — und der eigentliche Ertrag des Items.

        `zubehoer.html` sieht im Quelltext aus wie `technik.html`: dieselben
        Stylesheets, dieselbe Klasse am selben Element. Der Unterschied ist
        eine zweite Klasse am Container. Wer den Befund aus dem Markup oder aus
        einer einzelnen CSS-Regel erschließt, meldet hier falsch.
        """
        versteckt = {
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "hidden_by_css"
        }
        zwilling = "zubehoer.html"
        assert zwilling not in versteckt, f"{zwilling} ist keine Falle mehr"
        quelle = quelltext(site, zwilling)
        assert 'class="einschub"' in quelle, "die Falle trägt die Klasse nicht mehr"
        assert re.search(r'class="rand offen"', quelle), (
            "ohne die zweite Klasse am Container ist die Falle selbst ein Befund"
        )
        assert re.search(r"\.rand\.offen\s*{[^}]*visibility:\s*visible",
                         stylesheets["style.css"]), (
            "die Ausnahme fehlt in style.css — dann ist die Falle ein Befund"
        )
        # Und die versteckte Seite darf sie NICHT tragen.
        for page in versteckt:
            assert "rand offen" not in quelltext(site, page), page

    def test_der_versteckte_block_traegt_genug_text(self, sample, site) -> None:
        # Die Regel greift ab 60 Zeichen. Ein Block knapp darüber machte die
        # Referenz von der Formulierung abhängig statt von der Kaskade.
        from german_seo_bench.reference.audit import HIDDEN_MIN_TEXT

        for finding in sample["reference"]["findings"]:
            if finding["issue"] != "hidden_by_css":
                continue
            quelle = quelltext(site, finding["page"])
            block = re.search(r'<p class="einschub">(.*?)</p>', quelle, re.DOTALL)
            assert block, f"{finding['page']}: kein Textblock mehr"
            laenge = len(" ".join(block.group(1).split()))
            assert laenge > 4 * HIDDEN_MIN_TEXT, (
                f"{laenge} Zeichen liegen zu nah an der Schwelle {HIDDEN_MIN_TEXT}"
            )


class TestFehlalarmFallen:
    """Zustände, die wie ein Befund aussehen und keiner sind.

    Sie sind der Grund, warum Precision überhaupt etwas kosten kann: Ohne sie
    läge sie bei jedem halbwegs sorgfältigen Modell auf 1.000 und mäße damit
    nichts. Jede Falle steht auf einer Seite, die auch echte Befunde
    trägt — eine Falle auf einer sonst sauberen Seite bestraft nur den, der die
    Seite überhaupt ansieht.

    Für die zwei Fallen mit einer Zahl (Layout und Klick) prüft der Test die
    SCHRANKE im Quelltext, nicht den gerenderten Wert: Beide sind so gebaut,
    dass die Schwelle rechnerisch nicht erreicht werden kann.
    """

    def test_layoutverschiebung_bleibt_unter_der_schwelle(self, sample, site) -> None:
        """CLS ist Wirkungsanteil mal Wegstrecke — und die Wegstrecke ist hier klein.

        Geteilt wird durch die größere Viewport-Seite (1280). Ein Einschub von
        88 Pixeln ergibt 88/1280 = 0,069; der Wirkungsanteil kann höchstens 1
        werden. Damit liegt CLS nachweislich unter 0,07 und die Schwelle bei
        0,1 — die Obergrenze ist gerechnet, nicht gemessen, und gilt deshalb
        auf jeder Maschine. Zwei Einschübe würden sich addieren: Deshalb prüft
        der Test auch, dass es genau einer ist.
        """
        quelle = quelltext(site, "sale.html")
        assert "sale.html" not in {
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "layout_shift_after_load"
        }
        hoehen = [int(h) for h in re.findall(r"height:(\d+)px", quelle)]
        raender = [int(r) for r in re.findall(r"padding:(\d+)px", quelle)]
        assert len(quelle.split("innerHTML")) == 2, "mehr als ein Einschub"
        assert hoehen and sum(hoehen) + 2 * sum(raender) <= 100, (hoehen, raender)

    def test_klick_bleibt_unter_der_schwelle(self, sample, site) -> None:
        quelle = quelltext(site, "liste.html")
        assert "liste.html" not in {
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "interaction_blocked"
        }
        dauer = re.search(r"Date\.now\(\) \+ (\d+)", quelle)
        assert dauer, "liste.html hat keine blockierende Schleife mehr"
        # Deutlich unter 200 ms, aber lang genug, dass ein ungenaues Verfahren
        # sie für lang hält. Genau da soll die Falle zuschnappen.
        assert 100 <= int(dauer.group(1)) < 200, dauer.group(1)

    def test_lazy_bild_liegt_unterhalb_des_ersten_viewports(self, sample, site) -> None:
        """Richtig eingesetztes Lazy Loading — kein Befund.

        Das lazy geladene Bild steht hinter einem 700 Pixel hohen Block, das
        Bild ganz oben trägt kein `loading="lazy"`. Wer `lazy_lcp_image` am
        Attribut festmacht statt an der Geometrie, meldet hier falsch.
        """
        quelle = quelltext(site, "poster.html")
        assert "poster.html" not in {
            f["page"] for f in sample["reference"]["findings"]
            if f["issue"] == "lazy_lcp_image"
        }
        erstes_bild = re.search(r"<img[^>]*>", quelle)
        assert erstes_bild and "lazy" not in erstes_bild.group(0)
        block = re.search(r"min-height:(\d+)px", quelle)
        assert block and int(block.group(1)) >= 600, "der Abstand zum Fold fehlt"
        assert quelle.index("min-height") < quelle.rindex('loading="lazy"')

    def test_das_grosse_bild_nach_attribut_ist_nicht_das_lazy_geladene(
        self, sample, site, stylesheets
    ) -> None:
        """Der zweite `lazy_lcp_image`-Befund — entschieden durch CSS.

        Auf `zubehoer.html` trägt das lazy geladene Bild die KLEINEN Maße im
        Markup (300x200) und wird per CSS auf 900x600 aufgezogen; das nach
        Attribut größte (1200x600) ist nicht lazy und wird auf 220x110
        geschrumpft. Jede Heuristik über die Attribute findet hier nichts und
        liegt damit falsch — nur die gerenderte Geometrie entscheidet.

        Zugleich ist das die Gegenrichtung zur Falle auf `poster.html`: Dort
        ist ein lazy geladenes Bild richtig eingesetzt, hier sieht ein
        richtiges falsch aus.
        """
        seite = "zubehoer.html"
        assert (seite, "lazy_lcp_image") in {
            (f["page"], f["issue"]) for f in sample["reference"]["findings"]
        }
        quelle = quelltext(site, seite)
        bilder = [
            {
                "lazy": "lazy" in tag,
                "attr": int(re.search(r'width="(\d+)"', tag).group(1))
                * int(re.search(r'height="(\d+)"', tag).group(1)),
                "klasse": re.search(r'class="([\w-]+)"', tag).group(1),
            }
            for tag in re.findall(r"<img[^>]*>", quelle, re.DOTALL)
        ]
        assert len(bilder) == 2, bilder
        nach_attribut = max(bilder, key=lambda b: b["attr"])
        assert not nach_attribut["lazy"], (
            "das nach Attribut größte Bild ist lazy — dann findet eine "
            "Attribut-Heuristik den Befund und das Item misst nichts"
        )
        lazy = next(b for b in bilder if b["lazy"])
        assert lazy["attr"] < nach_attribut["attr"], lazy

        # Und die CSS-Seite: Die Klassen müssen die Reihenfolge umdrehen, und
        # zwar mit genug Abstand zur Mindestfläche des LCP-Kandidaten.
        from german_seo_bench.reference.audit import LCP_MIN_AREA

        flaechen = {}
        for klasse in (lazy["klasse"], nach_attribut["klasse"]):
            regel = re.search(
                rf"\.{klasse}\s*{{[^}}]*width:\s*(\d+)px[^}}]*height:\s*(\d+)px",
                stylesheets["layout.css"],
            )
            assert regel, f"{klasse} hat keine feste gerenderte Größe in layout.css"
            flaechen[klasse] = int(regel.group(1)) * int(regel.group(2))
        assert flaechen[lazy["klasse"]] > 4 * LCP_MIN_AREA, flaechen
        assert flaechen[nach_attribut["klasse"]] < LCP_MIN_AREA / 1.5, (
            f"{flaechen} — das geschrumpfte Bild liegt zu nah an der "
            f"Mindestfläche {LCP_MIN_AREA}"
        )

    def test_richtiges_noindex_und_richtiger_canonical(self, sample, site) -> None:
        quelle = quelltext(site, "filter.html")
        assert 'content="noindex' in quelle and "canonical" in quelle
        # Beides STATISCH. Käme es per JS, wären es zwei echte Befunde.
        kopf = quelle.split("</head>")[0]
        assert 'content="noindex' in kopf and 'rel="canonical"' in kopf
        assert "<script" not in quelle
        assert "filter.html" not in {f["page"] for f in sample["reference"]["findings"]}

    def test_einzelne_weiterleitung_ist_kein_befund(self, sample, nginx) -> None:
        sprünge = dict(
            re.findall(r"location = /([\w.-]+\.html)\s*{\s*return 301 /([\w.-]+\.html)", nginx)
        )
        assert "shop.html" in sprünge
        assert sprünge["shop.html"] not in sprünge, "aus der Falle ist eine Kette geworden"
        assert "shop.html" not in {f["page"] for f in sample["reference"]["findings"]}


class TestToterCodeAlsFalle:
    """Fünf Fallen, die den billigsten Weg bestrafen: das Quelltextlesen.

    Ein starkes Modell findet fast alles, was in den ausgelieferten Bytes
    steht. Punkte kostet es dann vor allem durch **Falschmeldungen**, etwa
    wenn es die Eigenschaften der Zielseite der angefragten Adresse zuschreibt.

    Daraus folgt die Bauform dieser Klasse: Ein starkes Modell übersieht
    kaum etwas, aber es lässt sich **täuschen**. Jede Falle hier ist ein
    Codepfad, der im Quelltext genau wie ein Befund aussieht und zur Laufzeit
    nichts tut. Wer das gerenderte DOM und das Netzwerkprotokoll ansieht, sieht
    nichts und meldet nichts — wer den Quelltext nach Mustern durchsucht,
    sammelt fünf Falschmeldungen.

    Geprüft wird beides: dass der Marker dasteht (sonst ist die Falle keine) und
    dass die Bedingung nachweislich nicht greifen kann. Bei drei der fünf ist
    dieser Nachweis im Quelltext selbst zu führen — es gibt das Element nicht,
    es gibt die Adresse mit Parameter nicht, es gibt die Datei nicht.
    """

    # (Seite, Befund, der Marker, der ein Quelltextleser findet)
    FALLEN = (
        ("presse.html", "client_side_redirect", r"window\.location\.replace\("),
        ("galerie.html", "title_changed_by_js", r"document\.title\s*="),
        ("kategorie.html", "broken_resource", r"'/img/[\w.-]+'"),
        ("produkt.html", "noindex_after_render", r"content\s*=\s*'noindex"),
        ("scroll.html", "script_loads_script", r"\.src\s*=\s*messung\.skript"),
    )

    def test_der_marker_steht_da_und_der_befund_nicht(self, sample, site) -> None:
        befunde = {(f["page"], f["issue"]) for f in sample["reference"]["findings"]}
        for page, issue, marker in self.FALLEN:
            quelle = quelltext(site, page)
            assert re.search(marker, quelle), (
                f"{page}: der Marker für {issue} ist weg — dann ist die Falle "
                "keine und ein Quelltextleser zahlt nichts"
            )
            assert (page, issue) not in befunde, (
                f"{page} trägt {issue} jetzt wirklich — dann ist aus der Falle "
                "ein Befund geworden und die Referenz sagt etwas anderes als "
                "dieser Test"
            )

    def test_jede_falle_sitzt_auf_einer_seite_mit_echtem_befund(
        self, sample, site
    ) -> None:
        """Sonst bestraft sie nur den, der die Seite überhaupt ansieht.

        Dieselbe Regel wie bei den übrigen Fehlalarm-Fallen: Eine
        Falle auf einer sonst sauberen Seite belohnt das Wegsehen.
        """
        betroffen = {f["page"] for f in sample["reference"]["findings"]}
        for page, _, _ in self.FALLEN:
            assert page in betroffen, f"{page} trägt gar keinen echten Befund"

    def test_das_platzhalterbild_hat_kein_element(self, site) -> None:
        """`kategorie.html`: die Zuweisung läuft über eine leere Knotenliste.

        Der Nachweis steht im Quelltext — es gibt kein `img.motiv` auf der
        Seite, also wird der Platzhalter nie gesetzt und nie angefordert. Käme
        ein solches Bild dazu, wäre aus der Falle ein `broken_resource`.
        """
        quelle = quelltext(site, "kategorie.html")
        assert "querySelectorAll('img.motiv')" in quelle
        assert 'class="motiv"' not in quelle, (
            "es gibt jetzt ein img.motiv — dann wird der Platzhalter angefordert"
        )
        pfad = re.search(r"var platzhalter = '(/[^']+)'", quelle)
        assert pfad and not (site / pfad.group(1).lstrip("/")).exists(), (
            "der Platzhalter existiert — dann wäre er nicht einmal kaputt"
        )

    def test_die_vorschau_adresse_gibt_es_nicht(self, sample, site) -> None:
        """`produkt.html`: das `noindex` hängt an einem Query-Parameter.

        Keine Adresse der Aufgabe trägt einen — also kann der Zweig nicht
        laufen. Der Nachweis geht über die Seitenliste und über alle
        Verlinkungen der Prüflandschaft.
        """
        quelle = quelltext(site, "produkt.html")
        treffer = re.search(r"get\('(\w+)'\) === '1'", quelle)
        assert treffer, "produkt.html prüft keinen Parameter mehr"
        parameter = treffer.group(1)
        assert not any("?" in page for page in sample["input"]["pages"])
        for page in sample["input"]["pages"]:
            for andere in quellen(site, page):
                assert f"?{parameter}" not in andere, (
                    f"{page} verlinkt die Vorschau-Adresse — dann ist die "
                    "Falle ein echter Befund"
                )

    def test_das_messkript_gibt_es_nicht_und_der_schalter_steht_aus(
        self, site
    ) -> None:
        """`scroll.html`: ein Feature-Schalter, der aus ist.

        Zwei Nachweise, weil einer allein zu wenig wäre: Der Schalter steht auf
        `false`, UND die genannte Datei liegt nicht in `site/`. Stünde sie da
        und der Schalter an, wäre es ein `script_loads_script`; stünde sie nicht
        da und der Schalter an, ein `broken_resource`.
        """
        quelle = quelltext(site, "scroll.html")
        treffer = re.search(r"\{ skript: '([\w.-]+)', aktiv: (\w+) \}", quelle)
        assert treffer, "der Schalter in scroll.html ist umgebaut"
        assert treffer.group(2) == "false", "der Schalter steht an"
        assert not (site / treffer.group(1)).exists(), (
            f"{treffer.group(1)} liegt jetzt in site/"
        )


class TestScoreRechnung:
    """Die Rechnung hinter dem Score — Klasse für Klasse, mit Gegenprobe.

    Sie steht hier und nicht nur im Oracle-Lauf, weil ihr Fehlerfall im
    Messlauf strukturell unsichtbar ist: Dort hat jede Klasse Befunde. Der
    Fehler zeigt sich erst auf einem Ausschnitt — und genau dafür benutzt man
    die Zahl beim Nachsehen.
    """

    VOLL = {
        "source": {"found": 18, "total": 18},
        "network": {"found": 5, "total": 5},
        "render": {"found": 4, "total": 4},
        "request": {"found": 2, "total": 2},
        "second_load": {"found": 2, "total": 2},
    }
    KEINE_FP = dict.fromkeys(CAPABILITY_CLASSES, 0)

    def test_alles_gefunden_ergibt_eins(self) -> None:
        wert, je_klasse = capability_f1(self.VOLL, self.KEINE_FP)
        assert wert == 1.0
        assert all(v == 1.0 for v in je_klasse.values())

    def test_eine_verfehlte_klasse_kostet_ein_fuenftel(self) -> None:
        """Der Kern dieser Rechnung: Eine vollständig verfehlte Klasse kostet
        ein Fünftel, egal wie wenige Befunde sie hat. In Micro-F1 verschwände
        sie im Rauschen."""
        by = {**self.VOLL, "second_load": {"found": 0, "total": 2}}
        wert, je_klasse = capability_f1(by, self.KEINE_FP)
        assert je_klasse["second_load"] == 0.0
        assert wert == pytest.approx(4 / 5)

    def test_falschmeldungen_zaehlen_gegen_ihre_klasse(self) -> None:
        """Sonst wäre der Score ein reines Recall-Maß — und ein Agent, der
        alles meldet, bekäme eine Eins."""
        fp = {**self.KEINE_FP, "source": 18}
        wert, je_klasse = capability_f1(self.VOLL, fp)
        # 18 richtig, 18 falsch: Precision 0.5, Recall 1.0, F1 0.667.
        assert je_klasse["source"] == pytest.approx(2 / 3)
        assert wert < 1.0

    def test_eine_klasse_ohne_befunde_zaehlt_nicht_mit(self) -> None:
        """Eine leere Klasse ist keine verfehlte Klasse.

        Als 0.0 gezählt, drückte sie den Score auf einem Ausschnitt der Aufgabe
        (nur `source` und `network`), obwohl der Agent alles gefunden hat, was
        dort zu finden war. „Kommt in der
        Referenz nicht vor" ist keine verfehlte Fähigkeit, sondern keine
        Messung.
        """
        ausschnitt = {
            "source": {"found": 6, "total": 6},
            "network": {"found": 1, "total": 1},
        }
        wert, je_klasse = capability_f1(ausschnitt, self.KEINE_FP)
        assert wert == 1.0, wert
        for name in ("render", "request", "second_load"):
            assert je_klasse[name] is None, name

    def test_ohne_jeden_befund_ist_die_zahl_null(self) -> None:
        # Kein Sonderfall mit Division durch null: ein Capture ohne Referenz
        # ergibt 0.0 und keine Ausnahme.
        wert, je_klasse = capability_f1({}, self.KEINE_FP)
        assert wert == 0.0
        assert all(v is None for v in je_klasse.values())


class TestFaehigkeitsklassen:
    """Was genügt, um einen Befund zu holen — je Fehlertyp eingetragen.

    Warum es diese Klassen gibt: Zwei Modelle können denselben Gesamtwert auf
    völlig verschiedene Weise erreichen — das eine bedient die Seiten
    (navigiert, klickt, scrollt), das andere liest sie in einer
    `fetch`-Schleife und rendert nur für Geometrie. Eine Zahl, die Lesen und
    Bedienen vermischt, verdeckt genau den Unterschied, um den es geht.

    Die Klasse benennt die **billigste bekannte** Strategie, nicht das Thema
    des Items. Deshalb steht `interaction_blocked` auf `source`: Es heißt
    Interaktion, wurde aber bei `clicked` 0 korrekt gemeldet.
    """

    def test_jeder_fehlertyp_hat_genau_eine_klasse(self, sample) -> None:
        zuordnung = sample["reference"]["capability_by_issue"]
        ids = [e["issue"] for e in sample["input"]["catalog"]]
        assert sorted(zuordnung) == sorted(ids), set(ids) ^ set(zuordnung)

    def test_klassen_stimmen_mit_dem_datensatz(self, sample) -> None:
        """Keine Spalte ohne Befunde, kein Befund ohne Spalte.

        Eine Klasse, die im Scorer steht, aber keine Befunde hat, zeigte eine
        Null oder eine Eins, die niemand verdient hat. Eine Klasse in den
        Daten, die im Scorer fehlt, verschwände aus der Auswertung.
        """
        zuordnung = sample["reference"]["capability_by_issue"]
        benutzt = {zuordnung[f["issue"]] for f in sample["reference"]["findings"]}
        assert benutzt == set(CAPABILITY_CLASSES), (
            f"Daten: {sorted(benutzt)}, Scorer: {sorted(CAPABILITY_CLASSES)}"
        )

    def test_die_klasse_steht_nicht_im_prompt(self, dataset, sample) -> None:
        """Sonst verrät die Aufgabe den Weg, den sie messen will.

        Dieselbe Schranke wie bei der Paarung Seite/Problem: Der Katalog darf
        in den Prompt, die Einordnung nicht. Stünde dort „cloaked_content:
        request", wüsste das Modell, dass es die Anfragebedingung ändern muss.
        """
        text = dataset[0].input
        assert "capability" not in text
        zuordnung = sample["reference"]["capability_by_issue"]
        for zeile in text.splitlines():
            genannt = [i for i in zuordnung if i in zeile]
            for issue in genannt:
                assert zuordnung[issue] not in zeile, (
                    f"Prompt-Zeile nennt {issue} und seine Klasse zugleich: {zeile!r}"
                )


class TestVertrag:
    def test_prompt_nennt_jede_seite_mit_url(self, dataset, sample) -> None:
        text = dataset[0].input
        for page in sample["input"]["pages"]:
            assert f"{SITE_URL}{page}" in text, page

    def test_prompt_nennt_den_katalog(self, dataset, sample) -> None:
        text = dataset[0].input
        for entry in sample["input"]["catalog"]:
            assert entry["issue"] in text, entry["issue"]

    def test_prompt_verraet_die_zuordnung_nicht(self, dataset, sample) -> None:
        """Die Schranke, an der die Aufgabe hängt.

        Seiten und Katalog stehen beide im Prompt — sie müssen. Was nicht
        drinstehen darf, ist die PAARUNG: Sobald eine Zeile eine Seite und eine
        Problem-Id zugleich nennt, ist die Antwort mitgeliefert. Geprüft wird
        deshalb zeilenweise und nicht auf Enthaltensein.
        """
        pages = set(sample["input"]["pages"])
        issues = {entry["issue"] for entry in sample["input"]["catalog"]}
        for line in dataset[0].input.splitlines():
            genannte_seiten = [p for p in pages if p in line]
            genannte_probleme = [i for i in issues if i in line]
            assert not (genannte_seiten and genannte_probleme), (
                f"Prompt-Zeile nennt Seite und Problem zugleich: {line!r}"
            )

    def test_prompt_nennt_den_viewport(self, dataset) -> None:
        # Die Wertung ist an diesen Viewport gebunden (CLS, LCP-Kandidat). Ohne
        # die Angabe im Prompt wäre `layout_shift_after_load` ein Münzwurf.
        assert f"{VIEWPORT_WIDTH} x {VIEWPORT_HEIGHT}" in dataset[0].input

    def test_kein_target(self, dataset) -> None:
        # A01 vergleicht nicht gegen eine richtige Antwort, sondern gegen eine
        # Paarmenge. Ein gesetztes Target würde im Log eine Wertung suggerieren,
        # die es nicht gibt.
        assert not dataset[0].target


class TestTopologie:
    """Was die Container hergeben müssen, damit Prompt und Wertung stimmen."""

    @pytest.fixture(scope="class")
    @classmethod
    def compose(cls) -> dict:
        return yaml.safe_load(Path(data_path(COMPOSE)).read_text(encoding="utf-8"))

    def test_default_dienst_existiert(self, compose) -> None:
        # Inspect schickt Werkzeugaufrufe in den Dienst `default`. Fehlt er,
        # bricht der Lauf mit einer Meldung ab, die nach einem Fehler in der
        # Aufgabe aussieht.
        assert "default" in compose["services"]

    def test_prompt_url_loest_im_netz_auf(self, compose) -> None:
        """Der Name aus dem Prompt muss im Containernetz auflösen.

        Die Prüflandschaft läuft unter `www.example.net` — reserviert nach
        RFC 2606 und damit das, was ein Mensch im Bericht lesen soll. Der Dienst
        heißt anders und trägt den Namen als **Netzwerk-Alias**. Geprüft wird
        deshalb der Alias und nicht der Dienstname: Läuft beides auseinander, navigiert der Agent ins Nichts,
        und die Null sähe wie ein unfähiges Modell aus.
        """
        host = SITE_URL.removeprefix("http://").rstrip("/")
        # `networks` darf in compose zwei Formen haben: eine Liste von Netzen
        # (so steht der Agent-Container da) oder eine Abbildung Netz →
        # Einstellungen. Nur die zweite kann Aliasse tragen.
        aliasse: set[str] = set()
        for dienst in compose["services"].values():
            netze = dienst.get("networks") or {}
            if not isinstance(netze, dict):
                continue
            for netz in netze.values():
                if isinstance(netz, dict):
                    aliasse |= set(netz.get("aliases") or [])
        assert host in aliasse, (
            f"{host} ist in compose.yaml kein Netzwerk-Alias — der Agent "
            f"bekommt die Adresse aus dem Prompt und findet sie nicht. "
            f"Vorhanden: {sorted(aliasse)}"
        )

    def test_die_eigene_domain_ist_eine_reservierte(self) -> None:
        """Was im Kundenbericht steht, darf niemandem gehören.

        Ein erfundener Name wie `testsite` wäre zweimal falsch: Er sieht in einem
        Bericht aus wie die Domain eines echten Auftraggebers, und er könnte
        eines Tages wirklich jemandem gehören. `example.net` und `example.com`
        sind nach RFC 2606 für genau diesen Zweck reserviert und lösen nirgends
        auf einen echten Host auf — und zwei verschiedene davon bilden den
        Unterschied „eigene Site gegen fremde Domain" ab, an dem die Canonicals
        hängen.
        """
        host = SITE_URL.removeprefix("http://").rstrip("/")
        assert host.endswith(".example.net") or host == "example.net", host
        # Die fremde Domain der Canonicals muss eine ANDERE sein, sonst zeigt
        # `canonical_only_in_render` auf die eigene Site und der Befund ändert
        # seine Bedeutung.
        fremd = "example.com"
        assert fremd not in host, f"{host} und die Fremddomain sind dieselbe"

    def test_pruefungslandschaft_wird_nur_lesend_gemountet(self, compose) -> None:
        # Zwei Mounts: die Seiten und die Auslieferungsregeln.
        # Beide read-only — ein Agent, der die Konfiguration ändern könnte,
        # könnte sich das Cloaking wegschalten.
        mounts = compose["services"]["testsite"]["volumes"]
        assert mounts == [
            "./site:/usr/share/nginx/html:ro",
            "./testsite.nginx.conf:/etc/nginx/conf.d/default.conf:ro",
        ], mounts

    def test_kein_mount_im_agent_container(self, compose) -> None:
        """Die eigentliche Zusicherung dieser Aufgabe.

        Referenz, Scorer und Aufgabencode dürfen im Agent-Container nicht
        erreichbar sein. Das ist keine Eigenschaft des Python-Codes, sondern
        dieser Datei — und deshalb wird sie hier geprüft und nicht geglaubt.
        """
        assert "volumes" not in compose["services"]["default"]

    def test_netz_ist_abgeschlossen(self, compose) -> None:
        # Kein Weg nach draußen: Ein Agent, der die Seiten nicht rendern kann,
        # soll nicht ausweichen und etwas nachschlagen können.
        netze = compose["networks"]
        assert netze, "kein eigenes Netz — dann hängen die Container am Standardnetz"
        for name, spec in netze.items():
            assert spec.get("internal") is True, f"Netz {name} ist nicht internal"
        for name, service in compose["services"].items():
            assert set(service["networks"]) <= set(netze), name

    def test_agent_image_bringt_browser_und_mcp_server_mit(self) -> None:
        """Beides festgenagelt im Image, nichts per `@latest` zur Laufzeit.

        Sonst entscheidet der Tag, was gemessen wurde, und zwei Läufe im Abstand
        einer Woche sind nicht vergleichbar.
        """
        dockerfile = Path(data_path("a01_browser_audit/agent.Dockerfile")).read_text(
            encoding="utf-8"
        )
        assert "chromium" in dockerfile
        assert re.search(r"chrome-devtools-mcp@\$\{CHROME_DEVTOOLS_MCP_VERSION\}", dockerfile)
        assert re.search(r"CHROME_DEVTOOLS_MCP_VERSION=\d+\.\d+\.\d+", dockerfile), (
            "keine festgenagelte Version"
        )
        # Nur die Befehlszeilen, nicht die Kommentare: Der Kommentar oben
        # BEGRÜNDET, warum es kein `@latest` gibt, und nennt es dabei.
        befehle = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )
        assert "@latest" not in befehle

    def test_image_laeuft_als_root(self) -> None:
        """Kein `USER`-Wechsel im Dockerfile — eine Bedingung von Inspect.

        Inspect legt seine Sandbox-Werkzeuge in ein root-eigenes Verzeichnis mit
        Modus 0700 und ruft sie danach als **Vorgabebenutzer** des Images auf. Ein
        `USER node` bricht damit jeden Werkzeugaufruf mit Exit 126,
        `Permission denied` — und die Meldung zeigt auf `/var/tmp`, also genau
        nicht auf die Ursache.
        """
        dockerfile = Path(data_path("a01_browser_audit/agent.Dockerfile")).read_text(
            encoding="utf-8"
        )
        benutzer = [
            line
            for line in dockerfile.splitlines()
            if line.strip().upper().startswith("USER ")
        ]
        assert not benutzer, f"Image wechselt den Benutzer: {benutzer}"

    def test_mcp_flags_nennen_browser_und_viewport(self) -> None:
        # Die beiden Flags, ohne die die Messung etwas anderes misst: ohne
        # --executablePath startet kein Chrome (das Modell weicht auf curl aus),
        # ohne --viewport hängt die halbe Wertung am Vorgabefenster des Agenten.
        assert f"--executablePath={CHROME_PATH}" in MCP_ARGS
        assert f"--viewport={VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT}" in MCP_ARGS

    def test_sandbox_nur_im_messlauf(self) -> None:
        """Der Oracle-Lauf fährt keinen Container.

        Das ist nicht Bequemlichkeit, sondern hält den Smoke-Test der Wertung
        dort verfügbar, wo kein Docker läuft: `uv run pytest` muss die Wertung
        prüfen können, ohne ein Image zu bauen.
        """
        assert a01_browser_audit(oracle=True).sandbox is None
        sandbox = a01_browser_audit().sandbox
        assert sandbox is not None
        assert sandbox.type == "docker"
        assert Path(sandbox.config).exists()

    def test_grenzen_sind_gesetzt(self) -> None:
        # Ein Agent ohne Nachrichten- und Zeitgrenze läuft im Fehlerfall, bis
        # jemand hinsieht. Beide Grenzen gelten nur im Messlauf.
        gemessen = a01_browser_audit()
        assert gemessen.message_limit == MESSAGE_LIMIT
        assert gemessen.time_limit
        assert a01_browser_audit(oracle=True).message_limit is None

    def test_lange_schlussantwort_passt_in_eine_anfrage(self) -> None:
        """Eine lange Antwort muss auch bei langsamem Modell fertig werden.

        Mit den 600 s und 4096 Tokens der Einzelaufgaben scheitert die
        Schlussantwort eines lokalen Modells, oder eine ordentliche
        Zwischenüberlegung wird abgeschnitten.
        """
        config = a01_browser_audit().config
        assert config.max_tokens == ANSWER_MAX_TOKENS
        assert ANSWER_MAX_TOKENS > MAX_TOKENS
        assert config.attempt_timeout == MODEL_CALL_TIMEOUT
        # Volle Länge bei 10 Tokens pro Sekunde.
        assert MODEL_CALL_TIMEOUT >= ANSWER_MAX_TOKENS / 10
        # Ein langer Versuch darf das Gesamtbudget nicht aufbrauchen, sonst
        # gibt es nach ihm keine Wiederholung mehr.
        assert config.timeout >= config.attempt_timeout
        # Die Zeitgrenze des Datensatzes bleibt die äußere Schranke.
        assert MODEL_CALL_TIMEOUT < TIME_LIMIT
        assert a01_browser_audit().time_limit == TIME_LIMIT


class TestLokalerServer:
    """`python -m german_seo_bench.testsite` — die Prüflandschaft zum Selberansehen.

    Sie ist kein Messpfad und wird nicht gewertet. Was sie trotzdem garantieren
    muss: Dass sie **dieselbe Website** zeigt wie der Messlauf. Ein eigener
    kleiner Server wäre bequemer und zeigte an sechs Adressen 404 statt der
    Befunde, die dort liegen — wer sich die Site so ansieht, bekäme ein
    falsches Bild davon, was ein Modell zu finden hat.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def lokal(cls) -> dict:
        return yaml.safe_load(
            Path(data_path("a01_browser_audit/local.compose.yaml")).read_text(
                encoding="utf-8"
            )
        )

    def test_dieselbe_auslieferung_wie_im_messlauf(self, lokal, compose_messlauf) -> None:
        """Gleiches Image, gleiche Konfiguration, gleiche Seiten.

        Die beiden Mounts sind die Prüflandschaft; weicht hier etwas ab, sieht
        man lokal eine andere Site als die gemessene — und zwar ohne dass
        irgendetwas rot würde.
        """
        lokal_site = lokal["services"]["testsite"]
        mess_site = compose_messlauf["services"]["testsite"]
        assert lokal_site["image"] == mess_site["image"]
        assert lokal_site["volumes"] == mess_site["volumes"]

    def test_veroeffentlicht_einen_port_und_haengt_an_keinem_internen_netz(
        self, lokal
    ) -> None:
        # Genau umgekehrt zum Messlauf: Dort ist `internal: true` die
        # Zusicherung, hier wäre sie der Fehler — niemand käme hin.
        assert "networks" not in lokal
        ports = lokal["services"]["testsite"]["ports"]
        assert len(ports) == 1 and ports[0].endswith(":80"), ports

    def test_vorgabeport_ist_80(self, lokal) -> None:
        """Sonst zeigt die sitemap.xml ins Leere.

        Die angemeldeten Adressen und die Canonicals nennen den Host **ohne**
        Port. Startet der lokale Server auf 8099, folgt ein Crawler der Sitemap
        auf Port 80 und findet nichts — das sieht aus wie eine kaputte Sitemap
        und ist eine falsche Portwahl.
        """
        from german_seo_bench.testsite import DEFAULT_PORT

        assert DEFAULT_PORT == 80
        assert "${BENCH_SITE_PORT:-80}:80" in lokal["services"]["testsite"]["ports"][0]

    def test_die_portpruefung_trennt_belegt_von_nicht_erlaubt(self) -> None:
        """Zwei Ursachen, zwei Antworten — und sie sind verschieden.

        Auf einem Rechner mit rootless Docker bleibt Port 80 stumm. `docker
        compose up` läuft weiter, der Browser meldet „nicht erreichbar", und
        `curl` bekommt `Could not connect`. Keine der drei Meldungen nennt die
        Ursache:
        Ports unter 1024 sind privilegiert, und rootless Docker veröffentlicht
        sie unter dem Konto des Benutzers.

        „Belegt" hilft ein anderer Port, „nicht erlaubt" nicht — dort braucht es
        `ip_unprivileged_port_start` oder einen hohen Port samt dem Hinweis,
        dass die Sitemap dann nicht mehr stimmt. Eine Prüfung, die nur „geht
        nicht" sagt, schickt einen in die falsche Richtung.
        """
        import socket

        from german_seo_bench.testsite import PORT_BELEGT, PORT_VERBOTEN, port_pruefen

        assert PORT_BELEGT != PORT_VERBOTEN
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as horcher:
            horcher.bind(("127.0.0.1", 0))
            horcher.listen(1)
            belegt = horcher.getsockname()[1]
            assert port_pruefen(belegt) == PORT_BELEGT
        # Nach dem Schließen ist derselbe Port wieder frei — sonst prüfte der
        # Test nur, dass irgendetwas zurückkommt.
        assert port_pruefen(belegt) is None

    def test_ein_verbotener_port_heisst_nicht_belegt(self, monkeypatch) -> None:
        """Der Zweig, der auf dieser Maschine nie von selbst läuft.

        Die Prüfung läuft im Test als root, und root darf Port 80 binden — der
        `PermissionError`-Zweig wäre damit ungetestet, und eine Gegenprobe, die
        ihn entfernt, schlüge nicht an.
        Deshalb wird der Fehler hier erzwungen statt erzeugt: Geprüft wird die
        ZUORDNUNG von Fehler zu Grund, und die ist das Einzige, was dieser
        Zweig tut.
        """
        import socket

        from german_seo_bench.testsite import PORT_BELEGT, PORT_VERBOTEN, port_pruefen

        def verboten(self, adresse):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(socket.socket, "bind", verboten)
        # Ein freier, hoher Port — belegt ist er nachweislich nicht, die
        # Antwort darf also nur aus dem Bindeversuch kommen.
        assert port_pruefen(49999) == PORT_VERBOTEN

        def belegt(self, adresse):
            raise OSError(98, "Address already in use")

        monkeypatch.setattr(socket.socket, "bind", belegt)
        assert port_pruefen(49999) == PORT_BELEGT

    def test_beide_portgruende_nennen_ihren_eigenen_ausweg(self) -> None:
        # Der Text ist hier die halbe Funktion: Eine Diagnose ohne Ausweg
        # kostet denselben Nachmittag wie keine Diagnose.
        import inspect as pyinspect

        from german_seo_bench import testsite

        quelle = pyinspect.getsource(testsite.main)
        assert "ip_unprivileged_port_start" in quelle
        assert "--port 8099" in quelle
        # Und der Preis des hohen Ports muss dabeistehen, sonst wundert sich
        # danach jemand über eine Sitemap, die ins Leere zeigt.
        assert "sitemap.xml" in quelle

    def test_eigener_compose_projektname(self) -> None:
        """Sonst teilt der lokale Server den Namensraum mit dem Messlauf.

        Alle drei Compose-Dateien von A01 liegen im selben Verzeichnis, und
        Compose leitet den Projektnamen daraus ab. Ohne eigenen Namen hießen
        der lokale `testsite` und der des Messlaufs gleich und wären dieselbe
        Identität — ein `up` zum Ansehen könnte den Container eines laufenden
        Messlaufs neu erzeugen.
        """
        import inspect as pyinspect

        from german_seo_bench import testsite

        assert testsite.PROJECT and testsite.PROJECT != "a01_browser_audit"
        # Nur die Befehlszeilen, nicht die Kommentare: Der Kommentar im Modul
        # BEGRÜNDET, warum es kein `--remove-orphans` gibt, und nennt es dabei.
        # Dieselbe Falle wie bei der Prüfung des Agent-Images.
        befehle = "\n".join(
            zeile
            for zeile in pyinspect.getsource(testsite).splitlines()
            if not zeile.lstrip().startswith("#")
        )
        # Beide Aufrufe — `up` und `down` — müssen den Projektnamen tragen.
        assert befehle.count('"-p", PROJECT, "-f"') == 2, befehle.count('"-p", PROJECT, "-f"')
        # `--remove-orphans` wäre unter einem geteilten Namensraum gefährlich
        # gewesen und ist mit eigenem Namen ohne Nutzen.
        assert "--remove-orphans" not in befehle

    def test_die_bereitschaft_wird_ueber_die_schleifenadresse_geprueft(self) -> None:
        """Die Prüfung muss Server von Browser und Namensauflösung trennen.

        Scheitert sie auf 127.0.0.1, liegt es am Server; klappt es dort und im
        Browser nicht, liegt es am Hostnamen oder am Browser. Fragte sie über
        den Hostnamen, fielen beide Fälle zusammen — und genau dann sucht man
        an der falschen Stelle.
        """
        import inspect as pyinspect

        from german_seo_bench import testsite

        quelle = pyinspect.getsource(testsite.erreichbar)
        assert "127.0.0.1" in quelle
        # Geprüft wird auf die VARIABLE `HOST`, nicht auf ihren Wert: Im
        # Quelltext steht `{HOST}`, nie `www.example.net`. Eine Prüfung auf den
        # Wert schlüge deshalb nie an.
        assert not re.search(r"\bHOST\b", quelle), (
            "die Bereitschaftsprüfung fragt über den Hostnamen — dann fallen "
            "'Server läuft nicht' und 'Name löst nicht auf' zusammen"
        )

    def test_die_hosts_zeile_nennt_den_host_aus_dem_prompt(self) -> None:
        # Zwei Fassungen derselben Adresse liefen auseinander, sobald jemand
        # eine davon ändert — deshalb kommt sie aus der Aufgabe.
        from german_seo_bench.testsite import hosts_line

        host = SITE_URL.removeprefix("http://").rstrip("/")
        assert hosts_line().split()[-1] == host
        assert hosts_line().startswith("127.0.0.1")


class TestAgentAchse:
    """Über welches Gerüst läuft das Audit — und bleibt sonst alles gleich?

    Der Sinn der Achse steht und fällt damit, dass **nur** das Framework
    wechselt. Prompt, Wertung, Referenz, Browser und Viewport müssen dieselben
    sein; sonst vergleicht ein Lauf über `goose` gegen einen über `react` zwei
    Dinge auf einmal und die Differenz ist nicht zuzuordnen.
    """

    def test_nur_bekannte_gerueste(self) -> None:
        assert AGENTS == (AGENT_REACT, AGENT_GOOSE)
        for name in AGENTS:
            assert a01_browser_audit(agent=name) is not None
        with pytest.raises(ValueError, match="Unbekannter Agent"):
            # Ein Tippfehler darf nicht still auf die Vorgabe zurückfallen:
            # Der Lauf hieße dann im Log `agent=opencode` und wäre ein
            # react-Lauf.
            a01_browser_audit(agent="opencode")

    def test_prompt_ist_fuer_beide_gerueste_derselbe(self, dataset) -> None:
        # Die Antwort gibt ein CLI über eine Datei ab, `react` über `submit` —
        # deshalb nennt der Prompt beide Wege, statt je Gerüst ein eigener zu
        # sein. Zwei Prompts wären eine zweite geänderte Größe.
        assert ANSWER_FILE in dataset[0].input
        assert (
            a01_browser_audit(agent=AGENT_REACT).dataset[0].input
            == a01_browser_audit(agent=AGENT_GOOSE).dataset[0].input
        )

    def test_das_cli_bekommt_denselben_browser(self) -> None:
        """Dieselben MCP-Flags wie im react-Pfad, wörtlich.

        Ohne das vergliche die Achse zwei Browser: `--executablePath` und
        `--viewport` sind Messbedingungen, keine Vorlieben, und beide hängen
        hier an einer Zeichenkette in einer CLI-Option statt an einer Liste.
        """
        erweiterung = next(
            arg
            for arg, davor in zip(goose_command(), [""] + goose_command())
            if davor == "--with-extension"
        )
        assert erweiterung.startswith(f"chrome-devtools:{MCP_COMMAND} ")
        for flag in MCP_ARGS:
            assert flag in erweiterung, flag

    def test_das_cli_fragt_nur_die_bruecke(self) -> None:
        # Der Endpunkt des CLI zeigt in den eigenen Container. Stünde hier eine
        # Adresse eines Anbieters, liefe die Messung am Inspect-Log vorbei —
        # und das `internal`-Netz wäre umgangen.
        env = goose_env(13131)
        assert env["OPENAI_HOST"] == "http://localhost:13131"
        assert not any(
            wert.startswith("http") and "localhost" not in wert for wert in env.values()
        )

    def test_das_cli_bringt_seine_eigene_grenze_mit(self) -> None:
        # `MESSAGE_LIMIT` zählt Nachrichten in Inspects Loop. Im CLI-Pfad gibt
        # es diesen Loop nicht; die Grenze heißt dort `--max-turns`. Bliebe
        # `MESSAGE_LIMIT` gesetzt, bräche der Lauf an einer Grenze ab, die
        # nicht den Agenten meint.
        assert a01_browser_audit(agent=AGENT_REACT).message_limit == MESSAGE_LIMIT
        assert a01_browser_audit(agent=AGENT_GOOSE).message_limit is None
        assert "--max-turns" in goose_command()

    def test_das_image_bringt_das_cli_festgenagelt_mit(self) -> None:
        dockerfile = Path(data_path("a01_browser_audit/agent.Dockerfile")).read_text(
            encoding="utf-8"
        )
        assert re.search(r"GOOSE_VERSION=\d+\.\d+\.\d+", dockerfile), (
            "keine festgenagelte goose-Version — dann entscheidet der Tag, "
            "was gemessen wurde"
        )
        befehle = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )
        # Das Installationsskript muss aus DEMSELBEN Release kommen wie das
        # Binary; `/stable/` wäre wieder ein wandernder Zeiger.
        assert "releases/download/v${GOOSE_VERSION}/download_cli.sh" in befehle
        assert "/stable/" not in befehle


def punkt_komma_hilfe(wert: float) -> str:
    return f"{wert:.2f}".replace(".", ",")


class TestBericht:
    """Der Kundenbericht — Beiwerk, und der Test hält genau das fest.

    Das Item ist kein Item: Es gibt keine Spalte dafür, keinen Punkt und keinen
    Abzug. Was hier geprüft wird, ist deshalb nicht die Qualität des Berichts,
    sondern seine **Folgenlosigkeit** — dass er die Messung weder verändert
    noch gefährdet — und die Trennung, um die es beim Bauen ging: Der
    Audit-Prompt darf von ihm nichts wissen.
    """

    def test_der_audit_prompt_weiss_nichts_vom_bericht(self, dataset) -> None:
        """Die eigentliche Zusicherung dieser Klasse.

        Stünde der Wunsch nach einem Bericht im Auftrag, müsste ein Modell zwei
        Dinge zugleich liefern — und kleine Modelle vermischen das dann
        (Fließtext um das JSON, oder ein Bericht statt der Liste). Der Bericht
        wird deshalb erst NACH der Antwort verlangt, in einem eigenen Aufruf.
        """
        text = dataset[0].input.lower()
        for wort in ("bericht", "pdf", "kunde", "html-dokument"):
            assert wort not in text, (
                f"der Audit-Prompt spricht von {wort!r} — damit verwässert die "
                "Zugabe die Aufgabe, die gemessen wird"
            )

    def test_der_berichtsauftrag_bekommt_die_eigene_antwort(self, sample) -> None:
        antwort = '[{"page": "home.html", "issue": "content_requires_js"}]'
        text = report_prompt(antwort, sample["input"]["pages"])
        assert antwort in text, "die eigene Antwort fehlt — dann berichtet das Modell frei"
        assert "NICHT in die Bewertung" in text
        # Markdown, und ausdrücklich kein HTML/CSS: Ein HTML-Bericht braucht
        # bei gleichem Inhalt etwa doppelt so viele Ausgabe-Tokens — Markup,
        # das das Layout aus dem Repo ohnehin mitbringt.
        assert "Markdown" in text
        assert "Kein HTML und kein CSS" in text
        assert "Codeblock" in text, "ohne den Hinweis kommt der Bericht im Zaun"

    def test_der_berichtsauftrag_verraet_die_referenz_nicht(self, sample) -> None:
        """Sonst wäre die Zugabe ein Leck.

        Der Bericht entsteht im selben Lauf wie das Audit. Stünde die Referenz
        darin, bekäme ein Modell die Lösung serviert — zwar erst nach der
        Abgabe, aber in einem Mehrfachdurchgang (`--epochs`) wäre sie dann im
        Kontext des nächsten.
        """
        text = report_prompt("[]", sample["input"]["pages"])
        for finding in sample["reference"]["findings"]:
            assert not (finding["page"] in text and finding["issue"] in text), finding

    LANG = "Lorem ipsum dolor sit amet, consetetur sadipscing elitr. " * 6

    @pytest.mark.parametrize(
        "roh, erwartet",
        [
            # Der Normalfall.
            ("# Bericht\n\n" + LANG, True),
            # Der ganze Bericht in einem Codeblock — der Zaun fällt weg.
            ("```markdown\n# Bericht\n\n" + LANG + "\n```", True),
            ("```\n# Bericht\n\n" + LANG + "\n```", True),
            # Eine Zeile Höflichkeit darüber ist ein Schönheitsfehler, kein
            # Grund zu verwerfen — sie zu entfernen hieße zu raten, wo der
            # Bericht anfängt.
            ("Gern, hier ist der Bericht:\n\n# Bericht\n\n" + LANG, True),
            # Keine Überschrift: kein Bericht.
            (LANG, False),
            # Zu kurz, und das ist der wichtige Fall — eine Absage.
            ("# Hinweis\n\nIch kann keinen Bericht erstellen.", False),
            ("", False),
        ],
    )
    def test_der_bericht_wird_erkannt_oder_verworfen(
        self, roh: str, erwartet: bool
    ) -> None:
        """Kein Notbehelf für den Fehlfall.

        Ein PDF aus irgendeinem Text sähe aus, als hätte das Modell einen
        Bericht geliefert — deshalb lieber keine Datei als eine leere.
        """
        ergebnis = extract_markdown(roh)
        assert (ergebnis is not None) == erwartet, ergebnis
        if erwartet:
            assert not ergebnis.startswith("```") and not ergebnis.endswith("```")

    def test_das_layout_kommt_aus_dem_repo_und_nicht_vom_modell(self) -> None:
        """Der zweite Gewinn des Markdown-Wegs, neben der halben Erzeugungszeit.

        Käme das CSS vom Modell, sähe jeder Bericht anders aus — und wer zwei
        Berichte nebeneinanderlegt, verglich zwei Geschmäcker statt zweier
        Analysen.
        """
        html = report_html(
            "# Prüfbericht\n\n| Seite | Befund |\n|---|---|\n| a | b |\n",
            "local/testmodell",
        )
        assert html.startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")
        # Das Stylesheet ist unseres, und es ist auf Druck ausgelegt.
        assert "@page" in html and "size: A4" in html
        assert "break-inside: avoid" in html
        # Keine externe Datei: Gedruckt wird in einem Container ohne Netz.
        assert "http://" not in html.replace(SITE_URL, "")
        assert "<script" not in html
        # Die Markdown-Tabelle muss als Tabelle ankommen — ohne die Erweiterung
        # rendert CommonMark sie als Fließtext mit Strichen.
        assert "<table>" in html and "<th>" in html
        # Und der Kopf nennt das Modell: Drei Berichte nebeneinander sind sonst
        # nicht auseinanderzuhalten.
        assert "local/testmodell" in html

    @staticmethod
    def _bewertung(tp: int, fp: int, fn: int) -> dict:
        """Eine Bewertung aus drei Zahlen, ohne einen ganzen Lauf zu bauen."""
        from german_seo_bench.checks.audit import grade_audit

        gemeldet = {(f"r{i}.html", "content_requires_js") for i in range(tp)} | {
            (f"f{i}.html", "content_requires_js") for i in range(fp)
        }
        referenz = {(f"r{i}.html", "content_requires_js") for i in range(tp)} | {
            (f"m{i}.html", "cloaked_content") for i in range(fn)
        }
        return {
            "verdict": grade_audit(gemeldet, referenz),
            "reported": gemeldet,
            "reference": referenz,
            # Wie `evaluate_audit` sie liefert — ohne diese beiden Schlüssel
            # prüfte der Test eine Bewertung, die es so nie gibt.
            "parse_error": None,
            "discarded": [],
            "by_capability": {
                "source": {"found": tp, "total": tp},
                "request": {"found": 0, "total": fn},
            },
            "false_by_capability": {"source": fp},
            "score": 0.5,
        }

    def test_der_kasten_sagt_was_der_bericht_wert_ist(self) -> None:
        """Die drei Angaben, ohne die das PDF ein hübsches Dokument ohne
        Wahrheitswert wäre.

        Ein Kundenbericht aus einem Lauf mit Score 0,13 sieht genauso sauber aus
        wie einer aus einem Lauf mit 0,96 — dieselbe Gliederung, dieselbe
        Sprache, dieselben Empfehlungen. Nur stimmt bei dem einen fast nichts
        davon.
        """
        html = assessment_html(self._bewertung(tp=3, fp=3, fn=28), {})
        assert 'class="bewertung"' in html
        # Was ein Leser zuerst wissen will: Wie viel davon stimmt überhaupt?
        assert "Von 6 gemeldeten Problemen gibt es" in html and "3 nicht" in html
        assert "28 tatsächlich vorhandene fehlen" in html
        assert "50%" in html and "10%" in html
        # Deutsche Dezimalschreibung — der Bericht geht an einen deutschen Kunden.
        assert "0,50" in html and "0.50" not in html
        # Und die Herkunft: Sonst liest das jemand als Selbsteinschätzung.
        assert "nicht vom Modell" in html and "vom Benchmark" in html

    @staticmethod
    def _state(messages: int = 10, **store):
        """Ein TaskState mit gefülltem Store — ohne einen Lauf zu fahren."""
        from inspect_ai.model import ChatMessageUser
        from inspect_ai.solver import TaskState
        from inspect_ai.util import Store

        zustand = TaskState(
            model="local/x", sample_id="s", epoch=1,
            input="", messages=[ChatMessageUser(content="x")] * messages,
        )
        zustand._store = Store(store)
        return zustand

    def test_ein_kontextueberlauf_steht_im_kasten(self) -> None:
        """Die wichtigste der Störungen, weil sie unsichtbar bleibt.

        Ein abgebrochener Lauf fällt auf. Ein Lauf, der neunmal seinen Kontext
        gekürzt hat und danach einen sauber formulierten Bericht abliefert,
        sieht aus wie ein normales Ergebnis — und sein niedriger Score sagt
        etwas über das Kontextfenster, nicht über das Modell.
        """
        from german_seo_bench.providers import STORE_CONTEXT_OVERFLOWS

        bewertung = self._bewertung(tp=1, fp=1, fn=1)
        notizen = run_notes(self._state(**{STORE_CONTEXT_OVERFLOWS: 9}), bewertung)
        assert any("9×" in n and "Kontextfenster" in n for n in notizen), notizen
        html = assessment_html(bewertung, {}, notizen)
        assert 'class="stoerung"' in html
        assert "Dieser Lauf war gestört" in html
        # Die Störung muss VOR den Zahlen stehen: Sie entscheidet, wie sie zu
        # lesen sind.
        assert html.index("gestört") < html.index("der Meldungen sind richtig")

    def test_ein_gescheitertes_agent_programm_steht_im_kasten(self) -> None:
        notizen = run_notes(
            self._state(**{STORE_CLI: {"returncode": 1}}), self._bewertung(1, 0, 1)
        )
        assert any("Rückgabewert 1" in n for n in notizen), notizen

    def test_eine_unlesbare_antwort_steht_im_kasten(self) -> None:
        bewertung = self._bewertung(tp=0, fp=0, fn=3)
        bewertung["parse_error"] = "kein JSON in der Antwort"
        bewertung["discarded"] = ["x.html/erfunden", "y.html/auch"]
        notizen = run_notes(self._state(), bewertung)
        assert any("nicht auswertbar" in n for n in notizen), notizen
        assert any("2 Meldungen wurden verworfen" in n for n in notizen), notizen

    def test_ein_ungestoerter_lauf_bekommt_keine_warnung(self) -> None:
        """Die Gegenprobe. Stünde die Warnung immer da, wäre sie wertlos."""
        notizen = run_notes(self._state(messages=5), self._bewertung(3, 0, 1))
        assert notizen == [], notizen
        html = assessment_html(self._bewertung(3, 0, 1), {}, notizen)
        assert 'class="stoerung"' not in html
        assert "gestört" not in html

    def test_knapp_an_der_zuggrenze_wird_vermerkt(self) -> None:
        # Die Grenze selbst beendet den Datensatz, bevor der Bericht entsteht.
        # Knapp darunter heißt: Der Agent hat die Prüfung womöglich abgekürzt.
        notizen = run_notes(
            self._state(messages=MESSAGE_LIMIT), self._bewertung(1, 0, 1)
        )
        assert any(str(MESSAGE_LIMIT) in n for n in notizen), notizen

    def test_der_kasten_verweist_auf_die_dokumentation(self) -> None:
        """Ohne den Zeiger steht `second_load` unerklärt in einem Kundendokument.

        Die Fähigkeitsnamen sind Benchmark-Vokabular und kommen im Bericht sonst
        nirgends vor. Ein Leser, der in der Tabelle `request 0 von 2` sieht,
        muss nachschlagen können, was damit gemeint ist.
        """
        from german_seo_bench.tasks.a01_browser_audit import REPORT_DOCS, REPORT_DOCS_PATH

        html = assessment_html(self._bewertung(tp=1, fp=1, fn=1), {})
        assert REPORT_DOCS in html, html
        # Und die beiden Klassen, deren Namen aus sich heraus nichts sagen,
        # bekommen im Kasten selbst eine Kurzerklärung.
        for klasse in ("second_load", "request"):
            assert klasse in html
        assert "zwei Ladevorgänge" in html
        # Die Datei muss es im Repo wirklich geben — ein toter Verweis in einem
        # Kundendokument ist schlimmer als keiner. Geprüft relativ zum Repo
        # selbst, damit der Test nicht am Namen des Checkout-Ordners hängt.
        repo = Path(__file__).resolve().parents[1]
        assert (repo / REPORT_DOCS_PATH).is_file(), repo / REPORT_DOCS_PATH
        # Und der Link zeigt auf genau diese Datei im Tag der Paketversion.
        from importlib.metadata import version

        assert REPORT_DOCS == (
            "https://github.com/stefanfis/german-seo-bench/blob/"
            f"v{version('german-seo-bench')}/{REPORT_DOCS_PATH}"
        )

    def test_der_kasten_nennt_die_erfundenen_befunde(self) -> None:
        # Die praktisch wichtigste Zeile: Ein Leser, der den Bericht umsetzt,
        # muss wissen, welche Empfehlung auf nichts beruht.
        html = assessment_html(self._bewertung(tp=1, fp=2, fn=1), {})
        assert "Frei erfunden" in html
        assert "f0.html/content_requires_js" in html

    def test_ein_fehlerfreier_bericht_bekommt_einen_anderen_satz(self) -> None:
        html = assessment_html(self._bewertung(tp=5, fp=0, fn=2), {})
        assert "Alles, was in diesem Bericht steht, trifft zu" in html
        assert "Frei erfunden" not in html

    def test_der_kasten_steht_vor_dem_ersten_befundabschnitt(self) -> None:
        """Hinter der Zusammenfassung, vor den Befunden — und ohne auf einen
        bestimmten Überschriftentext zu bauen.

        Auf die Überschrift „Befunde nach Dringlichkeit" zu matchen wäre
        unzuverlässig: Nur ein Teil der Modelle übernimmt die Formulierung aus
        dem Prompt. Vier gemessene Berichte trugen vier verschiedene
        Überschriften — eine davon wörtlich die aus dem Prompt, die anderen
        „Kritische Befunde: Indexierung und Crawling", „Indexierung und
        Crawling (Höchste Priorität)" und „Kritisch – Indexierung und
        Crawling".
        """
        rumpf = (
            "<h1>Bericht</h1>\n<p>Die Website steht schlecht da.</p>\n"
            "<h2>Kritische Befunde: Indexierung</h2>\n<p>…</p>\n"
            "<h2>Weitere Mängel</h2>\n"
        )
        ergebnis = insert_assessment(rumpf, "<div>KASTEN</div>")
        assert ergebnis.index("<p>Die Website steht") < ergebnis.index("KASTEN")
        assert ergebnis.index("KASTEN") < ergebnis.index("Kritische Befunde")

    def test_eine_zusammenfassungs_ueberschrift_wird_uebersprungen(self) -> None:
        # Schreibt das Modell seine Zusammenfassung unter eine eigene
        # Überschrift, gehört der Kasten dahinter und nicht davor.
        rumpf = (
            "<h1>Bericht</h1>\n<h2>Zusammenfassung</h2>\n<p>Kurz.</p>\n"
            "<h2>Kritische Befunde</h2>\n"
        )
        ergebnis = insert_assessment(rumpf, "<div>KASTEN</div>")
        assert ergebnis.index("Zusammenfassung") < ergebnis.index("KASTEN")
        assert ergebnis.index("KASTEN") < ergebnis.index("Kritische Befunde")

    def test_ohne_ueberschrift_steht_der_kasten_oben(self) -> None:
        # Lieber zu früh als gar nicht: Ohne den Kasten ist der Bericht ohne
        # Wahrheitswert.
        ergebnis = insert_assessment("<p>Nur Fließtext.</p>", "<div>KASTEN</div>")
        assert ergebnis.startswith("<div>KASTEN</div>")

    def test_der_kasten_ist_rot_umrandet(self) -> None:
        # Er gehört NICHT zum Bericht, und das muss man auf einen Blick sehen.
        html = report_html("# B\n\nText.\n\n## Befunde\n\nMehr.", "m",
                           self._bewertung(tp=1, fp=1, fn=1), {})
        assert "border: 2pt solid #b00020" in html
        assert 'class="bewertung"' in html

    def test_ohne_bewertung_bleibt_der_bericht_unveraendert(self) -> None:
        # Der Parameter ist freiwillig: `report_html` muss auch ohne Lauf
        # benutzbar bleiben (Tests, Nachrendern eines alten Markdowns).
        html = report_html("# B\n\n## Befunde\n\nText.", "m")
        assert 'class="bewertung"' not in html

    def test_die_zahlen_im_kasten_sind_die_des_scorers(self, sample) -> None:
        """Die Zusicherung gegen zwei Wahrheiten.

        Der Bericht entsteht VOR der Wertung und rechnet deshalb selbst. Täte er
        das anders als der Scorer, stünde im PDF eine andere Zahl als im Log —
        und man glaubte der falschen.
        """
        meta = {
            "pages": sample["input"]["pages"],
            "catalog": sample["input"]["catalog"],
            "findings": sample["reference"]["findings"],
            "capability_by_issue": sample["reference"]["capability_by_issue"],
        }
        antwort = json.dumps(sample["reference"]["findings"][:4])
        bewertung = evaluate_audit(antwort, meta)
        html = assessment_html(bewertung, meta["capability_by_issue"])
        assert f"{bewertung['verdict'].precision:.0%}" in html
        assert punkt_komma_hilfe(bewertung["score"]) in html

    def test_markup_im_bericht_wird_nicht_ausgefuehrt(self) -> None:
        """Das Modell hat die geprüfte Website gelesen — mitsamt ihrer Skripte.

        Übernimmt es daraus ein `<script>` in den Bericht, darf das im PDF Text
        sein und kein Code. `markdown-it` läuft deshalb mit `html=False`.
        """
        html = report_html(
            "# Bericht\n\nGefunden: <script>alert(1)</script> im Kopf.\n", "m"
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_abschaltbar_und_standardmaessig_an(self) -> None:
        mit = a01_browser_audit()
        ohne = a01_browser_audit(report=False)
        assert len(mit.solver) == len(ohne.solver) + 1
        # Im Oracle-Pfad gibt es ihn nie: kein Modell, kein Container.
        assert not isinstance(a01_browser_audit(oracle=True).solver, list)

    def test_der_bericht_setzt_das_audit_gespraech_nicht_fort(self) -> None:
        """Zwei Dinge, die derselbe Griff kaputt machen würde.

        Der naheliegende Weg wäre `await generate(state)` — Inspects
        Solver-Hilfe. Sie tut aber genau zwei Dinge, die hier beide falsch sind:
        Sie setzt das **Audit-Gespräch** fort (bei 26 Seiten voller
        Browser-Snapshots hätte ein kleines Modell dort kein Fenster mehr) und
        sie **überschreibt `state.output`** — also die Antwort, die der Scorer
        wertet. Ein Bericht könnte dann eine Wertung ändern.

        Geprüft wird das am Quelltext des Schritts, und zwar weil es anders
        nicht geht: Solange der Schritt `state.output` gar nicht anfasst, ist
        die Wiederherstellung unten nicht beobachtbar — eine Gegenprobe, die
        sie entfernt, schlägt nicht an. Was hier festgehalten wird, ist deshalb
        die Entscheidung selbst, nicht ihre Wirkung.
        """
        import inspect as pyinspect
        import re as pyre

        quelle = pyinspect.getsource(audit_report)
        assert pyre.search(r"get_model\(\)\.generate\(", quelle), (
            "der Bericht fragt das Modell nicht mehr direkt"
        )
        assert not pyre.search(r"(?<![.\w])generate\(state", quelle), (
            "der Bericht ruft `generate(state)` — damit setzt er das "
            "Audit-Gespräch fort und überschreibt die gewertete Antwort"
        )
        assert "state.output = antwort" in quelle, (
            "die gewertete Antwort wird nicht mehr zurückgeschrieben — heute "
            "ist das folgenlos, nach einem Umbau auf `generate()` nicht mehr"
        )
        # Und die Nachrichten des Berichts dürfen nicht in den Verlauf: Der
        # Scorer liest dort Verhalten und Abdeckung ab.
        assert "state.messages.append" not in quelle

    def test_ein_gescheiterter_bericht_kostet_keinen_punkt(self, log_dir: str) -> None:
        """Die Zusicherung, auf die es ankommt.

        Gefahren wird der Oracle-Pfad MIT dem Berichtsschritt. Dort gibt es
        keinen Container — `sandbox()` wirft, und zwar an genau der Stelle, an
        der es im Messlauf auch schiefgehen könnte. Der Score muss trotzdem
        1.00 sein, und im Log muss stehen, warum es keinen Bericht gibt.

        Ohne diese Probe wäre der Bericht das, was eine Zugabe nie sein darf:
        eine neue Art, einen Lauf zu verlieren.
        """
        from german_seo_bench.oracle import oracle_solver

        log = inspect_eval(
            a01_browser_audit(oracle=True),
            model="none/none",
            log_dir=log_dir,
            solver=[oracle_solver(field="findings"), audit_report()],
        )[0]
        assert log.status == "success", log.error
        score = next(s for s in log.results.scores if s.name == "score")
        assert score.metrics["mean"].value == 1.0
        vermerk = log.samples[0].store[STORE_REPORT]
        assert vermerk["status"] != "erstellt", vermerk
        # Die Dauer steht auch im Fehlerfall da — sonst wüsste man nicht, ob
        # der Bericht sofort gescheitert ist oder erst nach einem langen
        # Modellaufruf, und könnte ihn nicht aus der Laufzeit herausrechnen.
        assert isinstance(vermerk["seconds"], float), vermerk
        # Und die Antwort, die gewertet wurde, ist unverändert durchgekommen.
        detail = log.samples[0].scores["audit_scorer"].metadata
        assert (detail["fp"], detail["fn"]) == (0, 0)
        assert detail["report"] == vermerk


class TestOracle:
    """Der Smoke-Test der Wertung: Die Referenz als Antwort muss 1.00 ergeben."""

    @pytest.fixture(scope="class")
    @classmethod
    def log(cls, log_dir: str):
        return inspect_eval(
            a01_browser_audit(oracle=True), model="none/none", log_dir=log_dir
        )[0]

    def test_lauf_erfolgreich(self, log) -> None:
        assert log.status == "success", log.error

    @pytest.mark.parametrize("metric", ("score", "precision", "recall"))
    def test_metrik_ist_eins(self, log, metric: str) -> None:
        score = next(s for s in log.results.scores if s.name == metric)
        assert score.metrics["mean"].value == 1.0

    def test_verhaltensspalten_sind_im_oracle_null(self, log) -> None:
        """Der Oracle-Lauf ruft kein Werkzeug auf — die Spalten müssen das sagen.

        Sie sind kein Leistungsmaß: Hier stehen sie zu Recht auf 0, obwohl `score`
        1.00 ist. Wären sie an die Wertung gekoppelt, fiele genau das auf.
        """
        for name in ("clicked", "scrolled", "ua_switched"):
            score = next(s for s in log.results.scores if s.name == name)
            assert score.metrics["mean"].value == 0.0
        detail = log.samples[0].scores["audit_scorer"].metadata
        assert detail["tool_calls"] == {}
        assert detail["clicked"] is False and detail["scrolled"] is False
        assert detail["ua_switched"] is False
        # Kein CLI im Spiel: Die beiden Felder müssen leer bleiben, sonst
        # stünde im Log eine Herkunft, die es nicht gab.
        assert detail["answer_source"] is None and detail["cli"] is None

    def test_jede_klasse_ist_im_oracle_vollstaendig(self, log) -> None:
        # Eine Klasse, die im Oracle nicht 1.00 erreicht, ist falsch verdrahtet
        # — dann zählt die Spalte etwas anderes als die Referenz.
        for name in CAPABILITY_CLASSES:
            score = next(s for s in log.results.scores if s.name == f"recall_{name}")
            assert score.metrics["mean"].value == 1.0, name
        detail = log.samples[0].scores["audit_scorer"].metadata["by_capability"]
        assert sum(e["total"] for e in detail.values()) == len(
            log.samples[0].metadata["findings"]
        )

    def test_alle_befunde_gezaehlt(self, log, sample) -> None:
        detail = log.samples[0].scores["audit_scorer"].metadata
        assert detail["tp"] == len(sample["reference"]["findings"])
        assert (detail["fp"], detail["fn"]) == (0, 0)
        assert not detail["discarded"] and detail["parse_error"] is None

    def test_jede_klasse_ist_im_oracle_belegt_und_voll(self, log) -> None:
        detail = log.samples[0].scores["audit_scorer"].metadata
        # Keine Klasse darf hier `None` sein — im Messlauf hat jede Befunde,
        # und `test_klassen_stimmen_mit_dem_datensatz` erzwingt das.
        assert all(v == 1.0 for v in detail["f1_by_capability"].values())
        assert all(v == 0 for v in detail["false_by_capability"].values())

    def test_der_score_bestraft_eine_verfehlte_klasse(self, log_dir: str) -> None:
        """Die Zusicherung, an der die Wahl des Scores hängt.

        Der Löser hier holt alles außer den beiden harten Klassen (`request`
        und `second_load`) — genau das Verhalten eines Modells, das die
        ausgelieferten Bytes liest und sonst nichts. Micro-F1 ließe das
        durchgehen. Der Score muss hier deutlich
        darunter liegen, sonst misst er wieder die Verteilung der Items statt
        der Fähigkeiten.
        """
        from inspect_ai.model import ModelOutput
        from inspect_ai.solver import Generate, Solver, TaskState, solver

        HART = ("request", "second_load")

        @solver
        def nur_die_billigen() -> Solver:
            async def solve(state: TaskState, generate: Generate) -> TaskState:
                klassen = state.metadata["capability_by_issue"]
                billig = [
                    f
                    for f in state.metadata["findings"]
                    if klassen[f["issue"]] not in HART
                ]
                state.output = ModelOutput.from_content(
                    model="billig", content=json.dumps(billig)
                )
                state.messages.append(state.output.message)
                return state

            return solve

        log = inspect_eval(
            a01_browser_audit(oracle=True),
            model="none/none",
            log_dir=log_dir,
            solver=nur_die_billigen(),
        )[0]
        # `audit_scorer` selbst steht ohne Metrik in der Liste — nur die
        # einzelnen Spalten tragen eine.
        werte = {
            s.name: s.metrics["mean"].value
            for s in log.results.scores
            if "mean" in s.metrics
        }
        # Precision bleibt makellos — der Löser meldet nichts Falsches.
        assert werte["precision"] == 1.0
        # Der Score fällt auf die Klassenquote: drei von fünf Klassen
        # vollständig, zwei auf null.
        assert werte["score"] == pytest.approx(3 / 5), werte["score"]
        for name in HART:
            assert werte[f"recall_{name}"] == 0.0, name
        # Und die Gegenprobe zur Wahl: Micro-F1 über alle Paare hätte dasselbe
        # Verhalten durchgewinkt. Die Zahl steht als Diagnose in den Metadaten
        # — ohne diesen Vergleich wäre nicht belegt, dass der Score etwas
        # anderes misst als sie.
        micro = log.samples[0].scores["audit_scorer"].metadata["micro_f1"]
        assert micro > 0.85, micro
        assert micro - werte["score"] > 0.2, (
            f"Micro-F1 {micro:.3f} und Score {werte['score']:.3f} liegen zu "
            "dicht beieinander — dann hat der Wechsel nichts gebracht"
        )

    def test_gegenprobe_scorer_unterscheidet(self, log_dir: str) -> None:
        """Ohne diese Probe wäre 1.00 auch mit einem Scorer erreichbar, der
        immer 1.00 liefert. Gemeldet wird hier ALLES — richtige Ids, falsch
        verteilt; das prüft zugleich, dass Precision wirkt.
        """
        from inspect_ai.model import ModelOutput
        from inspect_ai.solver import Generate, Solver, TaskState, solver

        @solver
        def alles_melden() -> Solver:
            async def solve(state: TaskState, generate: Generate) -> TaskState:
                alles = [
                    {"page": page, "issue": entry["issue"]}
                    for page in state.metadata["pages"]
                    for entry in state.metadata["catalog"]
                ]
                state.output = ModelOutput.from_content(
                    model="broken", content=json.dumps(alles)
                )
                state.messages.append(state.output.message)
                return state

            return solve

        broken = inspect_eval(
            a01_browser_audit(oracle=True),
            model="none/none",
            log_dir=log_dir,
            solver=alles_melden(),
        )[0]
        score = next(s for s in broken.results.scores if s.name == "score")
        precision = next(s for s in broken.results.scores if s.name == "precision")
        recall = next(s for s in broken.results.scores if s.name == "recall")
        assert recall.metrics["mean"].value == 1.0
        assert precision.metrics["mean"].value < 0.2
        assert score.metrics["mean"].value < 0.3


class TestBerichtsziel:
    """Wohin der Kundenbericht auf dem Host geht."""

    def test_ohne_vorgabe_relativ_zum_arbeitsverzeichnis(self, monkeypatch) -> None:
        from german_seo_bench.tasks.a01_browser_audit import report_dir

        monkeypatch.delenv("GERMAN_SEO_BENCH_REPORT_DIR", raising=False)
        assert report_dir() == Path("reports")

    def test_umgebungsvariable_legt_das_ziel_fest(self, monkeypatch, tmp_path) -> None:
        # Der Name steht so in der Doku. Ein Tippfehler im Code ließe die
        # Variable still wirkungslos, und die Berichte landeten wieder im Repo.
        from german_seo_bench.tasks.a01_browser_audit import report_dir

        monkeypatch.setenv("GERMAN_SEO_BENCH_REPORT_DIR", str(tmp_path))
        assert report_dir() == tmp_path
