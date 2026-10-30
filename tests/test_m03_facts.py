"""Zusicherungen über die Aufgabe M03 selbst.

Vier Dinge, die keine Textlogik sind:

1. Der Datensatz — jedes Faktum muss im Produkttext auch stehen, sonst ist der
   Datensatz nicht schwer, sondern unlösbar.
2. Die Fallen — jeder notierte Distraktor muss im Text vorkommen und darf nicht
   der echte Preis sein.
3. Der Vertrag — was der Prompt verlangt und was die Wertung prüft, müssen
   dieselben sieben Felder in derselben Reihenfolge sein.
4. Der Oracle-Lauf, in-process, mitsamt Gegenproben.
"""

import json

import pytest
from inspect_ai import eval as inspect_eval

from german_seo_bench.checks.facts import FIELDS, NULLABLE
from german_seo_bench.checks.facts import numbers
from german_seo_bench.dataset import load_dataset
from german_seo_bench.tasks.m03_facts import (
    ANSWER_FORMAT,
    DATA,
    FLAGS,
    GROUPS,
    RULES,
    m03_facts,
    prompt,
)
from conftest import group_metrics, group_names

# Wörter, an denen die Lieferbarkeit im Text hängt. Die Liste ist nicht die
# Prüfung, sondern die Zusicherung, dass jeder Datensatz die Angabe überhaupt
# macht — ein Produkttext ohne Verfügbarkeitsaussage macht `in_stock` unlösbar.
#
# Die zweite Gruppe ist der Grund, warum die Liste eine Liste bleiben muss: Die Datensätze der Stufen 4 und 5 sagen die
# Verfügbarkeit absichtlich OHNE das übliche Vokabular ("Restposten, solange
# der Vorrat reicht", "wird nicht mehr geführt"). Genau das ist dort die Falle.
# Wer einen solchen Datensatz aufnimmt, trägt seine Formulierung hier nach —
# sonst meldet die Zusicherung einen Datensatz als unlösbar, der es nicht ist.
STOCK_WORDS = (
    "lager",
    "lieferbar",
    "ausverkauft",
    "verfügbar",
    "versandfertig",
    "bestellbar",
    "restposten",
    "vorrat",
    "nicht mehr geführt",
)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATA, prompt)


@pytest.fixture(scope="module")
def samples(dataset) -> list[dict]:
    """Die Datensätze so, wie der Scorer sie sieht: Eingabe und Referenz."""
    return [{"id": sample.id, **sample.metadata} for sample in dataset]


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory) -> str:
    """Testläufe schreiben ihre Logs ins Temporäre, nicht nach ./logs."""
    return str(tmp_path_factory.mktemp("eval-logs"))


class TestDatensatz:
    def test_ids_sind_eindeutig(self, samples) -> None:
        ids = [s["id"] for s in samples]
        assert len(set(ids)) == len(ids)

    def test_nur_vorgesehene_felder_sind_null(self, samples) -> None:
        for sample in samples:
            for name, value in sample["facts"].items():
                if value is None:
                    assert name in NULLABLE, f"{sample['id']}: {name}"

    def test_alle_felder_vollstaendig(self, samples) -> None:
        for sample in samples:
            assert set(sample["facts"]) == set(FIELDS), sample["id"]

    def test_jedes_nullbare_feld_kommt_beidseitig_vor(self, samples) -> None:
        """Ein Feld, das immer belegt ist, prüft die Lücken-Erkennung nie.

        Für `brand`, `color` und `material` ist "nicht genannt" der
        interessantere Fall: Dort kann ein Modell nichts abschreiben, sondern
        muss eine Lücke erkennen statt zu raten. Käme der Fall in keinem
        Datensatz vor, blieb die halbe Aufgabe ungemessen — und umgekehrt.
        """
        for name in NULLABLE:
            values = [s["facts"][name] for s in samples]
            assert any(v is None for v in values), f"{name}: nie null"
            assert any(v is not None for v in values), f"{name}: immer null"

    def test_fakten_stehen_im_produkttext(self, samples) -> None:
        """Sonst ist der Datensatz nicht schwer, sondern unlösbar.

        Geprüft wird je Feld mit der Strenge, die es verdient: Zeichenketten
        müssen wörtlich vorkommen, der Preis als Zahl (der Text schreibt ihn in
        deutscher Notation, die Referenz als Zahl — genau diese Umrechnung ist
        die Aufgabe).
        """
        for sample in samples:
            text = sample["text"]
            facts = sample["facts"]
            assert facts["price_eur"] in numbers(text), (
                f"{sample['id']}: Preis {facts['price_eur']} steht nicht im Text"
            )
            for name in ("name", "brand", "color", "material", "sku"):
                value = facts[name]
                if value is not None:
                    assert value in text, f"{sample['id']}: {name}={value!r}"

    def test_lieferbarkeit_ist_im_text_ausgesagt(self, samples) -> None:
        # Alle Fehlschläge auf einmal, nicht nur den ersten: Beim Erweitern des
        # Datensatzes ist die Frage "welche Texte fehlen noch" und nicht
        # "welcher fällt als Erster auf".
        stumm = [
            sample["id"]
            for sample in samples
            if not any(word in sample["text"].lower() for word in STOCK_WORDS)
        ]
        assert not stumm, f"ohne Verfügbarkeitsaussage: {stumm}"


    def test_jeder_datensatz_hat_eine_stufe(self, samples) -> None:
        for sample in samples:
            assert sample["level"] in (1, 2, 3, 4, 5), sample["id"]

    def test_jede_stufe_ist_besetzt(self, samples) -> None:
        """Mindestens fünf Datensätze je Stufe.

        Eine Quote über zwei Items springt um 0,50 je Item und wäre eine
        Spalte, die eine Aussage suggeriert, die sie nicht trägt.
        """
        import collections

        counts = collections.Counter(s["level"] for s in samples)
        assert set(counts) == {1, 2, 3, 4, 5}, sorted(counts)
        for level, count in sorted(counts.items()):
            assert count >= 5, f"Stufe {level} hat nur {count} Datensätze"

    def test_stufe_steht_auf_der_referenzseite(self) -> None:
        # Die Schwierigkeit ist eine Eigenschaft der Messung, kein Hinweis für
        # das Modell. Geprüft an der Datendatei, weil `load_dataset` dem Prompt
        # ohnehin nur den `input`-Teil gibt.
        import json
        from importlib import resources

        raw = resources.files("german_seo_bench.data").joinpath(DATA).read_text(encoding="utf-8")
        for record in json.loads(raw)["samples"]:
            assert "level" not in record["input"], record["id"]
            assert "level" in record["reference"], record["id"]


    def test_sku_regel_zeigt_ein_beispiel_mit_buchstaben(self) -> None:
        """Die Gegenprobe zu einer Anweisung mit zwei Lesarten.

        "nur die Nummer selbst (ohne "Art.-Nr." oder "SKU:")" ist als "nur die
        Ziffern" lesbar — aus `UG-1717` würde `1717`. Gemessen würde damit die
        Eindeutigkeit unserer Formulierung und nicht die Extraktionsfähigkeit.

        Ein Beispiel schließt die zweite Lesart, eine Beschreibung nicht.
        Deshalb wird hier auf das Beispiel geprüft und nicht auf Wortlaut:
        Die Regel muss ein Ziel zeigen, das einen Buchstaben enthält.
        """
        sku_rule = next(
            line for line in RULES.splitlines() if line.strip().startswith("sku")
        )
        block = RULES.split("sku", 1)[1].split("in_stock")[0]
        assert "->" in block, "Die sku-Regel zeigt kein Beispiel"
        ziel = block.split("->")[1]
        assert any(c.isalpha() for c in ziel.split('"')[1]), (
            f"Das Beispiel zeigt eine rein numerische SKU: {ziel!r}"
        )
        assert sku_rule

    def test_jede_referenz_sku_traegt_einen_buchstaben(self, samples) -> None:
        # Sonst prüft der Datensatz die Unterscheidung gar nicht: Bestünde eine
        # SKU nur aus Ziffern, wäre "nur die Ziffern" und "die ganze Nummer"
        # dieselbe Antwort, und das Feld ginge auch bei falscher Lesart durch.
        for sample in samples:
            sku = sample["facts"]["sku"]
            assert any(c.isalpha() for c in sku), f"{sample['id']}: {sku!r}"


class TestFallen:
    """Die Distraktor-Zahlen sind der Kern der Aufgabe — und selbst prüfbar."""

    def test_jeder_distraktor_steht_im_text(self, samples) -> None:
        for sample in samples:
            found = numbers(sample["text"])
            for distractor in sample["price_distractors"]:
                assert float(distractor["value"]) in found, (
                    f"{sample['id']}: {distractor} steht nicht im Text"
                )

    def test_kein_distraktor_ist_der_echte_preis(self, samples) -> None:
        # Sonst würde die Begründung einer richtigen Antwort einen
        # "Streichpreis genommen" anhängen — oder, schlimmer, einer falschen
        # Antwort einen falschen Grund.
        for sample in samples:
            price = sample["facts"]["price_eur"]
            for distractor in sample["price_distractors"]:
                assert abs(float(distractor["value"]) - price) > 0.011, sample["id"]

    def test_es_gibt_genug_fallen(self, samples) -> None:
        """Die Aufgabe hängt an den Fallen. Ohne sie wäre sie Abschreiben.

        Die Grenze ist bewusst nicht "jeder Datensatz": `aerosafe` und
        `chefline` haben absichtlich keinen Zahlen-Distraktor — `aerosafe` ist
        die Gegenprobe zur Gewichtsfalle, dort IST der vierstellige Betrag der
        Preis.
        """
        with_traps = [s for s in samples if s["price_distractors"]]
        assert len(with_traps) >= len(samples) * 0.7


class TestVertrag:
    def test_prompt_bekommt_nur_den_produkttext(self) -> None:
        """Die Regel, an der alles hängt, von der Datenseite her geprüft.

        `load_dataset` gibt `prompt()` ausschließlich `input`. Stünde dort ein
        einziges Feld der Referenz, wäre die Antwort mitgeliefert — und zwar
        ohne dass es irgendwo aufflöge.
        """
        raw = load_dataset(DATA, lambda inputs: repr(sorted(inputs)))
        for sample in raw:
            assert sample.input == "['text']", sample.id

    def test_prompt_nennt_die_fallen_nicht(self, dataset) -> None:
        """Der Prompt verrät die schwierige Hälfte nicht.

        Stünde dort "Streichpreis, Versandkosten, Sternebewertung … sind NICHT
        der Preis", mäße `price_eur` Anweisungsbefolgung statt
        Unterscheidungsvermögen und wäre entsprechend gesättigt.

        Geprüft wird der fertige Prompt und nicht die Quelldatei: Der Hinweis
        könnte bei einer Umformulierung auch aus `RULES` heraus
        zurückkommen, und dann stünde er wieder drin, ohne dass jemand die
        Prompt-Funktion angefasst hätte.

        Geprüft wird nur die ANWEISUNG, nicht der Produkttext dahinter. Der
        darf die Wörter sehr wohl enthalten — `thermogo` nennt Versandkosten,
        und genau das ist dort die Falle. Der Unterschied ist die ganze
        Aufgabe: im Text stehen lassen, im Prompt nicht ankündigen.
        """
        verraeter = ("streichpreis", "versandkosten", "sternebewertung", "nicht der preis")
        for sample in dataset:
            anweisung = sample.input.split("Produkttext:")[0].lower()
            found = [word for word in verraeter if word in anweisung]
            assert not found, f"{sample.id}: Prompt nennt {found}"

    def test_musterloesung_nicht_im_prompt(self, dataset) -> None:
        for sample in dataset:
            assert sample.metadata["oracle"] not in sample.input, sample.id

    def test_musterloesung_ist_genau_die_referenz(self, samples) -> None:
        """Gegen die stille Drift zwischen Oracle-Text und Referenzfakten.

        Der Oracle-Lauf setzt `reference.oracle` als Antwort ein, gewertet wird
        gegen `reference.facts`. Liefen die beiden auseinander, meldete der
        Oracle-Lauf einen Fehler in der Wertung, wo bloß zwei Kopien derselben
        Wahrheit nicht mehr übereinstimmen.
        """
        for sample in samples:
            assert json.loads(sample["oracle"]) == sample["facts"], sample["id"]

    def test_vertrag_und_wertung_passen_zusammen(self) -> None:
        """Was der Prompt verlangt und was gewertet wird, ist dieselbe Liste.

        Liefen sie auseinander — ein Feld umbenannt, eines vergessen —, verlangte
        die Aufgabe etwas anderes, als die Wertung prüft, und die Differenz sähe
        in der Tabelle wie Modellversagen aus.
        """
        mentioned = [
            line.split()[0]
            for line in RULES.splitlines()[1:]
            if line.startswith("  ") and not line.startswith("    ")
        ]
        assert mentioned == list(FIELDS)

    def test_prompt_verlangt_ein_objekt_auf_oberster_ebene(self, dataset) -> None:
        """Der Prompt lässt keine Liste von Produkten zu.

        „Felder je Produkt" und ein bloßes „ein JSON-Objekt" lassen
        `{"products": [{...}]}` zu — ein Objekt, aber mit keinem der sieben
        Schlüssel oben. Ein Modell, das so antwortet, extrahiert richtig und
        bekommt trotzdem null.
        """
        for sample in dataset:
            anweisung = sample.input.split("Produkttext:")[0]
            assert "je Produkt" not in anweisung, sample.id
            assert sample.input.endswith(ANSWER_FORMAT), sample.id
        assert "oberster Ebene" in ANSWER_FORMAT
        assert f"{len(FIELDS)} Schlüssel" in ANSWER_FORMAT

    def test_kein_target(self, dataset) -> None:
        # M03 vergleicht nicht gegen eine Zeichenkette, sondern feldweise gegen
        # ein Objekt. Ein gesetztes Target würde im Log eine Wertung
        # suggerieren, die es nicht gibt.
        for sample in dataset:
            assert not sample.target, sample.id


class TestOracle:
    """Der Smoke-Test der Wertung: Die Musterlösung muss 1.00 ergeben."""

    @pytest.fixture(scope="class")
    @classmethod
    def log(cls, log_dir: str):
        return inspect_eval(m03_facts(oracle=True), model="none/none", log_dir=log_dir)[0]

    def test_lauf_erfolgreich(self, log) -> None:
        assert log.status == "success", log.error

    def test_alle_datensaetze_bewertet(self, log, dataset) -> None:
        assert len(log.samples) == len(dataset)

    @pytest.mark.parametrize("name", ("score",) + tuple(FIELDS) + FLAGS)
    def test_metrik_ist_eins(self, log, name: str) -> None:
        score = next(s for s in log.results.scores if s.name == name)
        assert score.metrics["mean"].value == 1.0

    @pytest.mark.parametrize("group", GROUPS)
    def test_profil_ist_vollstaendig(self, log, samples, group: str) -> None:
        expected = {str(sample[group]) for sample in samples}
        assert group_names(log, group) == expected

    @pytest.mark.parametrize("group", GROUPS)
    def test_jede_gruppe_ist_eins(self, log, group: str) -> None:
        columns = group_metrics(log, group)
        assert columns, f"keine Spalte für {group!r} im Log"
        for name, value in columns.items():
            assert value == 1.0, name

    def test_gegenprobe_scorer_unterscheidet(self, log_dir: str) -> None:
        # Ohne diese Probe wäre "alles 1.00" auch mit einem Scorer erreichbar,
        # der immer 1.00 liefert.
        broken = self._run(log_dir, _fixed_solver(json.dumps({"name": "Falsch"})))
        assert self._metric(broken, "score") < 1.0
        assert self._metric(broken, "parsed") == 1.0  # JSON war ja da
        assert self._metric(broken, "complete") == 0.0

    def test_gegenprobe_ohne_json_null_punkte(self, log_dir: str) -> None:
        prosa = self._run(log_dir, _fixed_solver("Die Fakten stehen im Text."))
        assert self._metric(prosa, "score") == 0.0
        assert self._metric(prosa, "parsed") == 0.0

    def test_verpackte_musterloesung_zaehlt_trotzdem(self, log_dir: str) -> None:
        """Die Toleranz des Extraktors, über alle Datensätze geprüft.

        Ein ```json-Block mit Vorrede ist die häufigste Verpackung kleiner
        Modelle. Würde sie als Extraktionsfehler gezählt, stünde in der Spalte
        `parsed` eine Aussage über das Antwortformat, die wie eine über die
        Extraktion aussieht.
        """
        wrapped = self._run(
            log_dir, _wrapping_solver("Hier sind die Fakten:\n```json\n{}\n```\nFertig.")
        )
        assert self._metric(wrapped, "score") == 1.0
        assert self._metric(wrapped, "parsed") == 1.0

    def test_einelementiges_array_zaehlt_trotzdem(self, log_dir: str) -> None:
        wrapped = self._run(log_dir, _wrapping_solver("[{}]"))
        assert self._metric(wrapped, "score") == 1.0

    @staticmethod
    def _run(log_dir: str, solver):
        return inspect_eval(
            m03_facts(oracle=True), model="none/none", log_dir=log_dir, solver=solver
        )[0]

    @staticmethod
    def _metric(log, name: str) -> float:
        return next(s for s in log.results.scores if s.name == name).metrics["mean"].value


def _fixed_solver(content: str):
    """Antwortet auf jeden Datensatz mit derselben Zeichenkette."""
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def fixed() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.output = ModelOutput.from_content(model="broken", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return fixed()


def _wrapping_solver(template: str):
    """Antwortet mit der Musterlösung des Datensatzes, in `template` verpackt.

    `template` enthält ein `{}` an der Stelle, an der das JSON steht.
    """
    from inspect_ai.model import ModelOutput
    from inspect_ai.solver import Generate, Solver, TaskState, solver

    @solver
    def wrapping() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            content = template.replace("{}", state.metadata["oracle"])
            state.output = ModelOutput.from_content(model="wrapped", content=content)
            state.messages.append(state.output.message)
            return state

        return solve

    return wrapping()
