"""Zusicherungen über die Serveraufzeichnung.

Geprüft wird gegen eine echte `/props`-Antwort (`tests/props_llama_cpp.json`,
aufgenommen von einem echten llama-swap). Ein
selbstgebautes Beispiel prüfte nur die eigenen Annahmen über das Format.
"""

import json
from pathlib import Path

import pytest

from german_seo_bench.config import SAMPLING_FIELDS, run_config
from german_seo_bench.server import _root, extract_props, extract_running, looks_like_props

PROPS = json.loads((Path(__file__).parent / "props_llama_cpp.json").read_text())

RUNNING = {
    "running": [
        {
            "model": "qwen3-1.7b",
            "state": "ready",
            "cmd": "llama-server --model Qwen3-1.7B-Q4_K_M.gguf --jinja "
            "--temp 0.0 --top-p 1.0 --top-k 0 --ctx-size 32768 --flash-attn on\n",
            "proxy": "http://localhost:5810",
            "ttl": 300,
        }
    ]
}


class TestRoot:
    @pytest.mark.parametrize(
        "base_url",
        ["http://192.168.1.50:8080/v1", "http://192.168.1.50:8080/v1/", "http://192.168.1.50:8080"],
    )
    def test_v1_wird_abgeschnitten(self, base_url: str) -> None:
        # `/props` und `/running` hängen an der Wurzel. Ohne das Abschneiden
        # liefe jede Abfrage ins Leere und die Messbedingung fehlte still.
        assert _root(base_url) == "http://192.168.1.50:8080"


class TestExtractProps:
    def test_sampling_parameter_vollstaendig(self) -> None:
        params = extract_props(PROPS)["params"]
        # Nicht nur die, die auf der Kommandozeile stehen: `min_p` ist ein
        # llama.cpp-Standard und verändert die Messung genauso.
        for field in ("temperature", "top_p", "top_k", "min_p", "seed"):
            assert field in params

    def test_reasoning_zustand_wird_erfasst(self) -> None:
        # Bei qwen3 entscheidet das über Denk- oder Direktantwort — ein
        # Unterschied, der jede Messung verschiebt.
        assert "reasoning_format" in extract_props(PROPS)["params"]

    def test_kontextfenster_und_build(self) -> None:
        recorded = extract_props(PROPS)
        assert recorded["n_ctx"] == 32768
        assert recorded["build_info"].startswith("b")

    def test_template_nur_als_hash(self) -> None:
        recorded = extract_props(PROPS)
        assert "chat_template" not in recorded
        assert len(recorded["chat_template_sha256"]) == 16

    def test_aufzeichnung_bleibt_klein(self) -> None:
        # Sie hängt an JEDEM Datensatz. Das komplette Template wären mehrere
        # Kilobyte je Sample.
        assert len(json.dumps(extract_props(PROPS))) < 4000

    def test_leere_antwort_stuerzt_nicht_ab(self) -> None:
        assert extract_props({}) == {}


class TestLooksLikeProps:
    def test_echte_props(self) -> None:
        assert looks_like_props(PROPS)

    @pytest.mark.parametrize(
        "body",
        [
            {"object": "list", "data": [{"id": "qwen3-1.7b"}]},  # /v1/models
            {"running": []},
            {},
            None,
            "kein json-objekt",
        ],
    )
    def test_fremde_antwort(self, body) -> None:
        # Gegenprobe: Ein Proxy, der jeden Pfad mit 200 bedient, darf nicht
        # als erfolgreiche Abfrage durchgehen.
        assert not looks_like_props(body)


class TestExtractRunning:
    def test_kommandozeile_wird_uebernommen(self) -> None:
        # Der wertvollste Teil: wörtlich und ohne Schema-Annahme, überlebt
        # also auch einen Versionssprung von llama.cpp.
        entry = extract_running(RUNNING, "qwen3-1.7b")
        assert "--temp 0.0" in entry["cmd"]
        assert entry["state"] == "ready"

    def test_fremdes_modell_ergibt_none(self) -> None:
        # Gegenprobe: Würde irgendein Eintrag zurückgegeben, schriebe man die
        # Bedingungen eines anderen Modells in die eigene Messung.
        assert extract_running(RUNNING, "qwen3.8-27b") is None

    def test_unerwartete_form_ergibt_none(self) -> None:
        assert extract_running({"unerwartet": 1}, "qwen3-1.7b") is None


class TestRunnerSendetKeinSampling:
    """Die Zusicherung, auf der die ganze Aufzeichnung ruht.

    `/props` meldet die Vorgaben des Servers, nicht die Parameter einer
    konkreten Anfrage. Sobald german_seo_bench selbst `temperature` mitschickt, steht in
    der Aufzeichnung etwas anderes als in der Anfrage — und niemand sieht es.
    Die Werte gehören in den llama-swap-Eintrag.
    """

    @pytest.mark.parametrize("field", SAMPLING_FIELDS)
    def test_feld_bleibt_ungesetzt(self, field: str) -> None:
        assert getattr(run_config(), field) is None


class TestServerContext:
    """Das Fenster vor der ersten Anfrage — nur, wenn es sicher zum Modell gehört.

    A01 nennt es goose und schreibt es ins Log. Props des vorher geladenen
    Modells wären schlimmer als keine: Mit einem zu großen Fenster verdichtet
    goose zu spät und läuft über.
    """

    @staticmethod
    def _mit(monkeypatch, recorded: dict, zeiten: list | None = None):
        import asyncio

        import german_seo_bench.server as server

        async def probe(base_url: str, model: str, timeout: float = 0) -> dict:
            if zeiten is not None:
                zeiten.append(timeout)
            return recorded

        monkeypatch.setattr(server, "probe_server", probe)
        return asyncio.run(server.server_context("http://dirac:8080/v1", "m"))

    def test_llama_server_allein(self, monkeypatch) -> None:
        # Kein `/running`: Es gibt nur ein Modell, die Props gehören zu ihm.
        assert self._mit(monkeypatch, {"n_ctx": 131072}) == (131072, None)

    def test_llama_swap_mit_laufendem_modell(self, monkeypatch) -> None:
        recorded = {"n_ctx": 65536, "upstream": {"cmd": "llama-server …"}}
        assert self._mit(monkeypatch, recorded) == (65536, None)

    def test_gegenprobe_llama_swap_ohne_das_modell(self, monkeypatch) -> None:
        # Das Modell steht nicht in `/running`: Die Props könnten vom
        # Vorgänger stammen.
        recorded = {"n_ctx": 131072, "upstream": {"note": "'m' nicht in /running"}}
        assert self._mit(monkeypatch, recorded) == (None, "'m' nicht in /running")

    def test_ohne_props_kein_fenster_aber_ein_grund(self, monkeypatch) -> None:
        recorded = {
            "errors": {"/upstream/m/props": "ReadTimeout: "},
            "props_note": "keine Props",
        }
        n_ctx, grund = self._mit(monkeypatch, recorded)
        assert n_ctx is None
        assert "ReadTimeout" in grund and "keine Props" in grund

    def test_die_abfrage_wartet_auf_das_laden(self, monkeypatch) -> None:
        # Der erste Lauf von A01 v2: Das Modell lud länger als die 5 s der
        # gewöhnlichen Abfrage, und gerechnet wurde mit einem angenommenen
        # Fenster statt der 131.072 Tokens des Servers.
        from german_seo_bench.server import LOAD_TIMEOUT_SECONDS, TIMEOUT_SECONDS

        zeiten: list = []
        self._mit(monkeypatch, {"n_ctx": 131072}, zeiten)
        assert zeiten == [LOAD_TIMEOUT_SECONDS]
        assert LOAD_TIMEOUT_SECONDS >= 300 > TIMEOUT_SECONDS
