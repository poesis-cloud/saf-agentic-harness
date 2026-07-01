# GitHub Copilot adapter

Canonical harness and hook documentation now lives in `../../../def/harness/README.md`. The
adapters-folder overview (shared dispatch script, per-host layout, how to add a new host) lives in
`../README.md`.

This adapter contains only the GitHub Copilot specific binding:

- `hooks/map.yaml` — the host hook registration, rendered to `.copilot/hooks.json`; every entry calls
  the shared `../dispatch.sh <event> github-copilot`
- `tools/map.yaml` — tool names, write verbs, and payload keys for this host
