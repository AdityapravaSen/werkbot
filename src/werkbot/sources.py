"""Load and validate data/sources.yaml."""

from pathlib import Path

import yaml

from werkbot.models import Source


def load_sources(path: Path, include_disabled: bool = False) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    sources = [Source.model_validate(item) for item in raw]
    names = [s.name for s in sources]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"Duplicate source names in {path}: {sorted(dupes)}")
    return sources if include_disabled else [s for s in sources if s.enabled]
