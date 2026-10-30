"""Ein Modell-Provider `local/…` mit brauchbarer Voreinstellung.

Ohne ihn müsste jeder, der das Repo klont, zuerst zwei Umgebungsvariablen
setzen, bevor überhaupt etwas läuft — für einen Wert, der bei llama-swap,
llama-server, Ollama und LM Studio ohnehin fast immer derselbe ist.

    --model local/qwen35-4b                          → http://localhost:8080/v1
    --model local/qwen35-4b --model-base-url http://…:8080/v1

Die Reihenfolge ist: `--model-base-url` schlägt `LOCAL_BASE_URL` schlägt
Voreinstellung. Ein Schlüssel wird nicht verlangt — lokale Server prüfen keinen,
der OpenAI-Client besteht aber auf einem nicht-leeren Wert.

Für mehrere Server gleichzeitig bleibt Inspects eigener Weg offen:
`--model openai-api/<dienst>/<modell>` mit `<DIENST>_BASE_URL`.
"""

import os
import re

from inspect_ai.model import GenerateConfig, ModelAPI, ModelOutput, modelapi

DEFAULT_BASE_URL = "http://localhost:8080/v1"

# Lokale Server prüfen den Schlüssel nicht. Der Wert steht hier nur, weil der
# OpenAI-Client ohne Schlüssel gar nicht erst eine Anfrage stellt.
PLACEHOLDER_API_KEY = "kein-schluessel-noetig"

# Wie oft in diesem Datensatz der Kontext übergelaufen ist. Gezählt wird HIER,
# wo es passiert, und nicht später aus dem Transkript gelesen: Dessen
# Ereignisliste ist im bounded-Modus nicht verlässlich abfragbar, und ein
# Zähler, der manchmal nichts findet, ist schlimmer als keiner.
#
# Die Zahl ist eine Laufbedingung, keine Modelleigenschaft: Ein Lauf, in dem
# gekürzt wurde, hat Teile seiner eigenen Beobachtungen vergessen und ist mit
# einem ungekürzten nicht vergleichbar. Deshalb gehört sie in die
# Ergebnisdatei und in den Kundenbericht.
STORE_CONTEXT_OVERFLOWS = "context_overflows"

# Zeitgrenze und Wiederholungen des OpenAI-Clients selbst. Ohne eigene Angabe
# gelten die Vorgaben des SDK: 600 s je Anfrage und zwei stille
# Wiederholungen, alles innerhalb EINES Versuchs von Inspect. Eine Antwort, die
# länger als 600 s braucht — eine Denkschleife bis `max_tokens` bei langem
# Kontext —, bricht der Client dann ab und schickt sie neu, dreimal, bis
# Inspects `attempt_timeout` den ganzen Lauf beendet. Die eigene Zeitgrenze
# der Aufgabe (A01: 1800 s) käme so nie zum Zug, und im Log stünde nur der
# letzte Abbruch.
#
# Deshalb liegt die Grenze des Clients über jeder `attempt_timeout`, die eine
# Aufgabe setzt, und wiederholt wird nur noch von Inspect, sichtbar im Log.
# Hängen kann trotzdem nichts: `attempt_timeout` gilt weiter. Wer sie per
# Kommandozeile über diesen Wert hebt, hebt ihn mit `-M client_timeout=…` mit.
CLIENT_TIMEOUT_SECONDS = 7200
CLIENT_MAX_RETRIES = 0


def _zaehle_ueberlauf() -> None:
    """Den Überlauf im Datensatz-Store vermerken, falls es einen gibt.

    Außerhalb eines Datensatzes (Einzeltests, Werkzeugaufrufe) gibt es keinen
    Store — das ist kein Fehler, sondern der Normalfall, und darf den
    Modellaufruf nicht kosten.
    """
    try:
        from inspect_ai.util import store

        s = store()
        s.set(STORE_CONTEXT_OVERFLOWS, int(s.get(STORE_CONTEXT_OVERFLOWS, 0)) + 1)
    except Exception:  # noqa: BLE001 — Diagnose darf nie die Messung kosten
        pass

# Woran ein Kontextüberlauf bei lokalen Servern zu erkennen ist.
#
# Inspect erkennt ihn nur an OpenAIs Fehlercode `context_length_exceeded`
# (plus DeepInfras „input length"). llama.cpp und llama-swap melden etwas
# anderes:
#
#     type: "exceed_context_size_error"
#     message: "the request exceeds the available context size (32768 tokens),
#               try increasing it"
#
# Ohne diese Übersetzung wird der Fehler nicht als Kontextüberlauf erkannt, sondern
# durchgereicht — der ganze Lauf endet mit `status: error` und **ohne Score**.
# Damit greift auch `truncation="auto"` im Agenten nie, denn der reagiert auf
# `stop_reason == "model_length"`.
#
# Großzügig gefasst, weil hier vier Server auflaufen (llama.cpp, Ollama,
# LM Studio, vLLM) und jeder es anders formuliert. Ein Fehlalarm ist billig:
# Er kostet eine Kürzung der Nachrichten statt eines Abbruchs. Ein
# übersehener Überlauf kostet den ganzen Lauf.
_CONTEXT_OVERFLOW = re.compile(
    r"exceed_context_size_error"
    r"|context_length_exceeded"
    r"|exceeds the available context size"
    r"|maximum context length"
    r"|context (?:size|length|window)[^.]{0,40}(?:exceed|too (?:small|long))"
    r"|(?:exceed|too long)[^.]{0,40}context (?:size|length|window)"
    r"|prompt is too long"
    r"|input length",
    re.IGNORECASE,
)


def _fehlertext(ex: object) -> str:
    """Alles an einer Ausnahme, worin der Grund stehen kann.

    `str(ex)` allein genügt nicht: llama-swap trägt den Grund als `type` im
    Rumpf (`exceed_context_size_error`) und formuliert die Meldung frei. Wer
    nur die Meldung liest, übersieht genau die Server, um die es hier geht.
    """
    teile = [str(ex)]
    for feld in ("code", "type"):
        wert = getattr(ex, feld, None)
        if isinstance(wert, str):
            teile.append(wert)
    rumpf = getattr(ex, "body", None)
    if isinstance(rumpf, dict):
        teile += [str(rumpf.get(k, "")) for k in ("message", "type", "code")]
    return " ".join(teile)


@modelapi(name="local")
def local() -> type[ModelAPI]:
    """Registriert `local/<modell>`. Wird über den Entry-Point geladen."""
    from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

    class LocalServerAPI(OpenAICompatibleAPI):
        def __init__(
            self,
            model_name: str,
            base_url: str | None = None,
            api_key: str | None = None,
            config: GenerateConfig = GenerateConfig(),
            **kwargs: object,
        ) -> None:
            super().__init__(
                model_name=model_name,
                base_url=base_url,
                api_key=api_key
                or os.environ.get("LOCAL_API_KEY")
                or PLACEHOLDER_API_KEY,
                config=config,
                # Muss gesetzt sein: Die Basisklasse liest den Dienst sonst aus
                # dem Modellnamen, und `local/qwen35-4b` kommt hier ohne das
                # Präfix an.
                service="local",
                service_base_url=DEFAULT_BASE_URL,
                **{"client_timeout": CLIENT_TIMEOUT_SECONDS,
                   "max_retries": CLIENT_MAX_RETRIES, **kwargs},
            )

        def handle_bad_request(self, ex: object) -> object:
            """Kontextüberlauf in `stop_reason="model_length"` übersetzen.

            Erst damit wird aus einem abgebrochenen Lauf ein verwertbarer:
            Inspects `react`-Schleife kürzt bei `model_length` die
            Nachrichten und macht weiter (`truncation="auto"`), und wenn das
            nicht mehr reicht, beendet sie den Agenten **sauber** mit dem, was
            er bis dahin hat. Ohne die Übersetzung stürbe der ganze Lauf an
            einem Fehler, den niemand einer Ursache zuordnen könnte.

            Die Kürzung ist kein Gratisgewinn: Ein Lauf, in dem gekürzt wurde,
            misst nicht mehr dasselbe wie einer ohne — er hat Teile seiner
            eigenen Beobachtungen vergessen. Das steht als
            `stop_reason: model_length` in den Modellereignissen des Logs und
            gehört beim Zitieren einer Zahl dazu.
            """
            if _CONTEXT_OVERFLOW.search(_fehlertext(ex)):
                _zaehle_ueberlauf()
                return ModelOutput.from_content(
                    self.model_name, content=str(ex), stop_reason="model_length"
                )
            return super().handle_bad_request(ex)  # type: ignore[arg-type]

    return LocalServerAPI
