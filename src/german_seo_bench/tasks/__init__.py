"""Die Aufgaben. `M…` = eine Anfrage an das Modell, `A…` = Agent mit Werkzeugen."""

from german_seo_bench.tasks.a01_browser_audit import a01_browser_audit
from german_seo_bench.tasks.m01_judge import m01_judge
from german_seo_bench.tasks.m01_titles import m01_titles
from german_seo_bench.tasks.m02_knowledge_mc import m02_knowledge_mc
from german_seo_bench.tasks.m03_facts import m03_facts
from german_seo_bench.tasks.m10_alt_text import m10_alt_text

__all__ = [
    "a01_browser_audit",
    "m01_judge",
    "m01_titles",
    "m02_knowledge_mc",
    "m03_facts",
    "m10_alt_text",
]
