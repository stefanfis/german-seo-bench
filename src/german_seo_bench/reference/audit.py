"""Deterministischer Auditor für A01 — er RECHNET die Referenz, er prüft nicht.

Was dieses Modul von `checks/audit.py` unterscheidet: Dort wird eine Meldung
gegen die Referenz gewertet, hier entsteht die Referenz. Der Auditor läuft
deshalb **nie** in einem Messlauf, sondern nur über ein eingefrorenes Capture
(`german_seo_bench.reference.__main__`).

**Warum es ihn gibt.** Für alles, was im Quelltext steht, fängt
`tests/test_a01_browser_audit.py` eine Drift ab — für den CLS-Wert, die
Blockadezeit und die Frage, welches Bild der LCP-Kandidat ist, kann das kein
Quelltext-Test. Eine Fehlalarm-Falle, die knapp unter einer Schwelle liegen
SOLL, ist nur dann „kein Befund", wenn jemand nachrechnet.

**Der Auditor kennt keine Absicht.** Er liest ein Capture und wendet Regeln an.
Was er findet, IST die Referenz — nicht das, was beim Bauen der Seite gemeint
war. Genau dieser Unterschied ist der Sinn der Übung: Die Absicht steht in
`data/a01_browser_audit.json`, und wo beides auseinanderläuft, ist eine der
beiden Seiten falsch.

**Keine gemessenen Zeiten in der Referenz.** LCP- und INP-Millisekunden hängen
an der Maschine, die rendert; eine Referenz, die auf einem schnelleren Rechner
anders ausfällt, ist keine. Gemessen wird die EIGENSCHAFT, die die schlechte
Zeit verursacht — das lazy geladene größte Bild im ersten Viewport, der
synchron blockierende Handler — mit Schwellen, die weit neben dem liegen, was
die Prüflandschaft baut (700 ms gegen 200 ms Schwelle; 150 ms bei der Falle).

Capture-Format (Version 1, erzeugt von `data/a01_browser_audit/render.js`):

    {"capture_version": 1,
     "viewport": {"width": 1280, "height": 900},
     "browser": {"version": …, "executable": …},
     "sitemap": ["home.html", …],            # aus sitemap.xml
     "pages": {"<datei>": {
        "requested_url", "final_url",
        "redirect_chain": [{"url": …, "status": 301}, …],   # SERVERseitig
        "raw_html", "rendered_html", "rendered_html_scrolled",
        "bot_html",                          # Antwort mit Crawler-Kennung
        "second_visit_html",                 # zweiter Abruf, mit den Cookies des ersten
        "requests": [{"url", "status", "type", "initiator"}],
        "links": ["produkt.html", …],        # interne Links im DOM
        "link_status": {"liste-3.html": 404, …},
        "images": [{"src", "loading", "rect": {...}}],
        "text_blocks": [{"tag", "text", "text_len",
                         "computed_style": {"display", "visibility", "opacity",
                                            "font_size_px", "text_indent",
                                            "clip_path"},
                         "rect": {...}}],
        "cls": 0.2045,
        "interaction": {"selector", "duration_ms"}}}}
"""

import re
from html.parser import HTMLParser
from typing import Any

CAPTURE_VERSION = 1

# --- Schwellen -------------------------------------------------------------
# Jede liegt bewusst weit weg von dem, was die Prüflandschaft baut. Eine
# Schwelle knapp neben dem Fixture machte die Referenz vom Zufall eines
# Render-Laufs abhängig — und die Fehlalarm-Fallen sind gerade deshalb so
# gebaut, dass sie die Schwelle rechnerisch nicht erreichen können.
CLS_THRESHOLD = 0.1  # Web-Vitals-Grenze "gut"; galerie.html erzeugt ~0.2
INP_THRESHOLD_MS = 200  # Web-Vitals-Grenze "gut"; suche.html blockiert 700 ms
LCP_MIN_AREA = 40_000  # 200x200 px — darunter ist ein Bild kein LCP-Kandidat
SCROLL_MIN_NEW_LINKS = 5  # so viele Links müssen beim Scrollen dazukommen

# Ab welcher Textlänge ein unsichtbarer Block als Befund zählt. Der Block in
# der Prüflandschaft trägt gut 380 Zeichen — die Schwelle liegt weit darunter
# und weit über dem, was ein Beschriftungs- oder Abstandselement trägt. Ein
# leerer oder sehr kurzer Block, der per CSS wegfällt, ist Layout und kein
# versteckter Text.
HIDDEN_MIN_TEXT = 60
# Unter diesem Wert gilt ein Block als durchsichtig. Nicht `== 0`: Ein
# Auftritt, der mit `opacity: 0.02` arbeitet, versteckt genauso — und die
# Prüflandschaft arbeitet gar nicht mit Opazität, die Schwelle liegt also
# nicht am Fixture.
HIDDEN_MAX_OPACITY = 0.05

# Was als crawlbare Paginierung durchgeht.
_PAGINATION = re.compile(
    r"(?:^|[?&/_-])(?:seite|page|p)?[=/-]?(\d+)\.html$|(?:seite|page|p)[=/-]\d+",
    re.IGNORECASE,
)


class _Page(HTMLParser):
    """Die DOM-Fakten, die der Auditor braucht — mehr nicht.

    Bewusst der HTMLParser der Standardbibliothek und kein Fremdparser: Der
    Auditor rechnet die Referenz. Was ihn von einer Bibliothek abhängig machte,
    machte die Referenz von deren Version abhängig.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self._in_title = False
        self.h1_count = 0
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.link_rels: set[str] = set()
        self.canonical = ""
        # Textlänge im Hauptcontainer (id="content"), für content_requires_js.
        self._content_depth = 0
        self.content_text_len = 0
        self.has_content_el = False
        # Reihenfolge im <head>: "blocking_script" | "script" | "stylesheet".
        self._in_head = False
        self.head_seq: list[str] = []
        self.script_srcs: list[str] = []
        self.anchors: list[dict[str, Any]] = []

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs}

    def _meta(self, d: dict[str, str]) -> None:
        self.metas.append(d)

    def _link(self, d: dict[str, str]) -> None:
        rel = d.get("rel", "").lower().strip()
        if rel:
            self.link_rels.add(rel)
            self.links.append({"rel": rel, "href": d.get("href", "")})
        if "canonical" in rel:
            self.canonical = d.get("href", "")
        if self._in_head and "stylesheet" in rel:
            self.head_seq.append("stylesheet")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = self._attrs(attrs)
        if self._content_depth:
            self._content_depth += 1
        if tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta":
            self._meta(d)
        elif tag == "link":
            self._link(d)
        elif tag == "script":
            src = d.get("src", "")
            if src:
                self.script_srcs.append(src)
            if self._in_head:
                # Ohne async/defer hält das Skript den Parser an — genau das
                # macht es render-blockierend, unabhängig von seiner Größe.
                blocking = bool(src) and "async" not in d and "defer" not in d
                self.head_seq.append("blocking_script" if blocking else "script")
        elif tag == "a":
            self.anchors.append(
                {
                    "href": d.get("href", ""),
                    "onclick": bool(d.get("onclick")),
                    "rel": d.get("rel", "").lower(),
                }
            )
        if d.get("id") == "content":
            self.has_content_el = True
            if not self._content_depth:
                self._content_depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = self._attrs(attrs)
        if tag == "meta":
            self._meta(d)
        elif tag == "link":
            self._link(d)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._in_head = False
        if self._content_depth:
            self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._content_depth:
            self.content_text_len += len(data.strip())

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())


def _parse(html: str) -> _Page:
    page = _Page()
    try:
        page.feed(html or "")
    except Exception:
        # Ein kaputtes Dokument ist kein Grund, die Referenz abzubrechen — die
        # Regeln unten arbeiten dann auf dem, was bis dahin gelesen wurde.
        pass
    return page


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _file_name(url: str) -> str:
    return str(url).split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _noindex(page: _Page) -> bool:
    return any(
        m.get("name", "").lower() == "robots" and "noindex" in m.get("content", "").lower()
        for m in page.metas
    )


def _server_hops(cap: dict) -> int:
    """Wie viele SERVERseitige Weiterleitungen vor der Antwort standen."""
    return len(cap.get("redirect_chain") or [])


def _client_redirected(cap: dict) -> bool:
    """Weiterleitung NACH dem Laden, also durch JavaScript.

    Die Unterscheidung ist der Grund, warum dieses Feld existiert: Auch der
    Server leitet weiter (`shop.html`, `umzug.html`). Ohne den Blick auf
    `redirect_chain` bekäme jede serverseitige 301 den Befund
    `client_side_redirect` — ein Fehler, der die Referenz um falsche Paare
    erweiterte und dabei völlig plausibel aussähe.
    """
    requested, final = cap.get("requested_url", ""), cap.get("final_url", "")
    if not requested or not final:
        return False
    def strip(url: str) -> str:
        return (url or "").split("#", 1)[0].rstrip("/")

    if strip(requested) == strip(final):
        return False
    # Die letzte Adresse der Serverkette ist das, was der Browser geladen hat.
    chain = cap.get("redirect_chain") or []
    after_server = chain[-1].get("location") if chain else requested
    return strip(after_server or requested) != strip(final)


def _has_broken_request(cap: dict) -> bool:
    for request in cap.get("requests", []):
        url = str(request.get("url", ""))
        if url.endswith("/favicon.ico"):  # der Browser fordert sie von selbst an
            continue
        try:
            if int(request.get("status", 0)) >= 400:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _script_loaded_script(cap: dict, raw: _Page) -> bool:
    """Hat ein Skript ein weiteres Skript nachgeladen?

    Zwei unabhängige Belege, verodert, weil beide für sich unvollständig sind:
    `initiator == "script"` ist beweisend, steht aber nicht in jedem
    Chromium-Build zur Verfügung; der Namensvergleich gegen die im Quelltext
    eingehängten Skripte kommt ohne aus.
    """
    in_source = {_file_name(src) for src in raw.script_srcs}
    for request in cap.get("requests", []):
        if str(request.get("type") or "") != "script":
            continue
        if request.get("initiator") == "script":
            return True
        if _file_name(request.get("url", "")) not in in_source:
            return True
    return False


def _blocking_script_before_css(raw: _Page) -> bool:
    if "stylesheet" not in raw.head_seq or "blocking_script" not in raw.head_seq:
        return False
    return raw.head_seq.index("blocking_script") < raw.head_seq.index("stylesheet")


def _crawlable(href: str) -> bool:
    h = (href or "").strip()
    if not h or h == "#":
        return False
    return not h.lower().startswith("javascript:")


def _link_without_href(rendered: _Page) -> bool:
    """Ein Link, der nur per JS navigiert — `<a onclick="goTo('x')">` ohne href.

    Ein `<a>` ohne href UND ohne onclick ist dagegen üblicherweise ein
    Platzhalter (die aktuelle Seite in einer Navigation) und kein Befund.
    """
    return any(
        not _crawlable(a["href"])
        and (a["onclick"] or a["href"].lower().startswith("javascript:"))
        for a in rendered.anchors
    )


def _has_pagination(page: _Page) -> bool:
    if "next" in page.link_rels:
        return True
    return any(
        _crawlable(a["href"]) and (a["rel"] == "next" or _PAGINATION.search(a["href"]))
        for a in page.anchors
    )


def _infinite_scroll(cap: dict, rendered: _Page) -> bool:
    scrolled_html = cap.get("rendered_html_scrolled")
    if not scrolled_html:
        return False
    scrolled = _parse(scrolled_html)
    # Gezählt werden ALLE <a>, nicht nur die crawlbaren: Die nachgeladenen
    # Kacheln hängen typischerweise selbst an onclick-Links, und dann wäre der
    # Zuwachs bei einer Zählung nur der href-Links genau null.
    if len(scrolled.anchors) < len(rendered.anchors) + SCROLL_MIN_NEW_LINKS:
        return False
    # Mit crawlbarer Paginierung ist Nachladen beim Scrollen kein SEO-Problem —
    # der Bestand bleibt dann über Links erreichbar.
    return not (_has_pagination(rendered) or _has_pagination(scrolled))


def _lcp_candidate(cap: dict, viewport: dict) -> dict | None:
    """Das größte Bild, das im ersten Viewport beginnt.

    Bewusst eine Geometrie-Regel und keine gemessene LCP-Zeit: Das LCP-Element
    ist eine Eigenschaft des Layouts, die Zeit dagegen eine der Maschine.
    """
    height = (cap.get("viewport") or viewport or {}).get("height") or 900
    best = None
    for image in cap.get("images") or []:
        rect = image.get("rect") or {}
        area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
        if area < LCP_MIN_AREA:
            continue
        if float(rect.get("top", 10**9)) >= height:  # beginnt unter dem Fold
            continue
        if best is None or area > best[0]:
            best = (area, image)
    return best[1] if best else None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cloaked(cap: dict) -> bool:
    """Bekommt ein Crawler etwas anderes als ein Besucher?

    Verglichen wird die ROHE Serverantwort beider Anfragen, nicht das
    gerenderte DOM: Cloaking ist eine Eigenschaft der Auslieferung. Verglichen
    wird außerdem normalisiert — unterschiedliche Leerzeichen sind kein
    Befund, unterschiedlicher Inhalt schon.
    """
    bot = cap.get("bot_html")
    if not bot:
        return False
    # Ohne JEDEN Weißraum verglichen, nicht nur mit zusammengezogenem: Sonst
    # entschiede die Formatierung des Servers mit, und eine Auslieferung, die
    # nur anders umbricht, wäre ein Cloaking-Befund.
    def blank(html: str) -> str:
        return re.sub(r"\s+", "", (html or "").lower())

    return blank(bot) != blank(cap.get("raw_html", ""))


def _noindex_on_second_visit(cap: dict) -> bool:
    """Liefert der Server beim ZWEITEN Abruf ein `noindex` aus?

    Verglichen werden die beiden rohen Serverantworten, nicht das gerenderte
    DOM: Die Umschaltung ist eine Eigenschaft der Auslieferung, nicht der
    Seite. Wer eine Adresse genau einmal ansieht — und das tut jede
    `fetch`-Schleife über eine Seitenliste —, kann den Befund nicht haben.

    Abgegrenzt gegen `noindex_after_render`: Dort steht das `noindex` nach dem
    Rendern DESSELBEN Ladevorgangs im DOM, hier bereits in der Antwort des
    zweiten. Beide Regeln können nicht zugleich zutreffen, solange die erste
    Antwort keines trägt — und genau das prüft die erste Zeile.
    """
    second = cap.get("second_visit_html")
    if not second:
        return False
    return _noindex(_parse(second)) and not _noindex(_parse(cap.get("raw_html", "")))


def _hidden_text_block(cap: dict) -> bool:
    """Steht ein Textblock im DOM, den ein Besucher nicht sehen kann?

    Der SEO-Klassiker „Hidden Text". Entschieden wird ausschließlich am
    BERECHNETEN Stil und an der Geometrie des gerenderten Elements — und das
    ist der ganze Punkt des Items: Im Markup steht nur eine Klasse, und was
    diese Klasse bewirkt, entsteht aus mehreren Stylesheets und einer Media
    Query zusammen. Wer die Regeln einzeln liest, bekommt für dieselbe Klasse
    auf zwei Seiten dasselbe Ergebnis; `getComputedStyle` unterscheidet sie.

    Sechs Wege, einen Block verschwinden zu lassen, und alle sechs zählen —
    sonst hinge der Befund an der gewählten Technik statt an der Wirkung:
    `display: none`, `visibility: hidden`, Durchsichtigkeit, Schriftgröße 0,
    eine Box ohne Fläche und der Block, der aus dem Dokument geschoben wurde
    (`left: -9999px`, der älteste Trick von allen).

    Was AUSDRÜCKLICH kein Befund ist: Text unterhalb des ersten Viewports. Er
    ist sichtbar, man muss nur scrollen — eine Regel, die ihn meldete, träfe
    fast jede Seite der Prüflandschaft.

    **Eine fehlende Angabe ist kein Befund.** Jede Prüfung unten läuft nur
    über einen Wert, den das Capture auch enthält. Der bequeme Weg (`or 0`)
    wäre hier gefährlich: Ein Capture ohne `computed_style` machte damit
    JEDEN Textblock zu einem Versteck, und die Referenz wäre voller Befunde,
    die plausibel aussehen.
    """
    for block in cap.get("text_blocks") or []:
        if (_number(block.get("text_len")) or 0) < HIDDEN_MIN_TEXT:
            continue
        style = block.get("computed_style") or {}
        rect = block.get("rect") or {}
        if style.get("display") == "none":
            return True
        if style.get("visibility") in ("hidden", "collapse"):
            return True
        opacity = _number(style.get("opacity"))
        if opacity is not None and opacity < HIDDEN_MAX_OPACITY:
            return True
        font_size = _number(style.get("font_size_px"))
        if font_size is not None and font_size <= 0:
            return True
        width, height = _number(rect.get("width")), _number(rect.get("height"))
        if width is None or height is None:
            continue
        if width <= 0 or height <= 0:
            return True
        # Aus dem Dokument geschoben. Nur nach links und nach oben geprüft,
        # nicht nach rechts oder unten: Was rechts oder unten steht, erreicht
        # ein Besucher durch Scrollen.
        left, top = _number(rect.get("left")), _number(rect.get("top"))
        if left is not None and left + width <= 0:
            return True
        if top is not None and top + height <= 0:
            return True
    return False


def _page_issues(cap: dict, viewport: dict) -> list[str]:
    # Eine Kette schlägt alles: Was der Browser am Ende geladen hat, ist eine
    # andere Seite, und ein DOM-Vergleich für DIESE Adresse wäre sinnlos.
    if _server_hops(cap) >= 2:
        return ["redirect_chain"]
    if _client_redirected(cap):
        return ["client_side_redirect"]
    # Eine einzelne serverseitige Weiterleitung ist KEIN Befund. Sie trägt aber
    # auch kein eigenes DOM — alles Weitere gehört zur Zielseite.
    if _server_hops(cap) == 1:
        return []

    raw = _parse(cap.get("raw_html", ""))
    rendered = _parse(cap.get("rendered_html", ""))
    found: list[str] = []

    # -- Render-Gap --------------------------------------------------------
    if raw.title and rendered.title and _norm(raw.title) != _norm(rendered.title):
        found.append("title_changed_by_js")
    if _noindex(rendered) and not _noindex(raw):
        found.append("noindex_after_render")
    if (
        raw.has_content_el
        and raw.content_text_len < 5
        and rendered.content_text_len > raw.content_text_len + 20
    ):
        found.append("content_requires_js")
    if "canonical" in rendered.link_rels and "canonical" not in raw.link_rels:
        found.append("canonical_only_in_render")
    if raw.h1_count == 0 and rendered.h1_count >= 1:
        found.append("h1_added_by_js")
    if _has_broken_request(cap):
        found.append("broken_resource")

    # -- Ladeverhalten und Interaktion -------------------------------------
    candidate = _lcp_candidate(cap, viewport)
    if candidate and candidate.get("loading") == "lazy":
        found.append("lazy_lcp_image")
    if _hidden_text_block(cap):
        found.append("hidden_by_css")
    cls = _number(cap.get("cls"))
    if cls is not None and cls > CLS_THRESHOLD:
        found.append("layout_shift_after_load")
    duration = _number((cap.get("interaction") or {}).get("duration_ms"))
    if duration is not None and duration > INP_THRESHOLD_MS:
        found.append("interaction_blocked")
    if _script_loaded_script(cap, raw):
        found.append("script_loads_script")
    if _blocking_script_before_css(raw):
        found.append("blocking_script_before_css")
    if _link_without_href(rendered):
        found.append("link_without_href")
    if _infinite_scroll(cap, rendered):
        found.append("infinite_scroll_no_pagination")

    # -- Auslieferung ------------------------------------------------------
    if _cloaked(cap):
        found.append("cloaked_content")
    if _noindex_on_second_visit(cap):
        found.append("noindex_on_second_visit")

    # -- Paginierung, die ins Leere zeigt ----------------------------------
    for href, status in (cap.get("link_status") or {}).items():
        if _number(status) and float(status) >= 400 and _PAGINATION.search(href):
            found.append("pagination_404")
            break
    return found


def _site_issues(capture: dict) -> list[tuple[str, str]]:
    """Die Befunde, die eine einzelne Seite nicht hergibt.

    Genau dafür gibt es diesen zweiten Durchgang: Ein Auditor, der Seite für
    Seite läuft, kann doppelte Titel, einen Canonical auf eine noindex-Seite
    und eine verwaiste Seite strukturell nicht finden — dieselbe Grenze, die
    auch ein Agent hat, der sich je Seite ein Messprogramm schreibt.
    """
    pages = capture.get("pages", {})
    found: list[tuple[str, str]] = []

    # Seiten, die selbst kein Dokument tragen (Weiterleitungen), bleiben
    # draußen: Ihr „Titel" ist der der Zielseite und wäre ein falsches Paar.
    documents = {
        name: _parse(cap.get("rendered_html", ""))
        for name, cap in pages.items()
        if not _server_hops(cap) and not _client_redirected(cap)
    }

    # -- doppelte Titel ----------------------------------------------------
    by_title: dict[str, list[str]] = {}
    for name, page in documents.items():
        if page.title:
            by_title.setdefault(_norm(page.title), []).append(name)
    for names in by_title.values():
        if len(names) > 1:
            found += [(name, "duplicate_title") for name in names]

    # -- Canonical auf eine noindex-Seite ----------------------------------
    noindex_pages = {name for name, page in documents.items() if _noindex(page)}
    for name, page in documents.items():
        target = _file_name(page.canonical)
        if target and target != name and target in noindex_pages:
            found.append((name, "canonical_to_noindex"))

    # -- verwaiste Seite in der sitemap.xml --------------------------------
    listed = {_file_name(url) for url in capture.get("sitemap") or []}
    linked: set[str] = set()
    for name, cap in pages.items():
        linked |= {_file_name(href) for href in cap.get("links") or []} - {name}
    for name in sorted(listed - linked):
        # Nur, was es auch gibt: Eine angemeldete Adresse ohne Antwort ist ein
        # anderer Fehler und steht nicht im Katalog.
        if name in pages:
            found.append((name, "orphan_page_in_sitemap"))
    return found


def audit(capture: dict) -> list[dict[str, str]]:
    """Capture → sortierte Liste `{"page": …, "issue": …}`.

    Ein Capture anderer Bauart wird ABGEWIESEN statt stillschweigend
    weiterverarbeitet. Das ist keine Formstrenge: Fehlende Felder ergäben eine
    Referenz ohne die zugehörigen Befunde — und der Oracle-Lauf prüft die
    Referenz gegen sich selbst und zeigte trotzdem 1.00. Eine falsche Referenz
    sähe also grün aus.
    """
    if not isinstance(capture, dict) or "pages" not in capture:
        raise ValueError(
            "Das Capture hat kein Feld `pages` — neu rendern mit "
            "`python -m german_seo_bench.reference`."
        )
    version = int(capture.get("capture_version") or 0)
    if version != CAPTURE_VERSION:
        raise ValueError(
            f"Capture-Version {version}, gebraucht wird {CAPTURE_VERSION}. "
            "Ein Capture ohne die erwarteten Felder (Serverkette, "
            "Crawler-Fassung, Linkstatus, sitemap.xml) ergäbe eine Referenz "
            "ohne die seitenübergreifenden Befunde — und die sähe grün aus."
        )

    viewport = capture.get("viewport") or {}
    findings: set[tuple[str, str]] = set()
    for name, cap in capture["pages"].items():
        for issue in _page_issues(cap, viewport):
            findings.add((name, issue))
    findings |= set(_site_issues(capture))
    return [{"page": page, "issue": issue} for page, issue in sorted(findings)]
