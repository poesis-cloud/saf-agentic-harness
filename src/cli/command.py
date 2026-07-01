"""Command — one CLI command's metadata (name + help text + argument configurator)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Command:
    name: str
    summary: str            # one-liner in the command list
    description: str        # shown by `<command> --help`
    configure: Callable[[argparse.ArgumentParser], None]
