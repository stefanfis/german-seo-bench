"""Zusicherungen über den Modellnamen samt Denkmodus (`modelname.py`).

Der Denkmodus eines Cloud-Modells steht nicht im Namen, sondern im Log. Geprüft
wird, dass er dort gelesen wird — und nur dort angenommen, wo die API ihn ohne
Angabe sicher vorgibt.
"""

from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log

from german_seo_bench.modelname import model_label, reasoning_budget, thinks_by_default
from german_seo_bench.tasks.m03_facts import m03_facts


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("anthropic/claude-haiku-5-5", True),
        ("anthropic/claude-sonnet-5", True),
        ("anthropic/claude-sonnet-5-5", True),
        ("anthropic/claude-opus-4-7", True),
        ("anthropic/claude-opus-4-8", True),
        ("anthropic/claude-fable-5-1", True),
        ("anthropic/claude-opus-4-7-20260115", True),
        # Gegenproben: vor 4.7 denkt Claude nur auf Anfrage, und das Datum
        # eines Snapshots ist keine Unterversion.
        ("anthropic/claude-haiku-4-5-20251001", False),
        ("anthropic/claude-sonnet-4-6", False),
        ("anthropic/claude-opus-4", False),
        # Andere Anbieter: Vorgabe unbekannt, also nichts angenommen.
        ("openai/gpt-5", False),
        ("local/claude-haiku-5-5", False),
    ],
)
def test_denkt_ohne_angabe(model: str, expected: bool) -> None:
    assert thinks_by_default(model) is expected


def _log(tmp_path: Path, model: str = "mockllm/zeta", **kwargs):
    """Kopf eines echten Laufs; der Modellname lässt sich danach umsetzen."""
    log = inspect_eval(m03_facts(oracle=True), model=model, log_dir=str(tmp_path),
                       display="none", limit=1, **kwargs)[0]
    return read_eval_log(log.location, header_only=True)


class TestModelLabel:
    def test_reasoning_effort_wird_zur_endung(self, tmp_path: Path) -> None:
        assert model_label(_log(tmp_path, reasoning_effort="none")) == "mockllm/zeta-think-none"

    def test_auch_ein_anderer_wert(self, tmp_path: Path) -> None:
        assert model_label(_log(tmp_path, reasoning_effort="high")) == "mockllm/zeta-think-high"

    def test_ohne_angabe_unveraendert(self, tmp_path: Path) -> None:
        # Gegenprobe zu beiden oben: Ohne Wert im Log wird nichts erfunden.
        assert model_label(_log(tmp_path)) == "mockllm/zeta"

    def test_claude_ohne_angabe_denkt_adaptiv(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        log.eval.model = "anthropic/claude-haiku-5-5"
        assert model_label(log) == "anthropic/claude-haiku-5-5-think-adaptive"

    def test_claude_mit_none_denkt_nicht(self, tmp_path: Path) -> None:
        # Die ausdrückliche Angabe geht der Vorgabe der API vor.
        log = _log(tmp_path, reasoning_effort="none")
        log.eval.model = "anthropic/claude-haiku-5-5"
        assert model_label(log) == "anthropic/claude-haiku-5-5-think-none"

    def test_endung_im_namen_geht_vor(self, tmp_path: Path) -> None:
        # Lokale Aliase tragen ihren Modus schon; eine zweite Endung dahinter
        # machte aus `-think-off` einen Namen mit zwei Modi.
        log = _log(tmp_path, reasoning_effort="high")
        log.eval.model = "local/qwen3.5-9b-think-off"
        assert model_label(log) == "local/qwen3.5-9b-think-off"


class TestDenkgrenze:
    """`thinking_budget_tokens` in der Anfrage wird zum Zusatz hinter dem Modus."""

    def _local(self, tmp_path: Path, model: str, budget) -> str:
        log = _log(tmp_path, extra_body={"thinking_budget_tokens": budget})
        log.eval.model = model
        return model_label(log)

    def test_zusatz_hinter_dem_modus(self, tmp_path: Path) -> None:
        assert self._local(tmp_path, "local/gemma4-12b-think-on", 1024) == \
            "local/gemma4-12b-think-on-budget1024"

    def test_ohne_angabe_kein_zusatz(self, tmp_path: Path) -> None:
        # Gegenprobe: Läufe ohne Grenze bekommen keinen Zusatz.
        log = _log(tmp_path)
        log.eval.model = "local/gemma4-12b-think-on"
        assert model_label(log) == "local/gemma4-12b-think-on"

    def test_unbegrenzt_wie_ohne_angabe(self, tmp_path: Path) -> None:
        assert self._local(tmp_path, "local/gemma4-12b-think-on", -1) == "local/gemma4-12b-think-on"

    @pytest.mark.parametrize("model", ["local/gemma4-12b-think-off", "local/qwen3.8-27b-think-none-precise"])
    def test_aus_ohne_zusatz(self, tmp_path: Path, model: str) -> None:
        # Ohne Denken greift keine Grenze; mit Zusatz bekäme derselbe Lauf
        # einen zweiten Namen neben den `-think-off`-Läufen ohne Angabe.
        assert self._local(tmp_path, model, 1024) == model

    def test_hinter_der_endung_aus_reasoning_effort(self, tmp_path: Path) -> None:
        log = _log(tmp_path, reasoning_effort="high", extra_body={"thinking_budget_tokens": 512})
        assert model_label(log) == "mockllm/zeta-think-high-budget512"

    def test_als_text_wie_als_zahl(self, tmp_path: Path) -> None:
        assert self._local(tmp_path, "local/g-think-on", "2048") == "local/g-think-on-budget2048"

    def test_andere_felder_zaehlen_nicht(self, tmp_path: Path) -> None:
        # Gegenprobe: Nur das Feld, das llama-server liest, ist eine Grenze.
        log = _log(tmp_path, extra_body={"chat_template_kwargs": {"enable_thinking": True}})
        log.eval.model = "local/g-think-on"
        assert model_label(log) == "local/g-think-on"

    @pytest.mark.parametrize("value", [0, -2, "viel", "1024 Tokens", 1.5])
    def test_unsinn_bricht_ab(self, tmp_path: Path, value) -> None:
        log = _log(tmp_path, extra_body={"thinking_budget_tokens": value})
        with pytest.raises(ValueError, match="thinking_budget_tokens"):
            reasoning_budget(log)
