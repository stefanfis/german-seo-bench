"""Gegenproben zur Wertung des Browser-Audits (A01) — ohne Inspect, ohne Docker.

Zu jeder Zusicherung gehört der Fall, in dem sie anschlagen muss. Besonders
wichtig ist das hier bei den beiden Nullen: "hat nichts gemeldet" und "hat kein
JSON geliefert" ergeben dieselbe Zahl und sind verschiedene Befunde.
"""

from types import SimpleNamespace

import pytest

from german_seo_bench.checks import (
    as_findings,
    behaviour,
    grade_audit,
    pages_seen,
    parse_findings,
    tool_base_name,
    tool_usage,
)


def nachricht(*aufrufe: tuple[str, dict]) -> SimpleNamespace:
    """Eine Assistenten-Nachricht mit Werkzeugaufrufen, ohne Inspect.

    `tool_usage` und `behaviour` lesen nur `tool_calls`, `function` und
    `arguments` — und zwar über `getattr`. Genau deshalb sind sie mit
    Wegwerf-Objekten testbar, und `checks/` bleibt frei von Inspect.
    """
    return SimpleNamespace(
        tool_calls=[
            SimpleNamespace(function=name, arguments=args) for name, args in aufrufe
        ]
    )

# Der Zustand "hat nur geladen und gelesen". Als Konstante, damit eine neue
# Verhaltensspalte an genau einer Stelle nachgetragen wird und die Gegenproben
# nicht einzeln danebenliegen.
UNTAETIG = {
    "clicked": False,
    "scrolled": False,
    "ua_switched": False,
    "cache_bypassed": False,
}

PAGES = ["home.html", "produkt.html", "blog.html"]
ISSUES = ["content_requires_js", "title_changed_by_js", "broken_resource"]


def antwort(*paare: tuple[str, str]) -> str:
    inner = ", ".join(f'{{"page": "{p}", "issue": "{i}"}}' for p, i in paare)
    return f"[{inner}]"


class TestParseFindings:
    def test_saubere_meldung(self) -> None:
        found, discarded, error = parse_findings(
            antwort(("home.html", "content_requires_js")), PAGES, ISSUES
        )
        assert found == {("home.html", "content_requires_js")}
        assert not discarded and error is None

    def test_leeres_array_ist_eine_aussage(self) -> None:
        # "keine Probleme gefunden" ist eine Meldung, kein Formatfehler — und
        # muss von "kein JSON" unterscheidbar bleiben.
        found, discarded, error = parse_findings("[]", PAGES, ISSUES)
        assert found == set() and not discarded and error is None

    def test_kein_json_ist_ein_anderer_fall(self) -> None:
        for text in ("", "Ich habe die Seiten geprüft.", "{"):
            _, _, error = parse_findings(text, PAGES, ISSUES)
            assert error, text

    def test_objekt_mit_einem_array_wird_ausgepackt(self) -> None:
        # Dieselbe Toleranz wie in M03: Die Verpackung ist nicht, was gemessen
        # wird. Die Einträge darin werden unverändert streng geprüft.
        text = '{"findings": [{"page": "home.html", "issue": "content_requires_js"}]}'
        found, discarded, error = parse_findings(text, PAGES, ISSUES)
        assert found == {("home.html", "content_requires_js")}
        assert not discarded and error is None

    def test_objekt_ohne_array_faellt_durch(self) -> None:
        # Die Gegenprobe zur Toleranz: Ein einzelnes Befundobjekt ist kein
        # Ergebnis über viele Seiten, und welches gemeint war, wäre geraten.
        _, _, error = parse_findings(
            '{"page": "home.html", "issue": "content_requires_js"}', PAGES, ISSUES
        )
        assert error == "JSON ist ein Objekt und kein Array von Befunden"

    def test_codeblock_und_prosa_drumherum(self) -> None:
        text = (
            "Hier mein Ergebnis:\n\n```json\n"
            '[{"page": "home.html", "issue": "content_requires_js"}]\n'
            "```\n\nInsgesamt ein Befund."
        )
        found, _, error = parse_findings(text, PAGES, ISSUES)
        assert found == {("home.html", "content_requires_js")} and error is None

    def test_unbekannte_seite_wird_verworfen_und_benannt(self) -> None:
        found, discarded, _ = parse_findings(
            antwort(("startseite.html", "content_requires_js")), PAGES, ISSUES
        )
        assert found == set()
        assert discarded and "Seite unbekannt" in discarded[0]

    def test_problem_ausserhalb_des_katalogs(self) -> None:
        # Der Katalog ist geschlossen. Ein Agent, der sich eine Id ausdenkt, hat
        # die Aufgabe nicht erfüllt — und das soll in der Wertung stehen, nicht
        # stillschweigend verschwinden.
        found, discarded, _ = parse_findings(
            antwort(("home.html", "fehlender_alt_text")), PAGES, ISSUES
        )
        assert found == set()
        assert discarded and "Problem nicht im Katalog" in discarded[0]

    def test_doppelte_meldung_zaehlt_einmal(self) -> None:
        found, _, _ = parse_findings(
            antwort(
                ("home.html", "content_requires_js"),
                ("home.html", "content_requires_js"),
            ),
            PAGES,
            ISSUES,
        )
        assert len(found) == 1

    def test_gueltiges_neben_ungueltigem(self) -> None:
        # Eine kaputte Zeile darf die guten nicht mitnehmen.
        found, discarded, error = parse_findings(
            antwort(
                ("home.html", "content_requires_js"),
                ("erfunden.html", "content_requires_js"),
            ),
            PAGES,
            ISSUES,
        )
        assert found == {("home.html", "content_requires_js")}
        assert len(discarded) == 1 and error is None


class TestGradeAudit:
    REFERENCE = {
        ("home.html", "content_requires_js"),
        ("produkt.html", "title_changed_by_js"),
        ("produkt.html", "broken_resource"),
    }

    def test_alles_getroffen(self) -> None:
        v = grade_audit(set(self.REFERENCE), self.REFERENCE)
        assert (v.f1, v.precision, v.recall) == (1.0, 1.0, 1.0)
        assert (v.tp, v.fp, v.fn) == (3, 0, 0)

    def test_nichts_gemeldet(self) -> None:
        v = grade_audit(set(), self.REFERENCE)
        assert (v.f1, v.precision, v.recall) == (0.0, 0.0, 0.0)
        assert v.fn == 3

    def test_alles_gemeldet_ist_nicht_gut(self) -> None:
        """Die Gegenprobe, für die es F1 überhaupt gibt.

        Wer jeden Katalogeintrag auf jeder Seite meldet, hat Recall 1.00 und ist
        nutzlos. Ein reiner Recall-Score würde das belohnen.
        """
        alles = {(p, i) for p in PAGES for i in ISSUES}
        v = grade_audit(alles, self.REFERENCE)
        assert v.recall == 1.0
        assert v.precision == pytest.approx(3 / 9)
        assert v.f1 < 0.6

    def test_haelfte_getroffen(self) -> None:
        v = grade_audit({("home.html", "content_requires_js")}, self.REFERENCE)
        assert v.precision == 1.0
        assert v.recall == pytest.approx(1 / 3)
        assert v.f1 == pytest.approx(0.5)

    def test_aufschluesselung_nach_problemtyp(self) -> None:
        # Der diagnostische Kern: WELCHE Fehlerklasse fehlt. Ein Agent ohne
        # Scrollen und einer ohne Klicken kommen auf ähnliches F1 und können
        # völlig Verschiedenes nicht.
        v = grade_audit({("blog.html", "broken_resource")}, self.REFERENCE)
        assert v.false == {"broken_resource": ["blog.html"]}
        assert v.missed == {
            "broken_resource": ["produkt.html"],
            "content_requires_js": ["home.html"],
            "title_changed_by_js": ["produkt.html"],
        }

    def test_leere_referenz_gibt_keine_punkte(self) -> None:
        # Nicht 1.00: Ohne Referenz ist nichts zu messen, und eine 1.00 sähe wie
        # ein perfekter Lauf aus.
        assert grade_audit(set(), set()).f1 == 0.0


class TestAsFindings:
    def test_referenzzeilen_werden_paare(self) -> None:
        rows = [{"page": "home.html", "issue": "content_requires_js"}]
        assert as_findings(rows) == {("home.html", "content_requires_js")}

    def test_gleiche_form_wie_die_meldung(self) -> None:
        # Die Zusicherung, an der die ganze Wertung hängt: Referenz und Meldung
        # müssen dieselbe Form haben, sonst ist die Schnittmenge immer leer —
        # ein Fehler, der wie ein völlig unfähiges Modell aussieht.
        rows = [{"page": "home.html", "issue": "content_requires_js"}]
        reported, _, _ = parse_findings(
            antwort(("home.html", "content_requires_js")), PAGES, ISSUES
        )
        assert as_findings(rows) == reported


class TestWerkzeugnutzung:
    """Wie der Agent gearbeitet hat — die zweite Hälfte der Diagnose."""

    def test_zaehlt_je_werkzeug(self) -> None:
        verlauf = [
            nachricht(("navigate_page", {"url": "http://www.example.net/home.html"})),
            nachricht(("take_snapshot", {}), ("list_network_requests", {})),
            SimpleNamespace(content="nur Text, keine Aufrufe"),
        ]
        assert tool_usage(verlauf) == {
            "navigate_page": 1,
            "take_snapshot": 1,
            "list_network_requests": 1,
        }

    def test_ohne_aufrufe_ist_leer(self) -> None:
        assert tool_usage([SimpleNamespace(content="nichts")]) == {}

    def test_klick_wird_erkannt(self) -> None:
        assert behaviour([nachricht(("click", {"uid": "7"}))])["clicked"]

    def test_scrollen_per_skript(self) -> None:
        # chrome-devtools-mcp hat kein Scroll-Werkzeug; gescrollt wird über
        # `evaluate_script`. Deshalb wird in den Argumenten gesucht.
        verlauf = [
            nachricht(("evaluate_script", {"function": "() => window.scrollTo(0, 99999)"}))
        ]
        assert behaviour(verlauf)["scrolled"]

    def test_klick_per_skript(self) -> None:
        """Ein Klick im Skript zählt wie das Werkzeug `click`.

        Ein Agent kann `btn.click()` in einem `evaluate_script` aufrufen und die
        Blockade mit `performance.now()` selbst messen. Zählte die Spalte nur das
        Werkzeug `click`, stünde dort "nicht geklickt" — und aus dem gefundenen
        `interaction_blocked` würde man auf ein undichtes Item schließen.
        """
        skript = (
            "() => { const t0 = performance.now(); "
            "document.getElementById('filter').click(); "
            "return performance.now() - t0; }"
        )
        assert behaviour([nachricht(("evaluate_script", {"function": skript}))])["clicked"]

    def test_lesen_ist_kein_handeln(self) -> None:
        """Die Gegenprobe zu beiden Mustern — sonst zählt Inspektion als Handlung.

        `scrollHeight` abzufragen ist kein Scrollen, und ein `onclick`-Attribut zu
        lesen ist kein Klick. Beides kommt in einem sauberen Audit von
        `scroll.html` vor.
        """
        verlauf = [
            nachricht(("evaluate_script", {"function": "() => document.body.scrollHeight"})),
            nachricht(
                (
                    "evaluate_script",
                    {"function": "() => [...document.links].map(a => a.getAttribute('onclick'))"},
                )
            ),
        ]
        assert behaviour(verlauf) == UNTAETIG

    def test_scrollen_per_taste(self) -> None:
        assert behaviour([nachricht(("press_key", {"key": "End"}))])["scrolled"]

    def test_laden_und_ansehen_ist_kein_handeln(self) -> None:
        """Die Gegenprobe: navigieren, Snapshot, Netzwerkliste.

        Beide Flags müssen dann falsch sein,
        sonst meldet die Spalte ein Verhalten, das es nicht gab.
        """
        verlauf = [
            nachricht(
                ("navigate_page", {"url": "http://www.example.net/scroll.html"}),
                ("take_snapshot", {}),
                ("list_network_requests", {}),
            )
        ]
        assert behaviour(verlauf) == UNTAETIG

    def test_skript_ohne_scrollen_zaehlt_nicht(self) -> None:
        verlauf = [nachricht(("evaluate_script", {"function": "() => document.title"}))]
        assert not behaviour(verlauf)["scrolled"]


class TestWerkzeugnamen:
    """Ein Agent-CLI benennt dieselben Werkzeuge anders als Inspects Loop.

    Mit goose gegen chrome-devtools-mcp heißen die Werkzeuge `chrome-devtools__click`, `chrome-devtools__press_key`
    und so weiter, über `react()` dagegen `click` und `press_key`. Ohne
    Normalisierung wäre `clicked` auf dem CLI-Pfad blind — die Spalte meldete
    "nicht geklickt" für einen Agenten, der geklickt hat, und aus einem
    trotzdem gefundenen Befund läse man ein undichtes Item.
    """

    def test_praefix_faellt_weg(self) -> None:
        assert tool_base_name("chrome-devtools__click") == "click"
        assert tool_base_name("mcp.chrome-devtools.take_snapshot") == "take_snapshot"
        assert tool_base_name("click") == "click"

    def test_histogramm_ist_ueber_frameworks_vergleichbar(self) -> None:
        # Sonst stünden im Log zwei Namen für dasselbe Werkzeug, und eine
        # Auswertung über beide Pfade addierte sie nicht.
        react = tool_usage([nachricht(("click", {"uid": "1"}))])
        cli = tool_usage([nachricht(("chrome-devtools__click", {"uid": "1"}))])
        assert react == cli == {"click": 1}

    def test_klick_wird_auch_mit_praefix_erkannt(self) -> None:
        assert behaviour([nachricht(("chrome-devtools__click", {"uid": "1"}))])["clicked"]


class TestUserAgentWechsel:
    """`ua_switched` — die Spalte zu `cloaked_content`.

    Sie unterscheidet "hat es versucht" von "kam nicht auf die Idee". Erkannt
    wird das SETZEN eines User-Agents, egal über welches Werkzeug; das bloße
    Auslesen ist wie `scrollHeight` Inspektion und keine Handlung.
    """

    def test_emulate_mit_user_agent(self) -> None:
        verlauf = [nachricht(("emulate", {"userAgent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}))]
        assert behaviour(verlauf)["ua_switched"]

    def test_zusaetzliche_kopfzeile(self) -> None:
        verlauf = [
            nachricht(
                (
                    "chrome-devtools__emulate",
                    {"extraHttpHeaders": {"User-Agent": "Googlebot/2.1"}},
                )
            )
        ]
        assert behaviour(verlauf)["ua_switched"]

    def test_curl_im_shell_werkzeug(self) -> None:
        # Der CLI-Pfad: goose bringt ein Shell-Werkzeug mit, und ein Audit über
        # `curl -A` ist ein legitimer Weg zu diesem Befund.
        verlauf = [
            nachricht(("shell", {"command": 'curl -A "Googlebot/2.1" http://www.example.net/preise.html'}))
        ]
        assert behaviour(verlauf)["ua_switched"]

    def test_auslesen_ist_kein_wechsel(self) -> None:
        """Die Gegenprobe. Ohne sie zählte jedes Audit als UA-Wechsel, das
        `navigator.userAgent` liest — und die Spalte sagte nichts mehr."""
        verlauf = [
            nachricht(("evaluate_script", {"function": "() => navigator.userAgent"})),
            nachricht(("evaluate_script", {"function": "() => document.title"})),
        ]
        assert behaviour(verlauf) == UNTAETIG

    def test_ein_gewoehnliches_audit_wechselt_nicht(self) -> None:
        verlauf = [
            nachricht(
                ("navigate_page", {"url": "http://www.example.net/preise.html"}),
                ("take_snapshot", {}),
            )
        ]
        assert not behaviour(verlauf)["ua_switched"]


class TestAbdeckung:
    """`coverage` — hat der Agent die Seiten überhaupt angesehen?

    Die Spalte trennt zwei Nullen, die in `recall` zusammenfallen: „geprüft und
    nichts gefunden" und „nie hingesehen". Ein Agent, der eine Seite prüft und
    dann abgibt, liest sich ohne sie wie ein Modell, das an der Aufgabe
    scheitert.
    """

    SEITEN = ["home.html", "produkt.html", "blog.html"]

    def test_navigation_zaehlt(self) -> None:
        verlauf = [
            nachricht(("new_page", {"url": "http://www.example.net/home.html"})),
            nachricht(("navigate_page", {"pageId": 2, "url": "http://www.example.net/produkt.html"})),
        ]
        assert pages_seen(verlauf, self.SEITEN) == {"home.html", "produkt.html"}

    def test_abruf_im_skript_zaehlt_auch(self) -> None:
        # Dieselbe Regel wie bei `clicked`: die Handlung, egal auf welchem Weg.
        # Ein Modell, das sich sein Audit selbst programmiert, sieht die Seite
        # genauso an wie eines, das navigiert.
        verlauf = [
            nachricht(
                (
                    "evaluate_script",
                    {"function": "async () => (await fetch('http://www.example.net/blog.html')).text()"},
                )
            )
        ]
        assert pages_seen(verlauf, self.SEITEN) == {"blog.html"}

    def test_curl_im_shell_werkzeug_zaehlt(self) -> None:
        verlauf = [nachricht(("shell", {"command": "curl http://www.example.net/produkt.html"}))]
        assert pages_seen(verlauf, self.SEITEN) == {"produkt.html"}

    def test_einen_namen_lesen_ist_kein_ansehen(self) -> None:
        """Die Gegenprobe. Ein Skript, das die Links einer Seite ausliest,
        nennt fremde Seiten — angesehen hat es sie nicht. Ohne diese Grenze
        stünde die Abdeckung nach dem ersten Snapshot auf 1.00."""
        verlauf = [
            nachricht(
                (
                    "evaluate_script",
                    {"function": "() => ['blog.html', 'produkt.html'].length"},
                )
            )
        ]
        assert pages_seen(verlauf, self.SEITEN) == set()

    def test_ein_pfadvergleich_im_skript_ist_kein_ansehen(self) -> None:
        """Ein Pfad in einem Vergleich, ohne die Seite je zu laden.

        Wer nach `/<datei>` suchte und das als angesehen zählte, machte genau
        den Fehler, gegen den es die Abdeckungsspalte gibt.
        """
        verlauf = [
            nachricht(
                (
                    "evaluate_script",
                    {
                        "function": "async () => Array.from(document.querySelectorAll('a'))"
                        ".some(l => l.href && l.href.endsWith('/produkt.html'))"
                    },
                )
            )
        ]
        assert pages_seen(verlauf, self.SEITEN) == set()

    def test_schleife_ueber_eine_seitenliste_zaehlt(self) -> None:
        """Alle Seiten in einer Schleife, die Adresse erst zusammengesetzt.

        Eine Regel, die nur fertige URLs erkennt, zählte dort nur einen Teil —
        neben einem hohen Recall machte das die Spalte unglaubwürdig.
        """
        skript = (
            'async () => { const pages = ["home.html","produkt.html","blog.html"]; '
            'for (const p of pages) { await fetch("http://www.example.net/" + p); } }'
        )
        verlauf = [nachricht(("evaluate_script", {"function": skript}))]
        assert pages_seen(verlauf, self.SEITEN) == set(self.SEITEN)

    def test_namensliste_ohne_abruf_zaehlt_weiter_nicht(self) -> None:
        # Die Gegenprobe dazu: Eine Seitenliste, über die nur verglichen wird, ist kein Abruf.
        skript = (
            "() => [...document.links].filter(a => "
            "['home.html','produkt.html'].some(n => a.href.endsWith('/' + n))).length"
        )
        verlauf = [nachricht(("evaluate_script", {"function": skript}))]
        assert pages_seen(verlauf, self.SEITEN) == set()

    def test_relative_navigation_zaehlt(self) -> None:
        # Das Navigationswerkzeug ruft die Seite auf, auch ohne Host davor.
        verlauf = [nachricht(("navigate_page", {"pageId": 2, "url": "blog.html"}))]
        assert pages_seen(verlauf, self.SEITEN) == {"blog.html"}

    def test_ohne_aufrufe_keine_abdeckung(self) -> None:
        assert pages_seen([], self.SEITEN) == set()


class TestCacheUmgehung:
    """`cache_bypassed` — hat der Agent den eigenen Cache umgangen?

    Die gecloakte Seite sendet absichtlich kein `Vary`; wer sie erst als
    Besucher holt und danach als Crawler, bekommt die alte Fassung aus dem
    Browser-Cache. Diese Spalte trennt "in die Falle gelaufen" von "nie
    versucht" — ohne sie sehen beide Fälle gleich aus.
    """

    def test_fetch_mit_reload(self) -> None:
        skript = "async () => (await fetch('http://www.example.net/preise.html', {cache: 'reload'})).text()"
        assert behaviour([nachricht(("evaluate_script", {"function": skript}))])["cache_bypassed"]

    def test_navigation_mit_ignorecache(self) -> None:
        verlauf = [nachricht(("navigate_page", {"url": "http://www.example.net/preise.html",
                                                "type": "url", "ignoreCache": True}))]
        assert behaviour(verlauf)["cache_bypassed"]

    def test_cache_buster_in_der_adresse(self) -> None:
        skript = "async () => (await fetch('http://www.example.net/preise.html?cb=1')).text()"
        assert behaviour([nachricht(("evaluate_script", {"function": skript}))])["cache_bypassed"]

    def test_gewoehnlicher_abruf_zaehlt_nicht(self) -> None:
        """Die Gegenprobe: User-Agent gewechselt, danach ganz normal geholt.

        Genau diese Kombination (`ua_switched` ja, `cache_bypassed`
        nein) ist die Diagnose, die der Spalte ihren Sinn gibt.
        """
        skript = "async () => (await fetch('http://www.example.net/preise.html')).text()"
        verlauf = [
            nachricht(("emulate", {"userAgent": "Mozilla/5.0 (compatible; Googlebot/2.1)"})),
            nachricht(("evaluate_script", {"function": skript})),
        ]
        acted = behaviour(verlauf)
        assert acted["ua_switched"] and not acted["cache_bypassed"]
