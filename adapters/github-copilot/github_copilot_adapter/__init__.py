"""The `github-copilot` host adapter package: the GitHub Copilot agent runtime shared by the
Copilot CLI, the Copilot app and VS Code (Session Target: Copilot).

Everything host-aware lives here; the gating logic is the shared adapter base
(`adapters/common/saf_host_adapter`), and the only dependency into the harness core is its
command API (adapter spec, Adapter architecture).
"""

import sys
from pathlib import Path

# The shared adapter base ships beside every host adapter: `adapters/common/`.
_COMMON_DIR = str(Path(__file__).resolve().parents[2] / "common")
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)
