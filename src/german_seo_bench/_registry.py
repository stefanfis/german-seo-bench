"""Einstiegspunkt für Inspect (`[project.entry-points.inspect_ai]`).

Inspect importiert dieses Modul, bevor es Aufgaben und Modell-Provider auflöst.
Dadurch sind die Aufgaben unter ihrem Registry-Namen erreichbar
(`german_seo_bench/m01_titles`, `german_seo_bench/m01_judge`,
`german_seo_bench/m02_knowledge_mc`, `german_seo_bench/m03_facts`,
`german_seo_bench/m10_alt_text`, `german_seo_bench/a01_browser_audit`) und
`--model local/…` ist bekannt — auch dann, wenn das Paket nur installiert und
nicht ausgecheckt ist. Genau das macht den `uvx`-Weg möglich.
"""

from german_seo_bench.providers import local
from german_seo_bench.tasks import (
    a01_browser_audit,
    m01_judge,
    m01_titles,
    m02_knowledge_mc,
    m03_facts,
    m10_alt_text,
)

__all__ = [
    "a01_browser_audit",
    "local",
    "m01_judge",
    "m01_titles",
    "m02_knowledge_mc",
    "m03_facts",
    "m10_alt_text",
]
