"""Write a framework model catalogue every slug of which an adapter's `models.yaml` binds.

Each hook load refuses a catalogue naming a profile the adapter does not bind (core
function 4: unroutable configurations die at load), so a framework fixture an adapter
bootstraps against carries a catalogue drawn from that adapter's own binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

CATALOGUE_PATH = Path("conf") / "model-profiles.conf.yaml"


def bound_slugs(models_yaml: Path) -> list[str]:
    """Answer every canonical slug an adapter's `models.yaml` binds."""
    document = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))
    return sorted(document["modelBindings"])


def write_catalogue(framework_dir: Path, slugs: Iterable[str]) -> Path:
    """Write `<framework_dir>/conf/model-profiles.conf.yaml` naming exactly `slugs`."""
    path = framework_dir / CATALOGUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = [
        {"slug": slug, "costRank": rank, "capabilities": ["reasoning"]}
        for rank, slug in enumerate(slugs, 1)
    ]
    path.write_text(yaml.safe_dump({"modelProfiles": profiles}), encoding="utf-8")
    return path


def write_bound_catalogue(framework_dir: Path, models_yaml: Path) -> Path:
    """Write a catalogue whose every slug `models_yaml` binds."""
    return write_catalogue(framework_dir, bound_slugs(models_yaml))
