"""`python -m german_seo_bench.media` — holt die Bilder und prüft sie."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from german_seo_bench.media import ENV_KEY, sync


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.media",
        description="Holt die Bilder, die das Repo nicht mitliefert, und prüft sie.",
    )
    parser.parse_args()
    # Wie Inspect selbst: Eine `.env` im Arbeitsverzeichnis gilt, die Umgebung
    # geht vor.
    load_dotenv(Path.cwd() / ".env", override=False)
    sys.exit(sync(os.environ.get(ENV_KEY)))


if __name__ == "__main__":
    main()
