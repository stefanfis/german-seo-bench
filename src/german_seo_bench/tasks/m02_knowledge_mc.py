"""M02 — Deutsches Weltwissen als Multiple Choice.

Wissensfragen aus der Welt, die für SEO im deutschsprachigen Raum und für
dessen Kunden zählt: Geografie, Recht, Reisen, E-Commerce, Österreich. Je Frage
zehn Antworten zur Auswahl, von denen genau eine stimmt. Das Modell nennt ihren
Buchstaben; gewertet wird nur der Buchstabe (`checks/choice.py`).

Warum Multiple Choice und keine freie Antwort: Eine freie Antwort lässt sich
nur über Wortlisten werten, und die sind an den Rändern unscharf — eine
richtige Antwort, die eine falsche Option richtig einordnet, fällt durch, und
eine widersprüchliche kommt durch, wenn ihr falscher Teil auf keiner Liste
steht. Multiple Choice verlangt nur Wiedererkennen, wertet dafür ohne jeden
Ermessensspielraum. Dass Wiedererkennen leichter ist als Abrufen, gleichen die
Fragen aus, nicht das Format.

Zehn Optionen statt der üblichen vier: Raten bringt dann 0.10 statt 0.25, und
unter den neun falschen stehen absichtlich nahe Verwechslungen — Zahlendreher,
Nachbarorte, ähnlich klingende Namen. Wer die Antwort nur ungefähr kennt, soll
hier nicht durch Ausschließen ans Ziel kommen.

Die Reihenfolge der Optionen steht fest in der Datendatei. Gemischt je Lauf
wäre sie eine zweite Zufallsquelle neben dem Sampling, und der schlechteste
Lauf je Frage (siehe `report.py`) maße dann auch, wie oft eine Option zufällig
an einer ungünstigen Stelle stand.
"""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Dataset
from inspect_ai.log import HeadlineMetric
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState, generate, system_message

from german_seo_bench.checks.choice import correct_letter, grade, letter
from german_seo_bench.config import run_config
from german_seo_bench.dataset import dataset_version, load_dataset
from german_seo_bench.metrics import profile
from german_seo_bench.oracle import oracle_solver
from german_seo_bench.server import record_endpoint, record_server

DATA = "m02_knowledge_mc.json"

# Nur die Form, kein Wort zur Sache: Jeder Hinweis auf Deutschland oder ein
# Sachgebiet wäre ein Wissenshinweis und damit Teil der Antwort.
SYSTEM = (
    "Du beantwortest Multiple-Choice-Fragen. Genau eine der Antworten ist "
    "richtig. Du hältst dich exakt an das geforderte Ausgabeformat und gibst "
    "nichts darüber hinaus aus."
)

FLAGS = ("invalid",)
# `level` ist das Schwierigkeitsprofil, `topic` zeigt, in welchem Feld ein
# Modell Lücken hat — für SEO zählt, ob es Reisen kennt, nicht nur der Schnitt.
GROUPS = ("level", "topic")


def prompt(inputs: dict[str, Any]) -> str:
    """Was das Modell sieht: Frage und Optionen, sonst nichts."""
    options = "\n".join(
        f"{letter(i)}) {choice}" for i, choice in enumerate(inputs["choices"])
    )
    return (
        f"{inputs['question']}\n\n{options}\n\n"
        "Antworte nur mit dem Buchstaben der richtigen Antwort."
    )


def with_oracle(dataset: Dataset) -> Dataset:
    """Trägt den Lösungsbuchstaben als Musterlösung in die Metadaten ein.

    Er wird aus `answer` und `choices` abgeleitet statt in der Datendatei zu
    stehen: Ein zweites Feld daneben könnte nach einer Umstellung der Optionen
    auf die falsche zeigen, und der Oracle-Lauf bestünde trotzdem nicht mehr
    aus dem, was die Wertung für richtig hält.
    """
    for sample in dataset:
        sample.metadata["oracle"] = correct_letter(sample.metadata)
    return dataset


@scorer(
    metrics=[
        {
            "score": [mean(), stderr(), *(profile(group) for group in GROUPS)],
            **{flag: [mean()] for flag in FLAGS},
        }
    ]
)
def choice_scorer() -> Scorer:
    """Die Wertung; die Logik steht in `checks/choice.py`."""

    async def score(state: TaskState, target: Target) -> Score:
        answer = state.output.completion.strip()
        verdict = grade(state.metadata, answer)
        truncated = state.output.stop_reason == "max_tokens"
        return Score(
            value={"score": verdict.points, "invalid": int(verdict.invalid)},
            answer=answer,
            explanation=("bei max_tokens abgeschnitten — " if truncated else "")
            + verdict.reason,
            metadata={
                "chosen": verdict.chosen,
                "correct": correct_letter(state.metadata),
                "truncated": truncated,
            },
        )

    return score


@task
def m02_knowledge_mc(oracle: bool = False) -> Task:
    """Deutsches Weltwissen, Multiple Choice.

    Args:
        oracle: Statt eines Modells den Lösungsbuchstaben einsetzen. Muss 1.00
            ergeben.
    """
    return Task(
        dataset=with_oracle(load_dataset(DATA, prompt)),
        # Adresse vor der ersten Anfrage aufzeichnen, Serverzustand danach —
        # dieselbe Reihenfolge wie in M01 und M03.
        solver=oracle_solver()
        if oracle
        else [
            record_endpoint(),
            system_message(SYSTEM),
            generate(),
            record_server(),
        ],
        scorer=choice_scorer(),
        config=run_config(),
        version=dataset_version(DATA),
        display_name="M02 — Deutsches Weltwissen, Multiple Choice",
        headline_metric=HeadlineMetric(score="score", metric="mean"),
    )
