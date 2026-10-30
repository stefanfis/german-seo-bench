"""Prüflogik der Aufgaben — ohne Inspect, einzeln testbar."""

from german_seo_bench.checks.alt_text import boilerplate_hit
from german_seo_bench.checks.audit import (
    AuditVerdict,
    as_findings,
    behaviour,
    grade_audit,
    pages_seen,
    parse_findings,
    tool_base_name,
    tool_usage,
)
from german_seo_bench.checks.facts import (
    FIELDS,
    NULLABLE,
    FactsVerdict,
    as_number,
    extract_json_object,
    grade_facts,
    matches,
    numbers,
)
from german_seo_bench.checks.language import is_german
from german_seo_bench.checks.text import (
    STYLE_RULES,
    fold,
    format_ok,
    head_hit,
    normalize,
    overlength_penalty,
    phrase_hit,
    phrase_position,
    style_violations,
    tidy,
    word_hit,
)

__all__ = [
    "FIELDS",
    "NULLABLE",
    "STYLE_RULES",
    "AuditVerdict",
    "FactsVerdict",
    "as_findings",
    "as_number",
    "behaviour",
    "boilerplate_hit",
    "extract_json_object",
    "fold",
    "format_ok",
    "grade_audit",
    "grade_facts",
    "head_hit",
    "is_german",
    "matches",
    "normalize",
    "numbers",
    "overlength_penalty",
    "pages_seen",
    "parse_findings",
    "phrase_hit",
    "phrase_position",
    "style_violations",
    "tidy",
    "tool_base_name",
    "tool_usage",
    "word_hit",
]
