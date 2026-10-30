"""JSON aus einer Modellantwort holen — für alle Aufgaben an einer Stelle.

Zwei Aufgaben verlangen JSON und bekommen es verpackt: M03 ein Objekt je
Datensatz, A01 ein Array von Befunden. Die Toleranz beim Auspacken ist dieselbe
und steht deshalb hier, nicht zweimal.

Was toleriert wird, ist die Verpackung: ein ```json-Block, eine Einleitung
davor ("Hier sind die Fakten:"), Text danach. Nichts davon ist ein
Formatfehler des Modells im Sinne der Messung — es ist die übliche Art, wie ein
Chat-Modell antwortet. Was NICHT toleriert wird, entscheidet die Aufgabe: M03
lehnt ein Array mit mehreren Objekten ab, A01 verlangt umgekehrt ein Array.
"""

import json
import re
from typing import Any

FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def first_json_value(text: str) -> tuple[Any | None, str | None]:
    """Erstes balanciertes `{…}` oder `[…]`, je nachdem was zuerst kommt.

    Rückgabe: (Wert, Fehlermeldung). Genau eines von beiden ist None.

    Der zuerst auftretende Klammertyp gewinnt. Andernfalls liefert ein Array
    von Objekten nur sein erstes Element, und ein Verstoß gegen das Format
    sähe wie eine Antwort aus.
    """
    starts = [
        (text.find(opener), opener, closer)
        for opener, closer in (("{", "}"), ("[", "]"))
        if text.find(opener) >= 0
    ]
    if not starts:
        return None, "kein JSON in der Antwort"
    error = None
    for start, opener, closer in sorted(starts):
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1]), None
                    except json.JSONDecodeError as exc:
                        error = f"JSON ungültig: {exc}"
                    break
        else:
            error = f"kein schließendes {opener}{closer} gefunden"
    return None, error


def candidates(text: str) -> list[str]:
    """Die Textstücke, in denen nach JSON gesucht wird — Codeblock zuerst.

    Der Codeblock hat Vorrang, weil Modelle das JSON gern in Prosa einbetten,
    die selbst Klammern enthält.
    """
    fenced = FENCE.search(text)
    return ([fenced.group(1)] if fenced else []) + [text]
