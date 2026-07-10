#!/usr/bin/env python3
# Copyright 2026 Poesis Cloud and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mock subagentStart hook for two-level subagent input inspection.

Reads the JSON payload from stdin, appends it to a log file, and echoes the
payload back to the spawning subagent as additionalContext so the subagent can
report exactly what the hook received. Only reacts to agents named mock-parent
or mock-child; all other subagents are logged but get an empty response.
"""

import datetime
import json
import os
import sys

LOG_PATH = "/tmp/safe-mock-subagent-hook.log.jsonl"


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        data = {"parse_error": str(exc), "raw": raw}

    # The host may send camelCase (subagentStart) or snake_case (SubagentStart).
    agent = data.get("agentName") or data.get("agent_name") or "unknown"

    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent": agent,
        "input": data,
    }

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if agent in ("mock-parent", "mock-child"):
        payload = json.dumps(data, ensure_ascii=False, indent=None)
        context = f"HOOK_INPUT_START\n{payload}\nHOOK_INPUT_END"
        print(json.dumps({"additionalContext": context}, ensure_ascii=False))
    else:
        # No reaction for unrelated agents.
        print("{}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
