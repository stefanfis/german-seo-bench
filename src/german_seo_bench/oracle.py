"""Der Oracle-Lauf: der Smoke-Test der Wertung.

`oracle_solver()` stellt keine Anfrage an ein Modell. Es setzt die Musterlösung
aus `metadata["oracle"]` als Antwort ein und schickt sie durch **denselben**
Scorer wie eine echte Modellantwort. Das Ergebnis muss 1.00 sein.

Ist es das nicht, ist nicht das Modell schlecht, sondern die Prüfung kaputt —
und dann ist jede Zahl aus dieser Aufgabe wertlos. Der Lauf braucht weder
Server noch GPU noch Netz und ist deshalb der Test, der nach jeder Änderung
läuft (`uv run pytest` fährt ihn für jede Aufgabe mit).

Warum ein Solver und kein eigener Model-Provider: Ein Provider sieht nur die
Nachrichten, nicht den Datensatz. Die Musterlösung steht aber je Datensatz in
den Metadaten — und dort darf sie nur der Solver und der Scorer sehen, nie ein
Prompt.
"""

import json

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

ORACLE_MODEL = "oracle"


@solver
def oracle_solver(field: str = "oracle") -> Solver:
    """Antwortet mit der Musterlösung des Datensatzes statt mit einem Modell.

    Eine Liste oder ein Objekt im Metadatenfeld wird als JSON eingesetzt. Das ist
    für die Aufgaben gedacht, deren verlangte Antwort selbst JSON ist (A01): Dort
    IST die Referenz die Musterlösung, und ein zweites Feld daneben, das
    dasselbe nochmal als Zeichenkette enthält, wären zwei Fassungen derselben
    Wahrheit — sie driften auseinander, und die Wertung glaubte dann der
    falschen.

    Args:
        field: Metadatenfeld, in dem die Musterlösung steht.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        answer = state.metadata.get(field)
        if isinstance(answer, (list, dict)):
            answer = json.dumps(answer, ensure_ascii=False)
        if not isinstance(answer, str):
            raise ValueError(
                f"Datensatz {state.sample_id!r} hat kein Feld {field!r} — "
                "ohne Musterlösung ist der Oracle-Lauf sinnlos."
            )
        state.output = ModelOutput.from_content(model=ORACLE_MODEL, content=answer)
        state.messages.append(state.output.message)
        return state

    return solve
