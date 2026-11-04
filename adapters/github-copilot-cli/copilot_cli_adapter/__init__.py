"""The `github-copilot-cli` host adapter package (GitHub Copilot app and Copilot CLI).

The package carries a unique name so it can share one Python process with the
`vscode-github-copilot-chat` adapter's bare modules (the test suite imports both).
Everything host-aware for the Copilot CLI agent runtime lives here; the only dependency
into the harness core is its command API (adapter spec I15).
"""
