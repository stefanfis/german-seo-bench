"""Was der Server tatsächlich tut — als Beleg neben dem, was der Runner anfordert.

Der Runner kann Sampling-Parameter setzen; im Agent-Pfad kann er es nicht. Ein
Agent stellt seine Modellanfragen selbst, und ihm die Werte zuverlässig
vorzuschreiben ginge allenfalls über die Eigenheiten des jeweiligen Frameworks.
Deshalb gehören diese Vorgaben nicht in den Benchmark, sondern in den Server:
llama-swap führt je Parametersatz einen eigenen Eintrag, und der Benchmark
wählt nur noch dessen Namen.

Damit verschiebt sich das Problem allerdings, statt zu verschwinden. Der Alias
ist eine Behauptung — ein Tippfehler in der llama-swap-Konfiguration, und es
wird monatelang unter falschem Namen gemessen. Deshalb wird nach jeder Antwort
abgefragt, was der Server wirklich geladen hat:

    Alias      = Absicht    (steht ohnehin in `meta.model`)
    /running   = Auftrag    (die Kommandozeile des Upstreams, wörtlich)
    /props     = Ergebnis   (die aufgelösten Werte, inklusive der Vorgaben,
                             die gar nicht auf der Kommandozeile stehen —
                             `min_p` etwa ist ein llama.cpp-Standard)

Abgefragt wird NACH der Antwort, nicht davor. llama-swap lädt ein Modell erst
mit der ersten Anfrage; eine Abfrage davor liefert die Werte des Modells, das
vorher dran war, und etikettiert die Messung still falsch.

Je Datensatz aufgezeichnet und nicht einmal je Lauf: Tauscht llama-swap mitten
im Lauf das Modell aus, fällt genau das auf.

Davor steht die zweite, kleinere Aufzeichnung: `record_endpoint()` schreibt die
**aufgelöste Basis-URL**, bevor die erste Anfrage rausgeht. Sie ist das
Gegenstück für den Fall, dass gar keine Antwort kommt — dann läuft
`record_server()` nie, und ohne sie hielte die Ergebnisdatei nicht fest, wohin
überhaupt gefragt wurde. Inspects `model_base_url` hält nur das CLI-Argument
fest, nicht die Adresse, die der Provider daraus gemacht hat — „Server war aus"
und „Adresse war falsch" sähen sonst im Log gleich aus.
"""

import hashlib
from typing import Any

import httpx

from inspect_ai.model import get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver

from german_seo_bench.providers import DEFAULT_BASE_URL

TIMEOUT_SECONDS = 5.0

# Aus `/props` übernommen. `params` wird VOLLSTÄNDIG gespeichert und nicht in
# ein eigenes Schema übersetzt: Die Feldnamen ändern sich zwischen
# llama.cpp-Versionen, und ein Übersetzer, der ein
# umbenanntes Feld übergeht, schreibt `null` — das liest sich wie "nicht
# gesetzt" statt wie "nicht gefunden".
_PROPS_FIELDS = ("model_path", "model_ftype", "build_info", "total_slots")


def _root(base_url: str) -> str:
    """`http://host:8080/v1` -> `http://host:8080`.

    `/props` und `/running` hängen an der Wurzel, nicht unter `/v1`.
    """
    return base_url.rstrip("/").removesuffix("/v1")


def looks_like_props(body: Any) -> bool:
    """Ist das wirklich eine llama.cpp-`/props`-Antwort?

    Ein Proxy, der jeden Pfad mit 200 beantwortet, liefert sonst irgendein
    JSON, aus dem nichts zu holen ist — und das Ergebnis läse sich wie ein
    Server ohne gesetzte Parameter.
    """
    return isinstance(body, dict) and any(
        key in body for key in ("default_generation_settings", "model_path", "build_info")
    )


def extract_props(props: dict[str, Any]) -> dict[str, Any]:
    """Die Messbedingung aus einer `/props`-Antwort.

    Das Chat-Template wird nur als Kurz-Hash abgelegt. Es entscheidet bei qwen3
    über das Reasoning-Verhalten, ist also Teil der Messbedingung — aber
    mehrere Kilobyte je Datensatz wären es nicht wert. Ein geänderter Hash
    genügt als Signal, dass hier nicht mehr dasselbe gemessen wird.
    """
    generation = props.get("default_generation_settings") or {}
    recorded: dict[str, Any] = {
        key: props[key] for key in _PROPS_FIELDS if key in props
    }
    if "params" in generation:
        recorded["params"] = generation["params"]
    if "n_ctx" in generation:
        recorded["n_ctx"] = generation["n_ctx"]
    if "chat_template_caps" in props:
        recorded["chat_template_caps"] = props["chat_template_caps"]
    template = props.get("chat_template")
    if isinstance(template, str):
        recorded["chat_template_sha256"] = hashlib.sha256(
            template.encode("utf-8")
        ).hexdigest()[:16]
    return recorded


def extract_running(running: Any, model: str) -> dict[str, Any] | None:
    """Der Eintrag aus `/running`, der zu diesem Modell gehört.

    Interessant ist vor allem `cmd`: die wörtliche Kommandozeile des Upstreams.
    Sie ist der einzige Teil der Aufzeichnung, der keine Schema-Annahme trifft
    und deshalb auch einen Versionssprung überlebt.
    """
    entries = running.get("running") if isinstance(running, dict) else running
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("model") == model:
            return {
                key: entry[key]
                for key in ("cmd", "state", "ttl", "proxy")
                if key in entry
            }
    return None


def endpoint_record(model: str, base_url: str | None) -> dict[str, Any]:
    """Was der Runner anfragen WIRD — die aufgelöste Adresse, nicht die Absicht.

    `base_url` ist der Wert, den der Provider aus drei Quellen aufgelöst hat:
    `--model-base-url`, dann `LOCAL_BASE_URL`, dann seine Voreinstellung. Im
    Log steht von Inspect aus nur die erste davon (`eval.model_base_url`) — und die ist
    genau dann leer, wenn die Adresse aus einer der beiden anderen kam.

    `provider_default` sagt, ob die Adresse überhaupt jemand gewählt hat. Das
    ist eine Tatsache und keine Herkunftsvermutung: Steht dort `true`, hat
    niemand eine Adresse genannt, und der Wert ist der, der auf dem Rechner
    richtig war, auf dem der Provider geschrieben wurde.
    """
    if not base_url:
        # Kein Fehler: Anthropic, OpenAI und `none/none` haben keine Adresse,
        # die hier aufzulösen wäre. Der Vermerk unterscheidet das von einem
        # Feld, das zu füllen vergessen wurde.
        return {
            "model": model,
            "base_url": None,
            "note": "Provider ohne eigene Basis-URL (SDK-Vorgabe oder kein HTTP)",
        }
    return {
        "model": model,
        "base_url": base_url,
        "provider_default": base_url == DEFAULT_BASE_URL,
    }


@solver
def record_endpoint() -> Solver:
    """Schreibt die Zieladresse nach `state.metadata['endpoint']`.

    Gehört als ERSTER Schritt in die Solver-Kette, vor `system_message()` und
    `generate()`. Der ganze Zweck ist, dass die Angabe auch dann in der
    Ergebnisdatei steht, wenn die Anfrage nie beantwortet wird — deshalb darf
    nichts davor stehen, was selbst scheitern kann.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        api = get_model().api
        state.metadata["endpoint"] = endpoint_record(
            str(state.model), getattr(api, "base_url", None)
        )
        return state

    return solve


async def probe_server(base_url: str, model: str) -> dict[str, Any]:
    """Fragt den Serverzustand ab. Scheitert nie, meldet aber jeden Fehlschlag.

    Ein stiller Fehlschlag wäre hier besonders teuer: Es entstünde eine Messung
    ohne Bedingungsangabe, die aussieht wie eine mit.
    """
    root = _root(base_url)
    recorded: dict[str, Any] = {"base_url": base_url, "model": model}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def fetch(path: str) -> Any | None:
            try:
                response = await client.get(f"{root}{path}")
                response.raise_for_status()
                return response.json()
            except Exception as error:  # Netz, 404, kaputtes JSON — alles hier
                recorded.setdefault("errors", {})[path] = (
                    f"{type(error).__name__}: {error}"
                )
                return None

        # llama-swap kennt den Upstream je Modell; llama-server nur sich selbst.
        props = await fetch(f"/upstream/{model}/props") or await fetch("/props")
        if not looks_like_props(props):
            # Nicht jede 200 ist eine Antwort. Ein Proxy, der jeden Pfad
            # bedient, liefert hier irgendein JSON — und ohne diese Prüfung
            # stünde eine leere Aufzeichnung da, die wie "keine Parameter
            # gesetzt" aussieht statt wie "nicht gefunden".
            recorded["props_note"] = (
                "keine llama.cpp-Props unter /upstream/<modell>/props oder /props"
            )
        else:
            recorded.update(extract_props(props))
        running = await fetch("/running")
        if running is not None:
            entry = extract_running(running, model)
            recorded["upstream"] = (
                entry if entry else {"note": f"{model!r} nicht in /running"}
            )
    return recorded


@solver
def record_server() -> Solver:
    """Schreibt den Serverzustand nach `state.metadata['server']`.

    Gehört HINTER `generate()` in die Solver-Kette — in A01 hinter den Agenten,
    als letzter Schritt und ohne dass sich sonst etwas ändert.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        api = get_model().api
        base_url = getattr(api, "base_url", None)
        if not base_url:
            state.metadata["server"] = {
                "note": "Provider ohne base_url — nichts abzufragen"
            }
            return state
        state.metadata["server"] = await probe_server(
            base_url, getattr(api, "model_name", str(state.model))
        )
        return state

    return solve
