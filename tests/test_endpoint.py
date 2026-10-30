"""Zusicherungen über die Aufzeichnung der Zieladresse.

Ein Lauf gegen einen Rechner ohne lokalen Inferenzserver soll eine
Ergebnisdatei hinterlassen, aus der hervorgeht, *wohin* gefragt worden war.
`eval.model_base_url` hält nur das
CLI-Argument fest und ist genau dann leer, wenn die Adresse aus `LOCAL_BASE_URL`
oder aus der Voreinstellung des Providers kam.

Die scharfe Probe ist deshalb nicht, dass die Adresse bei einem gelungenen Lauf
dasteht — sie muss bei einem **gescheiterten** dastehen. Dafür läuft hier ein
Eval gegen einen Port, auf dem nichts horcht.
"""

import pytest
from inspect_ai import eval as inspect_eval

from german_seo_bench.providers import DEFAULT_BASE_URL
from german_seo_bench.server import endpoint_record
from german_seo_bench.tasks.m03_facts import m03_facts

# Port 1 ist reserviert und auf keinem Entwicklungsrechner belegt: Der Versuch
# wird sofort abgewiesen, statt in eine Zeitgrenze zu laufen. Ein Test, der
# fünf Minuten auf ein Timeout wartet, wird abgeschaltet.
DEAD_URL = "http://127.0.0.1:1/v1"


class TestEndpointRecord:
    def test_voreinstellung_wird_als_solche_erkannt(self) -> None:
        # Der häufigste Fall: Niemand hat eine Adresse genannt, also gilt die
        # Voreinstellung des Providers.
        recorded = endpoint_record("local/qwen3-1.7b", DEFAULT_BASE_URL)
        assert recorded["base_url"] == DEFAULT_BASE_URL
        assert recorded["provider_default"] is True

    def test_gewaehlte_adresse_ist_keine_voreinstellung(self) -> None:
        recorded = endpoint_record("local/qwen3-1.7b", "http://192.168.1.50:8080/v1")
        assert recorded["base_url"] == "http://192.168.1.50:8080/v1"
        assert recorded["provider_default"] is False

    def test_provider_ohne_adresse_bekommt_einen_vermerk(self) -> None:
        # `none/none`, Anthropic und OpenAI haben keine Adresse, die hier
        # aufzulösen wäre. Ohne den Vermerk stünde ein leeres Feld da, und das
        # liest sich wie "vergessen" statt wie "gibt es nicht".
        recorded = endpoint_record("anthropic/claude-sonnet-5", None)
        assert recorded["base_url"] is None
        assert "note" in recorded

    def test_modellname_traegt_den_anbieter(self) -> None:
        # Der Alias allein ("qwen3-1.7b") sagt nicht, über welchen Provider
        # gefragt wurde — und damit auch nicht, welche Adressauflösung galt.
        assert endpoint_record("local/qwen3-1.7b", DEFAULT_BASE_URL)["model"] == (
            "local/qwen3-1.7b"
        )


class TestAdresseImLog:
    """Die eigentliche Zusicherung: Sie muss den Fehlerfall überleben."""

    @pytest.fixture(scope="class")
    @classmethod
    def failed_log(cls, tmp_path_factory):
        # Ein einziger Datensatz genügt, und mehr wäre nur Wartezeit: Alle
        # scheitern an derselben geschlossenen Tür.
        return inspect_eval(
            m03_facts(),
            model="local/qwen3-1.7b",
            model_base_url=DEAD_URL,
            log_dir=str(tmp_path_factory.mktemp("eval-logs")),
            limit=1,
            max_retries=0,
            timeout=10,
            attempt_timeout=10,
        )[0]

    def test_lauf_ist_wirklich_gescheitert(self, failed_log) -> None:
        # Gegenprobe zur Probe selbst: Liefe hier versehentlich ein echter
        # Server, prüfte der Test unten den gelungenen Fall — also gar nichts.
        assert failed_log.status == "error"

    def test_adresse_steht_trotzdem_im_log(self, failed_log) -> None:
        sample = failed_log.samples[0]
        endpoint = sample.metadata["endpoint"]
        assert endpoint["base_url"] == DEAD_URL
        assert endpoint["model"] == "local/qwen3-1.7b"

    def test_aufgezeichnet_wird_die_aufgeloeste_adresse(self, failed_log) -> None:
        """Nicht die Voreinstellung, sondern das Ergebnis der Auflösung.

        Ohne diese Zusicherung bestünde der Test auch dann, wenn schlicht
        `DEFAULT_BASE_URL` ins Log geschrieben würde — und genau die
        Verwechslung von Absicht und Ergebnis soll die Aufzeichnung ja
        ausschließen.
        """
        endpoint = failed_log.samples[0].metadata["endpoint"]
        assert endpoint["base_url"] != DEFAULT_BASE_URL
        assert endpoint["provider_default"] is False

    def test_serverzustand_fehlt_erwartungsgemaess(self, failed_log) -> None:
        # Der Beleg dafür, dass die Aufzeichnung vor der Anfrage nötig ist:
        # `record_server()` steht hinter `generate()` und läuft in diesem Fall
        # nie. Käme `server` hier doch vor, liefe die Kette in falscher
        # Reihenfolge — und die Adresse stünde wieder nur im Erfolgsfall da.
        assert "server" not in failed_log.samples[0].metadata
