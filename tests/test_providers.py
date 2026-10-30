"""Zusicherungen über den Provider `local/…`.

Der Punkt dieses Providers ist eine Voreinstellung, die ohne Einrichtung
funktioniert. Eine Voreinstellung, die niemand prüft, verrutscht beim nächsten
Upgrade der Basisklasse still — und der Lauf geht dann gegen einen Endpunkt,
den niemand gemeint hat.

`memoize=False` ist nötig: `get_model()` merkt sich Modelle je Prozess, und die
Tests unterscheiden sich hier gerade in der Umgebung, nicht im Namen.
"""

import pytest
from inspect_ai.model import get_model

from german_seo_bench.config import ATTEMPT_TIMEOUT_SECONDS
from german_seo_bench.providers import (
    CLIENT_TIMEOUT_SECONDS,
    DEFAULT_BASE_URL,
    PLACEHOLDER_API_KEY,
)
from german_seo_bench.tasks.a01_browser_audit import MODEL_CALL_TIMEOUT


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne das misst der Test die .env des Entwicklers mit."""
    monkeypatch.delenv("LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)


def _api(**kwargs):
    return get_model("local/qwen35-4b", memoize=False, **kwargs).api


class TestBaseUrl:
    def test_voreinstellung_ohne_einrichtung(self) -> None:
        assert _api().base_url == DEFAULT_BASE_URL
        assert DEFAULT_BASE_URL == "http://localhost:8080/v1"

    def test_umgebungsvariable_schlaegt_voreinstellung(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOCAL_BASE_URL", "http://10.0.0.7:8080/v1")
        assert _api().base_url == "http://10.0.0.7:8080/v1"

    def test_expliziter_wert_schlaegt_umgebungsvariable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Die Gegenprobe zur vorigen Zusicherung: Wäre die Reihenfolge
        # vertauscht, käme hier die Adresse aus der Umgebung zurück.
        monkeypatch.setenv("LOCAL_BASE_URL", "http://10.0.0.7:8080/v1")
        assert _api(base_url="http://192.168.1.50:8080/v1").base_url == (
            "http://192.168.1.50:8080/v1"
        )


class TestApiKey:
    def test_laeuft_ohne_schluessel(self) -> None:
        # Lokale Server prüfen keinen Schlüssel. Ohne den Platzhalter bräche
        # der OpenAI-Client ab, bevor er die erste Anfrage stellt.
        assert _api().api_key == PLACEHOLDER_API_KEY

    def test_schluessel_aus_umgebung_wird_genommen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOCAL_API_KEY", "geheim")
        assert _api().api_key == "geheim"


def test_modellname_ohne_dienstpraefix() -> None:
    # `local/qwen35-4b` kommt in der Basisklasse ohne Präfix an. Ginge das
    # schief, stünde im Request ein Modellname, den der Server nicht kennt.
    assert _api().service_model_name() == "qwen35-4b"


def test_ueber_registry_erreichbar() -> None:
    # Ohne Import von `german_seo_bench` auflösbar — das ist die Zusicherung, an der der
    # uvx-Weg hängt: Inspect lädt den Provider über den Entry-Point.
    assert get_model("local/x", memoize=False).api.base_url == DEFAULT_BASE_URL


class TestFrontierPfad:
    """Der Cloud-Client muss mitinstalliert sein, nicht nur erwähnt.

    Inspect führt die Anbieter-SDKs als optionale Abhängigkeiten und prüft erst
    beim ersten Aufruf, ob das Paket da ist. Fehlt es, endet der Lauf mit
    `Anthropic API requires optional dependencies` — und zwar in dem Moment, in
    dem man einen bezahlten Frontier-Lauf startet, nicht beim Einrichten.

    Deshalb steht `anthropic` in den Abhängigkeiten und nicht in einem Extra,
    und deshalb steht hier ein Test: Wer die Abhängigkeit für überflüssig hält
    und streicht, merkt es beim nächsten `uv run pytest` statt beim nächsten
    Vergleich lokal gegen Frontier.
    """

    def test_anthropic_client_ist_da(self) -> None:
        import importlib.util

        assert importlib.util.find_spec("anthropic"), (
            "Das Paket `anthropic` fehlt — dann scheitert jeder Lauf mit "
            "`--model anthropic/…` erst beim Start."
        )

    def test_der_provider_laesst_sich_laden(self) -> None:
        # Nicht nur das Paket, sondern der Pfad, den Inspect tatsächlich geht.
        # Ein Import, der an einer Versionsgrenze scheitert, sähe sonst wie ein
        # Modellfehler aus.
        import importlib

        assert importlib.import_module("inspect_ai.model._providers.anthropic")


class TestKontextueberlauf:
    """Der Kontextüberlauf lokaler Server — übersetzt, nicht durchgereicht.

    Ohne Übersetzung endet ein Agent-Lauf mit zu kleinem Kontextfenster mit
    `status: error` **ohne Score**. Inspect erkennt einen Überlauf nur an
    OpenAIs Fehlercode `context_length_exceeded`; llama.cpp meldet
    `exceed_context_size_error`. Damit griffe auch `truncation="auto"` nie — die
    `react`-Schleife reagiert auf `stop_reason == "model_length"`, und das käme
    nie an.
    """

    @staticmethod
    def _fehler(nachricht: str, typ: str = "invalid_request_error"):
        import httpx
        from openai import APIStatusError

        antwort = httpx.Response(
            400, request=httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
        )
        return APIStatusError(
            nachricht, response=antwort, body={"message": nachricht, "type": typ}
        )

    @staticmethod
    def _api():
        # Über `get_model` wie die übrigen Tests der Datei — die Klasse direkt
        # zu bauen geht nicht, `@modelapi` liefert eine Fabrik.
        return _api()

    @pytest.mark.parametrize(
        "nachricht",
        [
            # llama.cpp / llama-swap, wörtlich
            "the request exceeds the available context size (32768 tokens), "
            "try increasing it",
            # dieselbe Lage, andere Server
            "This model's maximum context length is 8192 tokens",
            "prompt is too long: 42000 tokens > 32768 maximum",
            "input length exceeds the maximum",
        ],
    )
    def test_ueberlauf_wird_zu_model_length(self, nachricht: str) -> None:
        from inspect_ai.model import ModelOutput

        ergebnis = self._api().handle_bad_request(self._fehler(nachricht))
        assert isinstance(ergebnis, ModelOutput), ergebnis
        assert ergebnis.stop_reason == "model_length", ergebnis.stop_reason

    def test_der_typ_allein_genuegt_auch(self) -> None:
        # llama-swap setzt den Typ, die Meldung kann abweichen.
        from inspect_ai.model import ModelOutput

        ergebnis = self._api().handle_bad_request(
            self._fehler("Anfrage abgelehnt", typ="exceed_context_size_error")
        )
        assert isinstance(ergebnis, ModelOutput)
        assert ergebnis.stop_reason == "model_length"

    def test_jeder_ueberlauf_wird_im_datensatz_gezaehlt(self) -> None:
        """Die Zahl ist eine Laufbedingung und gehört in die Ergebnisdatei.

        Ein Lauf, in dem gekürzt wurde, hat Teile seiner eigenen Beobachtungen
        vergessen und ist mit einem ungekürzten nicht vergleichbar. Gezählt
        wird im Provider, wo der Überlauf auftritt — das Transkript ist im
        bounded-Modus nicht verlässlich abfragbar, und ein Zähler, der manchmal
        nichts findet, ist schlimmer als keiner.
        """
        from inspect_ai.util import Store, store
        from inspect_ai.util._store import init_subtask_store

        from german_seo_bench.providers import STORE_CONTEXT_OVERFLOWS

        init_subtask_store(Store())
        api = self._api()
        assert store().get(STORE_CONTEXT_OVERFLOWS, 0) == 0
        for erwartet in (1, 2, 3):
            api.handle_bad_request(
                self._fehler("exceeds the available context size (32768 tokens)")
            )
            assert store().get(STORE_CONTEXT_OVERFLOWS, 0) == erwartet

        # Ein anderer Fehler darf den Zähler nicht bewegen — sonst stünde im
        # Bericht „Kontext war voll", wo ein Tippfehler war.
        api.handle_bad_request(self._fehler("model 'gibtsnicht' not found"))
        assert store().get(STORE_CONTEXT_OVERFLOWS, 0) == 3

    def test_ohne_datensatz_kostet_das_zaehlen_nichts(self) -> None:
        # Einzeltests und Werkzeugaufrufe laufen ohne Store. Das ist der
        # Normalfall und darf keinen Modellaufruf kosten.
        from inspect_ai.model import ModelOutput

        ergebnis = self._api().handle_bad_request(
            self._fehler("exceeds the available context size (8192 tokens)")
        )
        assert isinstance(ergebnis, ModelOutput)
        assert ergebnis.stop_reason == "model_length"

    @pytest.mark.parametrize(
        "nachricht",
        [
            "model 'gibtsnicht' not found",
            "invalid value for temperature",
            "unknown tool 'foo'",
        ],
    )
    def test_andere_fehler_bleiben_fehler(self, nachricht: str) -> None:
        """Die Gegenprobe, ohne die der Fix ein Vertuschen wäre.

        Würde jeder 400er zu `model_length`, verschwänden Tippfehler im
        Modellnamen und falsche Parameter still in einer Kürzung — der Lauf
        liefe weiter und maße etwas anderes, als er behauptet.
        """
        from inspect_ai.model import ModelOutput

        ergebnis = self._api().handle_bad_request(self._fehler(nachricht))
        assert not (
            isinstance(ergebnis, ModelOutput) and ergebnis.stop_reason == "model_length"
        ), ergebnis


def _read_timeout(client) -> float:
    """Wartezeit auf die Antwort; der Client führt sie als Zahl oder als `Timeout`."""
    timeout = client.timeout
    return timeout.read if hasattr(timeout, "read") else timeout


class TestClientGrenzen:
    """Die Zeitgrenze von Inspect muss die sein, die greift, nicht die des SDK."""

    def test_client_wartet_laenger_als_jede_aufgabe(self) -> None:
        client = _api().client
        assert _read_timeout(client) >= max(ATTEMPT_TIMEOUT_SECONDS, MODEL_CALL_TIMEOUT)

    def test_client_wiederholt_nicht_still(self) -> None:
        assert _api().client.max_retries == 0

    def test_gegenprobe_ohne_eigene_angabe_gilt_das_sdk(self) -> None:
        """Mit den SDK-Vorgaben liefe eine lange Antwort dreimal in 600 s und erst dann in die Zeitgrenze."""
        from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

        client = OpenAICompatibleAPI("local/x", base_url="http://x/v1", api_key="k").client
        assert _read_timeout(client) == 600
        assert client.max_retries == 2
        assert _read_timeout(client) < MODEL_CALL_TIMEOUT

    def test_kommandozeile_schlaegt_voreinstellung(self) -> None:
        api = _api(client_timeout=CLIENT_TIMEOUT_SECONDS * 2, max_retries=1)
        assert _read_timeout(api.client) == CLIENT_TIMEOUT_SECONDS * 2
        assert api.client.max_retries == 1
