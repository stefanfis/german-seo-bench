"""Der Modellname, unter dem ein Lauf im Bericht steht — samt Denkmodus.

Lokale Modelle tragen ihren Denkmodus im Namen (`local/qwen3.5-9b-think-off`),
weil llama-swap je Modus einen eigenen Alias braucht. Cloud-Modelle haben eine
solche Endung nicht: `anthropic/claude-haiku-5-5` heißt mit und ohne Denken
gleich, der Modus steht im Log unter `reasoning_effort`. Liefen beide Fassungen
dieselbe Aufgabe, landeten sie unter einem Namen in einer Zelle, und der
Bericht nähme den schlechteren Lauf über zwei Einstellungen — eine Zahl, die
keiner von beiden entspricht.

Deshalb bekommt ein Name ohne Endung hier die Endung, die im Log steht:

    reasoning_effort gesetzt          -think-<wert>      (none → aus, sonst an)
    nicht gesetzt, Claude ab 4.7      -think-adaptive    (an: Vorgabe der API)
    sonst                             unverändert        (Modus unbekannt, „?“)

Die Denkgrenze lokaler Modelle steht nicht im Namen, sondern in der Anfrage:
llama-server nimmt `thinking_budget_tokens` im Rumpf an
(`--extra-body '{"thinking_budget_tokens": 512}'`), und Inspect hält den Rumpf
im Log fest. Sie wird zum Zusatz `-budget<n>` hinter dem Modus
(`local/gemma4-12b-think-on-budget512`), damit Läufe mit und ohne Grenze nicht
in einer Zelle landen. Die gleichnamige Startoption `--reasoning-budget` steht
dagegen in keinem Log; ist sie gesetzt, übergeht der Server das Feld. Bei „aus“ entfällt der Zusatz: Ohne
Denken greift keine Denkgrenze, und derselbe Lauf bekäme sonst zwei Namen.
`-1` ist llama.cpps „unbegrenzt“ und damit dasselbe wie keine Angabe.

Claude ab 4.7 denkt ohne Angabe adaptiv — es entscheidet selbst, ob und wie
viel; abschalten muss man es ausdrücklich. Bei anderen Anbietern ist die
Vorgabe je Modell verschieden und nirgends im Log ablesbar; dort einen Modus
anzunehmen, wäre geraten.

Alle Leser, die Läufe einander zuordnen (Bericht, Einzelergebnisse,
Laufdaten), nehmen den Namen von hier. Nähme einer weiter `log.eval.model`,
fänden Antworten und Laufdaten ihre Zeile nicht mehr.
"""

from __future__ import annotations

import re

from inspect_ai.log import EvalLog

# Hinter `-think-` steht die Variante, wie sie in der llama-swap-Konfiguration
# heißt, gegebenenfalls mit Zusatz (`-think-on-precise`).
THINK_SUFFIX = re.compile(r"^(?P<base>.+?)-think-(?P<variant>[A-Za-z0-9]+(?:-[A-Za-z0-9.]+)*)$")

# Was Claude ohne `reasoning_effort` tut, wenn es von sich aus denkt.
ADAPTIVE = "adaptive"

# Varianten ohne Denken. `enable_thinking` kennt on/off, `reasoning_effort`
# none bis xhigh.
THINK_OFF = frozenset({"off", "none"})

# Feld im Anfragerumpf (`inspect eval --extra-body`), wie llama-server es liest,
# und der Wert für „unbegrenzt“.
BUDGET_KEY = "thinking_budget_tokens"
UNLIMITED = -1
BUDGET_MARK = "-budget"

# `anthropic/claude-<familie>-<major>[-<minor>][-<datum>]`. Das Datum eines
# Snapshots (`-20251001`) ist keine Unterversion.
_CLAUDE = re.compile(r"^anthropic/claude-[a-z]+-(?P<major>\d)(?:-(?P<minor>\d{1,2}))?(?:-\d{8})?$")


def thinks_by_default(model: str) -> bool:
    """Claude ab 4.7 denkt ohne Angabe; frühere Claude-Modelle und andere Anbieter nicht sicher."""
    match = _CLAUDE.match(model)
    if not match:
        return False
    major, minor = int(match["major"]), int(match["minor"] or 0)
    return (major, minor) >= (4, 7)


def thinks_off(model: str) -> bool:
    """Endung `-think-off`/`-think-none`, auch mit Zusatz (`-think-off-precise`)."""
    match = THINK_SUFFIX.match(model)
    return bool(match) and match["variant"].split("-", 1)[0] in THINK_OFF


def model_label(log: EvalLog) -> str:
    """`log.eval.model`, ergänzt um Denkmodus und Denkgrenze aus dem Log."""
    model = _with_mode(log)
    budget = reasoning_budget(log)
    if budget is None or thinks_off(model):
        return model
    return f"{model}{BUDGET_MARK}{budget}"


def reasoning_budget(log: EvalLog) -> int | None:
    """Die Denkgrenze aus dem Anfragerumpf des Laufs; None ohne Angabe oder bei -1."""
    config = log.plan.config if log.plan else None
    extra = getattr(config, "extra_body", None) or log.eval.model_generate_config.extra_body or {}
    value = extra.get(BUDGET_KEY)
    if value is None:
        return None
    try:
        budget = int(value)
    except (TypeError, ValueError):
        budget = 0
    if budget == UNLIMITED:
        return None
    if budget <= 0 or str(budget) != str(value).strip():
        # 0 hieße „gar nicht denken“ unter einem Namen, der „an“ sagt; ein
        # Tippfehler würde still zu einer eigenen Variante.
        raise ValueError(
            f"{log.location}: {BUDGET_KEY}={value!r} — erwartet eine ganze Zahl über 0 "
            f"oder {UNLIMITED} (unbegrenzt).")
    return budget


def _with_mode(log: EvalLog) -> str:
    """`log.eval.model`, bei einem Namen ohne Denkmodus ergänzt um den aus dem Log."""
    model = log.eval.model
    if THINK_SUFFIX.match(model):
        return model
    config = log.plan.config if log.plan else None
    effort = getattr(config, "reasoning_effort", None) or log.eval.model_generate_config.reasoning_effort
    if effort:
        return f"{model}-think-{effort}"
    if thinks_by_default(model):
        return f"{model}-think-{ADAPTIVE}"
    return model
