"""Der Auditor, der die Referenz rechnet — Regel für Regel, mit Gegenprobe.

Warum diese Datei die wichtigste der drei Testdateien um A01 ist: Was hier
falsch ist, ist danach die Wahrheit. `tests/test_a01_browser_audit.py` prüft,
ob die abgelegte Referenz zur Prüflandschaft passt; `tests/test_audit_checks.py`
prüft, wie eine Meldung dagegen gewertet wird. Hier entsteht die Referenz
selbst — ein Fehler an dieser Stelle macht alle drei Ebenen einig und alle drei
falsch.

Gearbeitet wird mit gebauten Captures, nicht mit gerenderten: Jede Regel
bekommt genau die Felder, um die es geht, und dazu den Fall, in dem sie NICHT
anschlagen darf. Ein Browser wäre hier kein Gewinn, sondern eine zweite
Fehlerquelle — und der Test liefe nur noch auf einer Maschine mit Docker.
"""

import pytest

from german_seo_bench.reference.audit import CAPTURE_VERSION, audit


def seite(html: str | None = None, **felder) -> dict:
    """Eine Seite im Capture. Alles, was nicht genannt ist, ist unauffällig.

    `html=` setzt ausgeliefertes UND gerendertes Dokument auf denselben Text —
    die Seite hat dann keinen Render-Gap. Wer nur eines von beiden setzt, baut
    einen: genau das prüfen die Tests zum Render-Gap.
    """
    if html is not None:
        felder.setdefault("raw_html", html)
        felder.setdefault("rendered_html", html)
    grund = {
        "requested_url": "http://www.example.net/x.html",
        "final_url": "http://www.example.net/x.html",
        "redirect_chain": [],
        "raw_html": "<html><head><title>X</title></head><body><h1>X</h1></body></html>",
        "rendered_html": "<html><head><title>X</title></head><body><h1>X</h1></body></html>",
        "rendered_html_scrolled": None,
        "bot_html": None,
        "second_visit_html": None,
        "requests": [],
        "images": [],
        "text_blocks": [],
        "cls": 0.0,
        "interaction": None,
        "links": [],
        "link_status": {},
    }
    grund.update(felder)
    return grund


def capture(pages: dict, **oben) -> dict:
    """Ein Capture aus fertigen Seiten.

    Der Platzhalter-Titel `X` wird dabei je Seite durch den Dateinamen ersetzt:
    Sonst trügen alle Seiten mit Vorgabewerten denselben Titel, und
    `duplicate_title` schlüge in jedem Test an, der mehr als eine Seite baut —
    ein Testartefakt, das wie ein echter Befund aussieht.
    """
    for name, cap in pages.items():
        for feld in ("raw_html", "rendered_html"):
            if cap.get(feld) and "<title>X</title>" in cap[feld]:
                cap[feld] = cap[feld].replace("<title>X</title>", f"<title>{name}</title>")
    doc = {
        "capture_version": CAPTURE_VERSION,
        "viewport": {"width": 1280, "height": 900},
        "sitemap": [],
        "pages": pages,
    }
    doc.update(oben)
    return doc


def befunde(doc: dict) -> set[tuple[str, str]]:
    return {(row["page"], row["issue"]) for row in audit(doc)}


class TestCaptureVersion:
    def test_fremde_capture_version_wird_abgewiesen(self) -> None:
        """Nicht aus Formstrenge: Ein Capture ohne die erwarteten Felder ergäbe
        eine Referenz ohne die seitenübergreifenden Befunde — und die sähe im
        Oracle-Lauf grün aus."""
        with pytest.raises(ValueError, match="Capture-Version"):
            audit({"capture_version": 2, "pages": {}})

    def test_ohne_seiten_kein_capture(self) -> None:
        with pytest.raises(ValueError, match="pages"):
            audit({"capture_version": CAPTURE_VERSION})

    def test_render_js_schreibt_dieselbe_version(self) -> None:
        """Die Version steht zweimal: im Renderer und im Auditor.

        Laufen beide auseinander, weist der Auditor jedes frische Capture ab —
        und das fiele erst beim nächsten Docker-Lauf auf, nicht hier.
        """
        import re
        from pathlib import Path

        from german_seo_bench.dataset import data_path

        quelle = Path(data_path("a01_browser_audit/render.js")).read_text(encoding="utf-8")
        treffer = re.search(r"^const CAPTURE_VERSION = (\d+);", quelle, re.MULTILINE)
        assert treffer, "render.js setzt CAPTURE_VERSION nicht mehr als Konstante"
        assert int(treffer.group(1)) == CAPTURE_VERSION


class TestWeiterleitungen:
    """Server- gegen Client-Weiterleitung.

    Die Regel „Zieladresse ungleich Ausgangsadresse heißt
    `client_side_redirect`“ wäre falsch, weil die testsite selbst weiterleitet:
    Jede 301 bekäme einen falschen Befund — und zwar einen, der völlig
    plausibel aussieht.
    """

    def test_javascript_leitet_weiter(self) -> None:
        doc = capture({"alt.html": seite(
            requested_url="http://www.example.net/alt.html",
            final_url="http://www.example.net/home.html",
        )})
        assert ("alt.html", "client_side_redirect") in befunde(doc)

    def test_eine_richtige_301_ist_kein_befund(self) -> None:
        doc = capture({"shop.html": seite(
            requested_url="http://www.example.net/shop.html",
            final_url="http://www.example.net/home.html",
            redirect_chain=[{
                "url": "http://www.example.net/shop.html",
                "status": 301,
                "location": "http://www.example.net/home.html",
            }],
        )})
        assert befunde(doc) == set()

    def test_zwei_spruenge_sind_eine_kette(self) -> None:
        doc = capture({"umzug.html": seite(
            requested_url="http://www.example.net/umzug.html",
            final_url="http://www.example.net/kategorie.html",
            redirect_chain=[
                {"url": "http://www.example.net/umzug.html", "status": 301,
                 "location": "http://www.example.net/umzug-zwischen.html"},
                {"url": "http://www.example.net/umzug-zwischen.html", "status": 301,
                 "location": "http://www.example.net/kategorie.html"},
            ],
        )})
        assert befunde(doc) == {("umzug.html", "redirect_chain")}

    def test_die_kette_erbt_nicht_die_befunde_des_ziels(self) -> None:
        # Sonst bekäme `umzug.html` alles, was `kategorie.html` hat — und in
        # der Tabelle stünde ein Befund auf einer Adresse, die ihn nicht hat.
        doc = capture({"umzug.html": seite(
            redirect_chain=[
                {"url": "http://www.example.net/umzug.html", "status": 301,
                 "location": "http://www.example.net/umzug-zwischen.html"},
                {"url": "http://www.example.net/umzug-zwischen.html", "status": 301,
                 "location": "http://www.example.net/kategorie.html"},
            ],
            rendered_html="<html><head><title>Anders</title></head><body></body></html>",
            cls=0.9,
        )})
        assert befunde(doc) == {("umzug.html", "redirect_chain")}


class TestSchwellen:
    """Die drei Zahlen, an denen die Fehlalarm-Fallen hängen."""

    def test_verschiebung_ueber_der_schwelle(self) -> None:
        assert ("g.html", "layout_shift_after_load") in befunde(
            capture({"g.html": seite(cls=0.2045)})
        )

    def test_verschiebung_knapp_darunter_ist_kein_befund(self) -> None:
        # Die Falle auf `sale.html`: sichtbar, aber unter der Schwelle.
        assert befunde(capture({"sale.html": seite(cls=0.069)})) == set()

    def test_blockade_ueber_der_schwelle(self) -> None:
        doc = capture({"suche.html": seite(interaction={"duration_ms": 712})})
        assert ("suche.html", "interaction_blocked") in befunde(doc)

    def test_blockade_knapp_darunter_ist_kein_befund(self) -> None:
        # Die Falle auf `liste.html`: 150 ms sind spürbar und trotzdem in Ordnung.
        doc = capture({"liste.html": seite(interaction={"duration_ms": 150})})
        assert befunde(doc) == set()

    def test_lazy_bild_im_ersten_viewport(self) -> None:
        doc = capture({"galerie.html": seite(images=[
            {"src": "hero.svg", "loading": "lazy",
             "rect": {"top": 120, "left": 0, "width": 1200, "height": 600}},
        ])})
        assert ("galerie.html", "lazy_lcp_image") in befunde(doc)

    def test_lazy_bild_unterhalb_des_ersten_viewports(self) -> None:
        """Richtig eingesetztes Lazy Loading — die Falle auf `poster.html`.

        Wer den Befund am Attribut festmacht statt an der Geometrie, meldet
        hier falsch. Genau deshalb steht die Regel auf `rect.top`.
        """
        doc = capture({"poster.html": seite(images=[
            {"src": "hero.svg", "loading": "",
             "rect": {"top": 100, "left": 0, "width": 1200, "height": 600}},
            {"src": "foto.svg", "loading": "lazy",
             "rect": {"top": 1400, "left": 0, "width": 300, "height": 200}},
        ])})
        assert befunde(doc) == set()

    def test_die_gerenderte_groesse_entscheidet_nicht_das_attribut(self) -> None:
        """Der zweite `lazy_lcp_image`-Befund — `zubehoer.html`.

        Das nach ATTRIBUT größte Bild (1200x600) ist nicht lazy und wird per
        CSS auf 220x110 geschrumpft; das lazy geladene trägt 300x200 im Markup
        und wird auf 900x600 aufgezogen. Eine Heuristik über die Attribute
        findet hier nichts — und liegt damit falsch.
        """
        doc = capture({"zubehoer.html": seite(images=[
            {"src": "foto.svg", "loading": "lazy",
             "rect": {"top": 114, "left": 16, "width": 900, "height": 600}},
            {"src": "hero.svg", "loading": "",
             "rect": {"top": 782, "left": 16, "width": 220, "height": 110}},
        ])})
        assert ("zubehoer.html", "lazy_lcp_image") in befunde(doc)

    def test_geschrumpftes_lazy_bild_ist_kein_kandidat(self) -> None:
        # Die Kehrseite: Wäre das geschrumpfte Bild das lazy geladene, gäbe es
        # keinen Befund — 220x110 liegt weit unter der Mindestfläche.
        doc = capture({"x.html": seite(images=[
            {"src": "hero.svg", "loading": "lazy",
             "rect": {"top": 114, "left": 16, "width": 220, "height": 110}},
        ])})
        assert befunde(doc) == set()

    def test_kleines_lazy_bild_ist_kein_lcp_kandidat(self) -> None:
        doc = capture({"p.html": seite(images=[
            {"src": "icon.svg", "loading": "lazy",
             "rect": {"top": 10, "left": 0, "width": 80, "height": 80}},
        ])})
        assert befunde(doc) == set()


def block(text_len: int = 400, **stil) -> dict:
    """Ein Textblock im Capture. Ohne Angabe ist er sichtbar.

    Die Vorgaben sind bewusst die eines ganz normalen Absatzes — jeder Test
    setzt genau die eine Eigenschaft, um die es ihm geht. Sonst prüfte man
    nicht die Regel, sondern die Vorgaben des Fixture-Bauers.
    """
    rect = {"top": 120, "left": 16, "width": 600, "height": 80}
    grund = {
        "display": "block",
        "visibility": "visible",
        "opacity": "1",
        "font_size_px": 16,
        "text_indent": "0px",
        "clip_path": "none",
    }
    # Geometrie und berechneter Stil kommen über dieselbe Signatur herein —
    # aufgeteilt wird nach Zugehörigkeit, damit ein Test `width=0` schreiben
    # kann, ohne den Aufbau des Captures zu kennen.
    for name, wert in stil.items():
        (rect if name in rect else grund)[name] = wert
    return {
        "tag": "p",
        "text": "Wandkarte drucken lassen, Weltkarte drucken, Landkarte drucken",
        "text_len": text_len,
        "computed_style": grund,
        "rect": rect,
    }


class TestVerstecktePerCss:
    """`hidden_by_css` — der SEO-Klassiker „Hidden Text", Klasse `render`.

    Warum die Regel am BERECHNETEN Stil hängt und nicht am Markup: In der
    Prüflandschaft steht auf beiden Seiten dieselbe Klasse am selben Element,
    und ob sie ausblendet, entscheidet erst die Kaskade aus zwei Stylesheets
    und einer Media Query. Eine Regel, die das Markup liest, fände entweder
    beide oder keine — und beides wäre falsch.
    """

    def test_visibility_hidden_ist_ein_befund(self) -> None:
        doc = capture({"technik.html": seite(text_blocks=[block(visibility="hidden")])})
        assert befunde(doc) == {("technik.html", "hidden_by_css")}

    def test_derselbe_block_sichtbar_ist_kein_befund(self) -> None:
        """Die Gegenprobe, die das Item überhaupt erst zu einem Item macht.

        `zubehoer.html` trägt dieselbe Klasse an derselben Stelle und ist
        sichtbar. Wer den Befund aus dem Markup erschließt, meldet hier falsch.
        """
        assert befunde(capture({"zubehoer.html": seite(text_blocks=[block()])})) == set()

    def test_display_none_ist_ein_befund(self) -> None:
        doc = capture({"x.html": seite(text_blocks=[block(display="none")])})
        assert ("x.html", "hidden_by_css") in befunde(doc)

    def test_durchsichtiger_block_ist_ein_befund(self) -> None:
        doc = capture({"x.html": seite(text_blocks=[block(opacity="0")])})
        assert ("x.html", "hidden_by_css") in befunde(doc)

    def test_leicht_blasser_block_ist_kein_befund(self) -> None:
        # Sonst wäre jede Abblendung ein Befund. Die Schwelle steht bei 0,05.
        doc = capture({"x.html": seite(text_blocks=[block(opacity="0.6")])})
        assert befunde(doc) == set()

    def test_schriftgroesse_null_ist_ein_befund(self) -> None:
        doc = capture({"x.html": seite(text_blocks=[block(font_size_px=0)])})
        assert ("x.html", "hidden_by_css") in befunde(doc)

    def test_box_ohne_flaeche_ist_ein_befund(self) -> None:
        doc = capture({"x.html": seite(text_blocks=[block(width=0)])})
        assert ("x.html", "hidden_by_css") in befunde(doc)

    def test_nach_links_geschobener_block_ist_ein_befund(self) -> None:
        doc = capture({"x.html": seite(text_blocks=[block(left=-9999, width=600)])})
        assert ("x.html", "hidden_by_css") in befunde(doc)

    def test_text_unterhalb_des_ersten_viewports_ist_kein_befund(self) -> None:
        """Die wichtigste Gegenprobe der Regel.

        Text unter dem Fold ist sichtbar, man muss nur scrollen. Eine Regel,
        die ihn meldete, träfe fast jede Seite der Prüflandschaft — und zwar
        völlig plausibel aussehend.
        """
        doc = capture({"ratgeber.html": seite(text_blocks=[block(top=2400)])})
        assert befunde(doc) == set()

    def test_kurzer_unsichtbarer_block_ist_kein_befund(self) -> None:
        # Ein ausgeblendetes Beschriftungs- oder Abstandselement ist Layout,
        # kein versteckter Text. Die Schwelle steht bei 60 Zeichen.
        doc = capture({"x.html": seite(text_blocks=[block(text_len=20, display="none")])})
        assert befunde(doc) == set()

    def test_ein_block_ohne_angaben_ist_kein_befund(self) -> None:
        """Eine fehlende Angabe darf nicht als „unsichtbar" durchgehen.

        Der bequeme Weg (`_number(...) or 0`) machte jeden Textblock eines
        Captures ohne `computed_style` zum Versteck — die Referenz wäre voller
        Befunde, die plausibel aussehen und die es nicht gibt.
        """
        doc = capture({"x.html": seite(text_blocks=[
            {"tag": "p", "text": "…", "text_len": 400,
             "computed_style": {}, "rect": {}}
        ])})
        assert befunde(doc) == set()

    def test_ohne_textbloecke_kein_befund(self) -> None:
        # Ein Capture ohne das Feld darf den Befund nicht erfinden — sonst
        # hinge die Referenz daran, ob der Renderer es gefüllt hat.
        doc = capture({"x.html": seite()})
        doc["pages"]["x.html"]["text_blocks"] = None
        assert befunde(doc) == set()


class TestCloaking:
    def test_andere_auslieferung_fuer_den_crawler(self) -> None:
        doc = capture({"preise.html": seite(
            raw_html="<html><head><title>Preise</title></head><body><p>39 Euro</p></body></html>",
            bot_html="<html><head><title>Wandkarten günstig</title></head>"
                     "<body><p>Weltkarte kaufen, Poster kaufen</p></body></html>",
            rendered_html="<html><head><title>Preise</title></head><body><p>39 Euro</p></body></html>",
        )})
        assert ("preise.html", "cloaked_content") in befunde(doc)

    def test_gleiche_auslieferung_ist_kein_befund(self) -> None:
        html = "<html><head><title>Preise</title></head><body><p>39 Euro</p></body></html>"
        doc = capture({"preise.html": seite(raw_html=html, bot_html=html, rendered_html=html)})
        assert befunde(doc) == set()

    def test_nur_andere_leerzeichen_sind_kein_befund(self) -> None:
        # Sonst hinge der Befund an der Formatierung des Servers statt am
        # Inhalt — und jede Seite wäre gecloakt, sobald nginx anders umbricht.
        html = "<html><head><title>Preise</title></head><body><p>39 Euro</p></body></html>"
        doc = capture({"preise.html": seite(
            raw_html=html, bot_html=html.replace("><", ">\n<"), rendered_html=html
        )})
        assert befunde(doc) == set()


class TestSeitenuebergreifend:
    def test_doppelte_titel_treffen_beide_seiten(self) -> None:
        doc = capture({
            "poster.html": seite(html="<title>Poster – Kartenshop</title>"),
            "poster-a3.html": seite(html="<title>Poster – Kartenshop</title>"),
            "blog.html": seite(html="<title>Blog – Kartenshop</title>"),
        })
        assert befunde(doc) == {
            ("poster.html", "duplicate_title"),
            ("poster-a3.html", "duplicate_title"),
        }

    def test_eine_weiterleitung_erbt_den_titel_des_ziels_nicht(self) -> None:
        """Sonst wäre jede Weiterleitung ein doppelter Titel.

        Der gerenderte Titel einer weitergeleiteten Adresse IST der der
        Zielseite — ein Vergleich über alle Seiten fände dort zwangsläufig ein
        Paar, und `duplicate_title` stünde auf zwei Adressen, von denen eine
        gar kein Dokument hat.
        """
        doc = capture({
            "home.html": seite(html="<title>Start</title>"),
            "shop.html": seite(
                html="<title>Start</title>",
                requested_url="http://www.example.net/shop.html",
                redirect_chain=[{
                    "url": "http://www.example.net/shop.html",
                    "status": 301,
                    "location": "http://www.example.net/home.html",
                }],
                final_url="http://www.example.net/home.html",
            ),
        })
        assert befunde(doc) == set()

    def test_canonical_auf_eine_noindex_seite(self) -> None:
        doc = capture({
            "sale.html": seite(
                html='<link rel="canonical" href="http://www.example.net/filter.html">'
            ),
            "filter.html": seite(
                html='<meta name="robots" content="noindex, follow">'
                     '<link rel="canonical" href="http://www.example.net/filter.html">'
            ),
        })
        assert befunde(doc) == {("sale.html", "canonical_to_noindex")}

    def test_canonical_auf_eine_indexierbare_seite(self) -> None:
        doc = capture({
            "sale.html": seite(
                html='<link rel="canonical" href="http://www.example.net/kategorie.html">'
            ),
            "kategorie.html": seite(html="<title>Karten</title>"),
        })
        assert befunde(doc) == set()

    def test_selbstverweisender_canonical_einer_noindex_seite(self) -> None:
        # Eine noindex-Seite, die auf sich selbst zeigt, ist in Ordnung — die
        # Falle auf `filter.html`. Ohne diese Ausnahme meldete der Auditor sie
        # selbst, und die Kontrollseite wäre keine.
        doc = capture({"filter.html": seite(
            html='<meta name="robots" content="noindex">'
                 '<link rel="canonical" href="http://www.example.net/filter.html">'
        )})
        assert befunde(doc) == set()

    def test_verwaiste_seite_in_der_sitemap(self) -> None:
        doc = capture(
            {
                "home.html": seite(links=["blog.html"]),
                "blog.html": seite(links=["home.html"]),
                # `presse.html` verlinkt selbst nach Hause — verwaist ist eine
                # Seite, auf die niemand zeigt, nicht eine ohne eigene Links.
                "presse.html": seite(links=["home.html"]),
            },
            sitemap=[
                "http://www.example.net/home.html",
                "http://www.example.net/blog.html",
                "http://www.example.net/presse.html",
            ],
        )
        assert befunde(doc) == {("presse.html", "orphan_page_in_sitemap")}

    def test_verlinkte_seite_ist_nicht_verwaist(self) -> None:
        doc = capture(
            {
                "home.html": seite(links=["presse.html"]),
                "presse.html": seite(links=["home.html"]),
            },
            sitemap=["http://www.example.net/home.html", "http://www.example.net/presse.html"],
        )
        assert befunde(doc) == set()

    def test_nicht_angemeldete_seite_ohne_link_ist_kein_befund(self) -> None:
        # Der Befund heißt „steht in der sitemap.xml, aber nirgends verlinkt".
        # Eine Seite, die auch nicht angemeldet ist, ist etwas anderes — und
        # steht nicht im Katalog.
        doc = capture(
            {"home.html": seite(), "presse.html": seite(links=["home.html"])},
            sitemap=["http://www.example.net/home.html"],
        )
        assert befunde(doc) == set()


class TestPaginierung:
    def test_paginierungslink_mit_404(self) -> None:
        doc = capture({"liste.html": seite(
            links=["liste-2.html", "liste-3.html"],
            link_status={"liste-2.html": 200, "liste-3.html": 404},
        )})
        assert ("liste.html", "pagination_404") in befunde(doc)

    def test_antwortende_paginierung_ist_kein_befund(self) -> None:
        doc = capture({"liste.html": seite(
            links=["liste-2.html", "liste-3.html"],
            link_status={"liste-2.html": 200, "liste-3.html": 200},
        )})
        assert befunde(doc) == set()

    def test_ein_kaputter_link_ohne_paginierungsform(self) -> None:
        # Ein beliebiger toter Link ist kein Paginierungsfehler. Die
        # Unterscheidung hält den Katalog geschlossen: Sonst hinge der Befund
        # an jedem Vertipper in einem href.
        doc = capture({"home.html": seite(
            links=["impressum.html"], link_status={"impressum.html": 404}
        )})
        assert befunde(doc) == set()


class TestZweitbesuch:
    """`noindex_on_second_visit` — die Klasse `second_load`.

    Der Befund existiert nur über zwei Ladevorgänge hinweg. Die meisten
    Befunde sind aus den ausgelieferten Bytes entscheidbar und lassen sich mit
    einer `fetch`-Schleife holen. Eine Schleife, die jede Adresse einmal
    anfasst, kann diesen hier nicht holen.
    """

    OHNE = "<html><head><title>Tipps</title></head><body><h1>Tipps</h1></body></html>"
    MIT = OHNE.replace("<title>", '<meta name="robots" content="noindex, follow"><title>')

    def test_zweiter_abruf_traegt_noindex(self) -> None:
        doc = capture({"tipps.html": seite(html=self.OHNE, second_visit_html=self.MIT)})
        assert befunde(doc) == {("tipps.html", "noindex_on_second_visit")}

    def test_gleicher_zweiter_abruf_ist_kein_befund(self) -> None:
        doc = capture({"tipps.html": seite(html=self.OHNE, second_visit_html=self.OHNE)})
        assert befunde(doc) == set()

    def test_ohne_zweiten_abruf_kein_befund(self) -> None:
        # Ein Capture ohne Zweitbesuch darf den Befund nicht erfinden — sonst
        # hinge die Referenz daran, ob der Renderer das Feld gefüllt hat.
        doc = capture({"tipps.html": seite(html=self.OHNE)})
        assert befunde(doc) == set()

    def test_dauerhaftes_noindex_ist_etwas_anderes(self) -> None:
        """Die Abgrenzung: Eine Seite, die IMMER noindex trägt, ist kein
        Zweitbesuch-Befund — sonst bekäme die Kontrollseite `filter.html` einen
        Befund, den sie nicht hat."""
        doc = capture({"filter.html": seite(html=self.MIT, second_visit_html=self.MIT)})
        assert befunde(doc) == set()
