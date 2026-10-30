"""Prüfungen, die sich allein aus der Zeichenkette ergeben.

Bewusst ohne Inspect-Import: Diese Funktionen sind reine Textlogik und lassen
sich ohne Modell, ohne Server und ohne Eval-Rahmen einzeln testen. Der Scorer
in `tasks/` setzt sie nur zusammen. Stünden Wertung und Prüflogik in einer
Datei, wäre eine Prüfung nur noch über einen kompletten Lauf zu testen.

`phrase_hit` ist die Regel, nach der in einem erzeugten Text gesucht wird:
**eine** Phrase aus einer Liste, an Wortgrenzen, buchstabengetreu. Sie gilt für
das Zielkeyword (`required`) und für die Sperrliste (`forbidden`) gleichermaßen.

Die Regel ist bewusst schlicht gehalten, auch um den Preis, dass einzelne
vertretbare Antworten durchfallen. Der Benchmark soll die grobe
Leistungsfähigkeit eines Modells zeigen und dabei nachvollziehbar bleiben; das
erreicht man besser über viele Aufgaben mit einer Regel, die jeder nachrechnen
kann, als über eine Regel, die in jedem Einzelfall fair sein will und die
niemand mehr überblickt. Teilwort-Treffer, geöffnete Flexionsendungen,
Umlautfaltung und mehrstufige Referenzen ergeben zusammen Toleranzen, deren
Zusammenwirken man einer Zahl nicht mehr ansieht.

`head_hit` lockert die Regel an genau einer Stelle: Ein Listenwort trifft
auch als Grundwort eines Kompositums ("Herbstkürbisse" trifft "Kürbisse"). Das
gilt nur für Motiv und Zusammenhang in M10, wo Bildbeschreibungen fast nur aus
Komposita bestehen; Sperrlisten und M01 suchen weiter mit `phrase_hit`.

M01 und M10 suchen sonst ausschließlich so. `fold` und `word_hit` sind die
tolerantere Fassung — Wortgrenzen ja, aber Umlaute aufgelöst und Schreibweise
egal. Sie tragen M02 und M03; `normalize` ist die Grundlage von `fold`.
"""

import re
import unicodedata
from typing import Iterable

_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]"
)

# Zeichen, die in einem <title> nichts verloren haben: Markdown-Auszeichnung,
# Klammern aus Platzhalter-Syntax, Zeilenumbrüche.
_UNWANTED = set("#*`_[]{}<>\n\r")

_QUOTES = "\"'«»„“”"


def normalize(text: str) -> str:
    """Kleinschreibung, Umlaute aufgelöst, nur Buchstaben und Ziffern.

    Die Grundlage von `fold` und damit von M02 und M03: "Kaese" zählt dort wie
    "Käse", und die Schreibweise spielt keine Rolle. Für M01 und M10 gilt das
    nicht — die suchen mit `phrase_hit`.
    """
    text = text.lower()
    for umlaut, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, replacement)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_WHITESPACE = re.compile(r"\s+")


def tidy(text: str) -> str:
    """Nur Unsichtbares einebnen: Unicode-Form und Leerraum.

    Keine Toleranz gegenüber der Schreibweise — das ist der Unterschied zu
    `normalize`. NFC setzt zerlegte Zeichen zusammen ("a" + Trema wird "ä"),
    damit zwei Zeichenketten, die gleich aussehen, auch gleich sind. Leerraum
    wird zu einem Leerzeichen, damit eine Phrase mit Zeilenumbruch darin noch
    trifft. Alles andere bleibt, wie es geschrieben steht.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def phrase_hit(text: str, phrases: Iterable[str]) -> str | None:
    """Die erste Phrase, die in `text` an Wortgrenzen vorkommt — oder None.

    Die eine Suchregel des Benchmarks, für Zielkeyword und Sperrliste dieselbe.
    Drei Eigenschaften, jede mit Grund:

    **Eine Ebene, ODER-verknüpft.** Die Liste nennt die Formen, die zählen
    ("Birnen ernten", "Birnenernte"); eine davon genügt. Wer mehr verlangen
    will, schreibt die längere Phrase hinein, statt eine zweite Ebene
    einzuführen.

    **Wortgrenzen.** "Birnenernten" trifft "Birnenernte" nicht. Ein Bindestrich
    ist eine Grenze, "Cookie" trifft also in "Cookie-Einwilligung".

    **Groß- und Kleinschreibung zählt.** Im Deutschen trägt sie Bedeutung:
    "buchen" und "Buchen" sind verschiedene Dinge, "Ernte" und "ernte" sind
    Substantiv und Verbform. Und ein durchgehend groß geschriebener Titel
    ("BIRNENERNTE: Jetzt vorbereiten!") ist auch als SEO-Titel keine gute Wahl,
    fällt hier also zu Recht durch.

    Was damit bewusst NICHT gilt: Umlaute werden nicht aufgelöst ("Kaese"
    trifft "Käse" nicht), Teilwörter zählen nicht ("Glühbirne" trifft "Birne"
    nicht) und Flexionsendungen werden nicht geraten ("Filialen" trifft
    "Filiale" nicht). Wer eine Form zulassen will, schreibt sie in die Liste.

    Zurückgegeben wird die getroffene Phrase und nicht `True`: Die Begründung im
    Log soll sagen, WAS getroffen hat — bei der Sperrliste ist das die halbe
    Diagnose.
    """
    haystack = tidy(text)
    for phrase in phrases:
        if _phrase_match(haystack, phrase):
            return phrase
    return None


def _phrase_match(haystack: str, phrase: str) -> re.Match[str] | None:
    """Die eine Suchregel, auf einen schon mit `tidy` geglätteten Text.

    `phrase_hit` und `phrase_position` müssen dasselbe als Treffer werten —
    sonst stünde ein Keyword vorne, das `keyword` gar nicht gefunden hat.
    """
    needle = tidy(phrase)
    if not needle:
        return None
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack)


def head_hit(text: str, phrases: Iterable[str]) -> str | None:
    """Wie `phrase_hit`, aber ein Listenwort trifft auch als Grundwort.

    Im Deutschen bestimmt das letzte Glied eines Kompositums, was gemeint ist:
    Eine "Holzhütte" ist eine Hütte, "Strandstühle" sind Stühle, eine
    "Landstraße" ist eine Straße. Für eine Bildbeschreibung ist das der
    Normalfall, und ohne diese Regel müsste jede Liste jedes denkbare
    Bestimmungswort aufzählen — das kann niemand, und die Listen wären für
    Menschen nicht mehr zu pflegen.

    Das Grundwort steht im Kompositum klein ("Herbst**k**ürbisse"), deshalb
    trifft ein groß geschriebenes Listenwort dort in Kleinschreibung. Davor
    muss ein Buchstabe stehen, dahinter eine Wortgrenze. Sonst bleibt alles wie
    in `phrase_hit`: keine geratenen Endungen ("Kürbissen" trifft "Kürbis"
    nicht), keine Umlautfaltung, und das Wort muss am **Ende** stehen
    ("Kürbisfeld" trifft "Kürbis" nicht — ein Feld ist kein Kürbis).

    Der Preis: Auch ein falsches Kompositum trifft, "Seilbahn" trifft "Bahn".
    Wo das eine naheliegende Verwechslung ist, gehört das Kompositum in die
    Sperrliste. Für Sperrlisten gilt diese Regel deshalb ausdrücklich NICHT —
    sonst sperrte "Auto" auch die "Elektroauto-Ladestation", und eine
    Sperrliste müsste jedes harmlose Kompositum mitbedenken.
    """
    haystack = tidy(text)
    for phrase in phrases:
        if _phrase_match(haystack, phrase) or _head_match(haystack, phrase):
            return phrase
    return None


def _head_match(haystack: str, phrase: str) -> re.Match[str] | None:
    needle = tidy(phrase)
    if not needle:
        return None
    head = needle[0].lower() + needle[1:]
    return re.search(rf"(?<=[^\W\d_]){re.escape(head)}(?!\w)", haystack)


def phrase_position(text: str, phrases: Iterable[str]) -> int | None:
    """Wo in `text` der früheste Treffer einer Phrase beginnt — oder None.

    Dieselbe Suchregel wie `phrase_hit`, aber über ALLE Phrasen: Gefragt ist,
    wie weit vorne das Thema im Titel steht, nicht welche Phrase der Liste
    zuerst trifft. Bei ["Wasserhahn", "Hahn"] und "Hahn tropft: Wasserhahn
    reparieren" ist das 0, obwohl `phrase_hit` "Wasserhahn" meldet.

    Die Position zählt im geglätteten Text (`tidy`), also so, wie der Titel
    angezeigt wird: Doppelte Leerzeichen schieben das Keyword nicht nach hinten.
    """
    haystack = tidy(text)
    starts = [m.start() for phrase in phrases if (m := _phrase_match(haystack, phrase))]
    return min(starts, default=None)


def overlength_penalty(chars_over: int, steps: tuple[tuple[int, float], ...]) -> float:
    """Abzug für `chars_over` Zeichen über der Obergrenze; 0 ohne Überlänge.

    `steps` sind Paare (höchstens so viele Zeichen darüber, Abzug), aufsteigend.
    Jenseits der letzten Stufe gibt die Aufgabe keinen Abzug mehr, sondern lässt
    den Text an `length` scheitern — der Wert wird dann nicht mehr gebraucht.
    Eine Funktion für M01 und M10, damit dieselbe Staffel in beiden gleich
    gerechnet wird.
    """
    if chars_over <= 0:
        return 0.0
    for limit, penalty in steps:
        if chars_over <= limit:
            return penalty
    return steps[-1][1]


def format_ok(title: str) -> bool:
    """Nur der Titel — so, wie er ins title-Tag gehört.

    Das ist die Prüfung, an der kleine Modelle am häufigsten scheitern: Sie
    liefern inhaltlich brauchbare Titel, packen sie aber in Anführungszeichen
    oder schreiben "Titel: ..." davor. Genau diese Formatschwäche ist für den
    Praxiseinsatz aus Screaming Frog heraus der teure Teil.
    """
    if not title:
        return False
    if any(char in _UNWANTED for char in title):
        return False
    if _EMOJI.search(title):
        return False
    if title[0] in _QUOTES and title[-1] in _QUOTES:
        return False
    if re.match(r"^\s*(seo[- ]?)?titl?e?l?\s*[:\-]", title, re.IGNORECASE):
        return False
    return True


# Zeichen, die außer Buchstaben, Ziffern und Leerzeichen in einem Titel
# vorkommen dürfen: die üblichen Trenner einer Title-Zeile und, was Preise,
# Mengen und Abkürzungen brauchen. Alles andere ("•", "→", "★", "™", "…", ";")
# sieht in einer Trefferliste nach Werbeprospekt aus.
_STYLE_CHARS = set(" -–—|:,.&/()!?+%'’€°")

# Wo ein Titel in Abschnitte zerfällt. Innerhalb eines Abschnitts steht
# deutscher Satzbau, und der kennt keine drei großgeschriebenen Wörter in
# Folge, ohne dass eines davon ein kleingeschriebenes Verb, Adjektiv oder
# Funktionswort sein müsste. Über die Trenner hinweg gilt das nicht: Eine
# Aufzählung "Symptome, Ursachen & Behandlung" ist richtig geschrieben.
_SEGMENT_BREAK = re.compile(r"[:–—|,&/()!?]|\s-\s")

# Ab dieser Länge ist ein Abschnitt aus lauter großgeschriebenen Wörtern
# verdächtig. Zwei genügen nicht: "Gartenbank Materialien" oder "Krebs
# Früherkennung" sind holprig, aber keine Title-Case-Schreibung.
_TITLE_CASE_RUN = 3

STYLE_RULES = ("spaces", "capital", "title_case", "charset", "sentence_end")


def style_violations(title: str) -> list[str]:
    """Die Stilregeln, gegen die ein Titel verstößt — in der Reihenfolge von `STYLE_RULES`.

    Keine dieser Regeln entscheidet, ob ein Titel brauchbar ist; das tun die
    harten Prüfungen. Sie fangen nur Titel, die alle harten Prüfungen bestehen
    und trotzdem auf den ersten Blick falsch aussehen:

        spaces        weniger als zwei Leerzeichen — eine Bindestrich-Kette
                      ("Sommer-Mosquito-Stich-Vorbeugung") statt einer Zeile
        capital       beginnt weder mit Großbuchstabe noch mit Ziffer
                      ("5 Tipps …" ist ein gewöhnlicher Titelanfang)
        title_case    ein Abschnitt aus drei oder mehr großgeschriebenen
                      Wörtern ("Igel Richtig Füttern")
        charset       ein Zeichen außerhalb von Buchstaben, Ziffern und
                      `_STYLE_CHARS`
        sentence_end  endet auf einen Punkt — ein Satz, keine Überschrift

    `title_case` verlangt bewusst NICHT einfach "mindestens ein
    kleingeschriebenes Wort": Deutsche Titel aus lauter Substantiven sind
    richtig geschrieben ("Mandelentzündung: Symptome, Ursachen & Behandlung").
    Und sie prüft den ganzen Abschnitt, nicht drei Wörter in Folge: Ein
    kleingeschriebenes Wort genügt, sonst träfe die Höflichkeitsform ("Wann
    bekommen Sie Ihr Geld zurück"). Einzelne Fehlalarme bleiben ("Geschmortes
    Rindfleisch Rezept"); sie kosten nur einen leichten Abzug.

    Emojis und Markdown stehen hier nicht: Sie lässt schon `format_ok` scheitern.
    """
    violations = []
    if title.count(" ") < 2:
        violations.append("spaces")
    if not title[:1].isupper() and not title[:1].isdigit():
        violations.append("capital")
    for segment in _SEGMENT_BREAK.split(title):
        words = [w for w in segment.split() if w[:1].isalpha()]
        if len(words) >= _TITLE_CASE_RUN and all(w[:1].isupper() for w in words):
            violations.append("title_case")
            break
    if any(not (char.isalnum() or char in _STYLE_CHARS) for char in title):
        violations.append("charset")
    if title.endswith("."):
        violations.append("sentence_end")
    return violations


def fold(text: str) -> str:
    """Normalisierung für den Vergleich — `normalize` mit einem Vorschritt.

    Kein zweiter Normalisierer neben `normalize`: Am Ende steht immer dieselbe
    Funktion. Zwei Normalisierer nebeneinander wären der Weg, auf dem die
    Referenzen von M02 und M03 auseinanderdriften.

    Der Vorschritt faltet Kompatibilitätszeichen auf ihre Grundform: "CH₄"
    wird zu "ch4", "m²" zu "m2". Ohne ihn verschluckt `normalize` die
    tiefgestellte Ziffer ganz ("ch"), und ein Modell, das die Summenformel
    korrekt mit Tiefstellung schreibt, bekäme null Punkte für eine richtige
    Antwort.

    Das NFC am Ende ist nicht Zierrat, sondern nötig: NFKD zerlegt "ä" in "a"
    plus kombinierendes Trema. Ohne das Zusammensetzen danach fände `normalize`
    kein "ä" mehr zum Ersetzen, "Käse" würde zu "kase" statt "kaese" — und die
    Umlautgleichheit, auf der die halbe Referenz beruht, wäre still weg.
    """
    return normalize(unicodedata.normalize("NFC", unicodedata.normalize("NFKD", text)))


def word_hit(haystack_folded: str, term: str) -> bool:
    """Kommt `term` als eigenständiges Wort vor? Beide Seiten gefaltet.

    Wortgrenzen sind hier wesentlich: Der Distraktor "Inn" darf nicht in
    "Innsbruck" oder "innerhalb" anschlagen.

    Die Grenze ist auf beiden Seiten hart: keine geratenen Flexionsendungen.
    Für M03 wäre eine Öffnung schädlich, weil Referenzen auch sehr kurze
    Begriffe enthalten ("TPE"). Mit geratenen Endungen träfe ein kurzer Begriff
    Allerweltswörter — "Inn" plus Endung ergibt "innen".

    Der Unterschied zu `phrase_hit`, mit dem M01 und M10 suchen: Hier wird
    gefaltet, dort nicht. "Kaese" trifft also "Käse", und die Schreibweise
    spielt keine Rolle.
    """
    needle = fold(term)
    if not needle:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
    return re.search(pattern, haystack_folded) is not None
