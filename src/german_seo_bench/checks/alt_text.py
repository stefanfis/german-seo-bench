"""Die Floskel-Prüfung für ALT-Texte (M10).

Bewusst ohne Inspect-Import, wie der Rest von `checks/`: reine Textlogik, ohne
Modell, ohne Server, ohne Eval-Rahmen einzeln testbar.

Motiv und Zusammenhang prüft M10 mit `phrase_hit` aus `checks/text.py`, also mit
derselben Regel wie M01; eine eigene Funktion dafür braucht es nicht. Dass eine
Bildbeschreibung keine vorgegebene Schreibweise hat, steckt in den Daten: "Sofa"
und "Couch" sind dasselbe Möbelstück, also stehen beide in der Liste, und eine
davon genügt.

`boilerplate_hit` prüft den Satzanfang gegen die Floskel, an der ALT-Texte aus
Sprachmodellen fast immer scheitern: "Bild von …", "Das Foto zeigt …",
"Abbildung einer …". Das ist keine Stilfrage. Screenreader kündigen ein Bild
bereits über die Rolle des Elements an; der Vorspann wird also vorgelesen und
verdoppelt eine Information, die schon da war. Deshalb ist er eine eigene
Prüfung und nicht Teil von `format_ok`: Beide sind reparierbar, aber an
verschiedenen Stellen — `format` mit einem `strip()`, `opening` nur, indem man
das Modell anders fragt.
"""

import re

# Der Floskel-Vorspann, am Textanfang verankert.
#
# Die Teile, jeder mit Grund:
#
#   ^\W*                 führende Anführungszeichen und Sternchen überspringen.
#                        Die fallen ohnehin schon `format_ok` zur Last; ohne das
#                        Überspringen bliebe die Floskel darunter unentdeckt und
#                        die Diagnose nennte nur die Verpackung.
#   (?:ein|das|die|…)\s+ der Artikel davor ist optional — "Bild von Pinguinen"
#                        und "Ein Bild von Pinguinen" sind dieselbe Floskel.
#                        `a`, `an` und `the` stehen mit in der Liste, weil die
#                        englische Floskel ("A picture of …") bei kleinen
#                        Modellen genauso vorkommt wie die deutsche — und dann
#                        gleich an zwei Prüfungen scheitern soll.
#   (?:es|er|en|s|e|n)?  die Flexionsendung: "Fotos", "Bilder", "Grafiken".
#                        Längere Endungen zuerst, sonst gewinnt "e" gegen
#                        "es" und der Lookahead scheitert an einem Rest,
#                        den es gar nicht geben müsste.
#
# Der Nachbar-Lookahead ist der eigentliche Trick. Ohne ihn schlüge "bild" in
# "Bildschirm eines Fernsehers" an — ein völlig richtiger ALT-Text, der dann an
# der Floskel-Prüfung gescheitert wäre. Er verlangt, dass hinter dem Stichwort
# ein Nicht-Wortzeichen folgt, und lässt damit "Bildschirm", "Bilderrahmen" und
# "Aufnahmegerät" durch.
_BOILERPLATE = re.compile(
    r"^\W*"
    r"(?:(?:ein|eine|eines|einer|einem|das|der|die|dieses|diese|dieser|hier"
    r"|a|an|the)\s+)?"
    r"(?:bild|foto|fotografie|photo|grafik|graphik|abbildung|aufnahme"
    r"|illustration|screenshot|image|picture|alt[-\s]?text|bildbeschreibung"
    r"|beschreibung|antwort)"
    r"(?:es|er|en|s|e|n)?"
    r"(?![a-z0-9äöüß])",
    re.IGNORECASE,
)


def boilerplate_hit(text: str) -> str | None:
    """Der Floskel-Vorspann im Wortlaut, oder None.

    Zurückgegeben wird das Gefundene und nicht nur `True`: In der Begründung
    des Scorers soll stehen, WOMIT der Text anfing. "gescheitert an: opening"
    allein schickt einen zum Nachlesen ins Log.
    """
    match = _BOILERPLATE.match(text)
    return match.group(0).strip() if match else None
