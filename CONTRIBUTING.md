# Contributing to SDLC Agentic Framework

Thank you for your interest in contributing! This document covers the process,
licensing, and conventions for contributions to this project.

---

## Developer Certificate of Origin (DCO)

This project uses the [Developer Certificate of Origin (DCO)](https://developercertificate.org/).
By making a contribution you certify that you wrote it or otherwise have the right
to submit it under the open-source license below, and you agree to the DCO terms.

Sign your commits:

```bash
git commit -s -m "your commit message"
```

This appends a `Signed-off-by: Your Name <email>` line to the commit message.

---

## Inbound license

All contributions are accepted under the **Apache License, Version 2.0** (the same
license that covers this project). By submitting a pull request you confirm that
you have the right to license your contribution under Apache-2.0 and you do so.

---

## External prerequisites — not relicensed here

This framework is designed to work alongside the following external components,
which are distributed under their own upstream licenses and are **not** included
in or relicensed by this repository:

| Component | Description | License |
|---|---|---|
| `SE:*` bench agents | Specialist subagent plugins for GitHub Copilot / VS Code | Microsoft / GitHub ToS |
| AI Runway / Azure AI | Azure ML / inference services | Microsoft Azure service agreements |

Do **not** include files derived from these external components in your PRs.
They are installation prerequisites, not framework constituents.

---

## Pull request process

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Ensure all Poesis-authored files carry the Apache-2.0
   license header (see [License header convention](#license-header-convention) below).
3. Run the CI checks locally if possible (see the CI workflow at `.github/workflows/ci.yml`).
4. Open a pull request against `main` with a clear description of the change and
   why it is needed.
5. A maintainer will review your PR. Please respond to review comments promptly.

---

## License header convention

Every Poesis-authored file that is part of the SHIP set (skills, agents, instructions,
prompt files, YAML configs) **must** carry the following Apache-2.0 header as the
first lines of the file:

```text
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
```

The canonical template is at [`license-header-template.txt`](license-header-template.txt) in the repo root.

Use `#` for YAML/Markdown/shell files. For JSON files (which do not support comments),
embed the copyright in a `"$license"` key at the top level.

The CI license-header check will enforce this once SHIP content is present. Files that
do not carry the header will fail CI.

---

## Code of conduct

Be respectful. We follow the standard [Contributor Covenant](https://www.contributor-covenant.org/)
norms — treat everyone with courtesy and professionalism.

---

## Questions?

Open an issue or start a discussion in the repository.
