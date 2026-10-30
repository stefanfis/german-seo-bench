"""Die Referenz von A01 herleiten statt sie zu tippen.

`audit(capture)` rechnet aus einem eingefrorenen Render-Capture die Befunde;
`python -m german_seo_bench.reference` erzeugt das Capture (Docker, Browser) und hält das
Ergebnis gegen die Referenz in `data/a01_browser_audit.json`.
"""

from german_seo_bench.reference.audit import CAPTURE_VERSION, audit

__all__ = ["CAPTURE_VERSION", "audit"]
