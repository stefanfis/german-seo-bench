"""Zusicherungen über die Laufvorgaben.

Der Zweck dieser Vorgaben ist, dass ein Lauf ohne Zutun durchläuft. Kippt eine
davon unbemerkt zurück auf Inspects Anbieter-Vorgabe, hängt der nächste Lauf
bei 19 von 20 Datensätzen — und sieht dabei aus wie ein Modellproblem. Genau
deshalb stehen sie hier als Test und nicht nur als Kommentar.
"""

import pytest

from german_seo_bench.config import (
    ATTEMPT_TIMEOUT_SECONDS,
    MAX_CONNECTIONS,
    MAX_RETRIES,
    MAX_TOKENS,
    TIMEOUT_SECONDS,
    run_config,
)
from german_seo_bench.tasks.m01_titles import m01_titles


class TestVorgaben:
    def test_keine_parallelen_anfragen(self) -> None:
        # Der Wert steuert zugleich die Sample-Parallelität: Ohne adaptiven
        # Regler leitet Inspect sie aus `max_connections` ab.
        assert MAX_CONNECTIONS == 1

    def test_adaptiver_regler_ist_damit_aus(self) -> None:
        # Gegenprobe zur Zusicherung oben: Ein gesetztes `max_connections`
        # schaltet Inspects adaptive Nebenläufigkeit ab (die bei 20 startet).
        # Wäre der Wert None, griffe wieder der adaptive Regler.
        from inspect_ai.util._concurrency import adaptive_active

        config = run_config()
        assert not adaptive_active(
            config.adaptive_connections, config.max_connections, config.batch
        )

    def test_haengender_versuch_wird_abgebrochen(self) -> None:
        # DIE entscheidende Zusicherung. `timeout` allein greift nicht: Es hat
        # tenacity-Semantik und wird nur zwischen zwei Versuchen geprüft — ein
        # Versuch, der nie zurückkommt, läuft daran vorbei. Nur
        # `attempt_timeout` legt einen echten Abbruch um den Versuch.
        assert ATTEMPT_TIMEOUT_SECONDS is not None
        assert 0 < ATTEMPT_TIMEOUT_SECONDS <= 900

    def test_volle_antwortlaenge_passt_in_einen_versuch(self) -> None:
        # Bei 10 Tokens pro Sekunde muss `MAX_TOKENS` vor der Zeitgrenze
        # erreicht sein. Sonst bricht eine ausufernde Antwort den ganzen Lauf
        # ab, statt abgeschnitten gewertet zu werden.
        assert ATTEMPT_TIMEOUT_SECONDS >= MAX_TOKENS / 10

    def test_gesamtbudget_nicht_kleiner_als_ein_versuch(self) -> None:
        # Gegenprobe: Wäre `timeout` kleiner als `attempt_timeout`, käme der
        # Versuch nie zu Ende, bevor das Gesamtbudget ihn zwischen den
        # Versuchen abbricht — die Zeitgrenze je Versuch wäre wirkungslos.
        assert TIMEOUT_SECONDS >= ATTEMPT_TIMEOUT_SECONDS

    def test_laengere_versuche_heben_das_gesamtbudget_mit(self) -> None:
        # Gegenprobe zur Gleichheit der beiden Vorgaben: Setzt eine Aufgabe
        # nur `attempt_timeout` herauf, muss `timeout` folgen — sonst ist das
        # Budget nach dem ersten langen Versuch weg.
        config = run_config(attempt_timeout=900)
        assert config.attempt_timeout == 900
        assert config.timeout == 900

    def test_laengere_antworten_lassen_den_rest_stehen(self) -> None:
        config = run_config(max_tokens=16384)
        assert config.max_tokens == 16384
        assert config.attempt_timeout == ATTEMPT_TIMEOUT_SECONDS
        assert run_config().max_tokens == MAX_TOKENS

    def test_endliche_wartezeit(self) -> None:
        # Ohne `timeout` wartet Inspect unbegrenzt. Ein Fehler, den niemand zu
        # sehen bekommt, ist kein Fehler, sondern ein Hänger.
        assert TIMEOUT_SECONDS is not None and 0 < TIMEOUT_SECONDS <= 900

    def test_antwortlaenge_begrenzt(self) -> None:
        # Ohne `max_tokens` entscheidet der Server, und llama-server
        # generiert bis der Kontext voll ist. Ein Modell, das sich festdenkt,
        # kostet dann Minuten statt Sekunden.
        assert MAX_TOKENS is not None and MAX_TOKENS >= 2048

    def test_endliche_wiederholungen(self) -> None:
        # Ohne `max_retries` wiederholt Inspect unbegrenzt, mit Backoff bis zu
        # 30 Minuten je Versuch.
        assert MAX_RETRIES is not None and 0 < MAX_RETRIES <= 5


class TestAufgabeUebernimmtVorgaben:
    """Die Vorgaben nützen nichts, wenn die Aufgabe sie nicht mitgibt."""

    @pytest.mark.parametrize(
        "field,expected",
        [
            ("max_connections", MAX_CONNECTIONS),
            ("attempt_timeout", ATTEMPT_TIMEOUT_SECONDS),
            ("timeout", TIMEOUT_SECONDS),
            ("max_retries", MAX_RETRIES),
            ("max_tokens", MAX_TOKENS),
        ],
    )
    def test_m01_traegt_die_vorgabe(self, field: str, expected: int) -> None:
        assert getattr(m01_titles().config, field) == expected

    def test_auch_im_oracle_lauf(self) -> None:
        # Der Oracle-Lauf stellt keine Anfrage, aber die Aufgabe soll in
        # beiden Betriebsarten dieselbe sein — sonst prüft der Smoke-Test
        # eine andere Aufgabe als die, die gemessen wird.
        assert m01_titles(oracle=True).config == m01_titles().config
