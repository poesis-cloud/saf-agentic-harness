import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ADAPTER_ENV = "vscode-github-copilot-chat"
ORCHESTRATORS = ("value-management-officer", "release-train-engineer", "scrum-master")
BENCH_AGENT = "developer"
BUNDLE_TOOLS = "tools: [read, search]\n"
AGENT_BODY = (
    "\n"
    "<!-- Copyright 2026 Poesis Cloud and contributors -->\n"
    "\n"
    "# {slug}\n"
    "\n"
    "Body text with a trailing space \n"
    "and a second line.\n"
)


def _agent_file(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug}\n"
        f"description: 'The {slug} — one line, quoted, with a — dash.'\n"
        "---\n" + AGENT_BODY.format(slug=slug)
    )


def _render(repo_root: Path, *args: str, env_overrides: dict | None = None):
    """Run the renderer exactly as `make install-hooks` does — as a subprocess, so
    exit status and stderr are the real ones the operator sees."""
    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, str(repo_root / "adapters" / "render_hooks.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )


@pytest.fixture
def framework(tmp_path: Path) -> Path:
    """A hermetic stand-in for a framework checkout: its own workflow catalog and its
    own committed, host-agnostic agent sources. Never the real framework repo."""
    root = tmp_path / "framework"
    workflows = root / "conf" / "workflows"
    workflows.mkdir(parents=True)
    agents = root / "agents"
    agents.mkdir()

    for index, slug in enumerate(ORCHESTRATORS):
        (workflows / f"w{index}.workflow.conf.yaml").write_text(
            f"slug: w{index}\norchestrator: {slug}\n", encoding="utf-8"
        )
    # a second workflow for the same orchestrator — the catalog is a multiset, the
    # registration is per agent
    (workflows / "w-extra.workflow.conf.yaml").write_text(
        f"slug: w-extra\norchestrator: {ORCHESTRATORS[0]}\n", encoding="utf-8"
    )

    for slug in (*ORCHESTRATORS, BENCH_AGENT):
        (agents / f"{slug}.agent.md").write_text(_agent_file(slug), encoding="utf-8")

    return root


@pytest.fixture
def bundle(framework: Path, tmp_path: Path) -> Path:
    """What the framework's own bundle renderer produces: a COPY of the source tree with
    the host's `tools:` frontmatter injected into every manifest agent. This is the tree
    the plugin ships and the only one the host is ever given."""
    root = tmp_path / "bundle"
    shutil.copytree(framework, root)
    for path in sorted((root / "agents").glob("*.agent.md")):
        path.write_text(
            path.read_text(encoding="utf-8").replace("---\n", "---\n" + BUNDLE_TOOLS, 1),
            encoding="utf-8",
        )
    return root


def _install(
    repo_root: Path,
    framework: Path,
    tmp_path: Path,
    bundle: Path | None = None,
    env_overrides=None,
    **overrides,
):
    args = [
        "--env",
        ADAPTER_ENV,
        "--framework-dir",
        str(framework),
        "--dest",
        str(tmp_path / "hooks-dest"),
    ]
    if bundle is not None:
        args += ["--bundle-dir", str(bundle)]
    for flag, value in overrides.items():
        args += [f"--{flag.replace('_', '-')}", str(value)]
    return _render(repo_root, *args, env_overrides=env_overrides)


def _rendered(root: Path, slug: str) -> Path:
    return root / "agents" / f"{slug}.agent.md"


def _snapshot(root: Path) -> dict:
    return {path: path.read_bytes() for path in sorted((root / "agents").glob("*.agent.md"))}


def _frontmatter(text: str) -> dict:
    _, _, rest = text.partition("---\n")
    front, _, _ = rest.partition("\n---\n")
    return yaml.safe_load(front)


class TestAgentHooksSource:
    def test_agent_hooks_yaml_is_a_render_source_not_an_installable_block(self, adapter_dir: Path):
        """Adapter spec H0 (Registration) + (Rendered registration): `agent-hooks.yaml` is the
        source of truth for the session-started boundary — one `UserPromptSubmit` entry
        carrying the deployment placeholders no checkout can resolve, including the
        `{{AGENT_SLUG}}` that makes the registration per orchestrator."""
        source = yaml.safe_load((adapter_dir / "agent-hooks.yaml").read_text(encoding="utf-8"))
        entries = source["agentHooks"]["UserPromptSubmit"]

        assert set(source) == {"agentHooks"}
        assert set(source["agentHooks"]) == {"UserPromptSubmit"}
        assert entries == [
            {
                "type": "command",
                "command": (
                    "{{ADAPTERS_DIR}}/dispatch.sh UserPromptSubmit "
                    f"{ADAPTER_ENV} {{{{AGENT_SLUG}}}}"
                ),
                "cwd": "{{FRAMEWORK_DIR}}",
                "timeout": 30,
            }
        ]
        assert shlex.split(entries[0]["command"])[-1] == "{{AGENT_SLUG}}"


class TestAgentDeliveryPath:
    def test_refuses_to_guess_where_the_delivered_agents_live(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Installation destination): a framework delivers its agents through
        a host bundle, so a framework-anchored default would install a SECOND copy of every
        orchestrator beside the delivered one — one carrying the H0 hook, one carrying the
        bundle's tool restrictions. Which one the host would prefer is not documented, so
        the renderer names the ambiguity instead of betting on it."""
        before = _snapshot(framework)

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert "bundle" in result.stderr.lower()
        assert not (framework / ".github").exists()
        assert _snapshot(framework) == before

    def test_injects_into_the_delivered_bundle_in_place(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Installation destination): the H0 block belongs in the agents the
        host actually receives — the rendered bundle, after its own renderer has run. One
        copy of each orchestrator exists, and it carries the registration."""
        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            assert "UserPromptSubmit" in _rendered(bundle, slug).read_text(encoding="utf-8")
        assert not (framework / ".github").exists()

    def test_the_bundles_own_tool_restrictions_survive_the_injection(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Installation destination): the block is injected ON TOP OF the
        bundle's own frontmatter injection. An agent carrying the hook but not the
        bundle's `tools:` restriction would run unrestricted — the failure a parallel
        delivery path produces silently."""
        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            front = _frontmatter(_rendered(bundle, slug).read_text(encoding="utf-8"))

            assert front["tools"] == ["read", "search"]
            assert "UserPromptSubmit" in front["hooks"]

    def test_the_workspace_agents_layout_stays_reachable_by_override(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Installation destination): `AGENTS_DIR`/`AGENTS_DEST` remain
        explicit, so a framework whose agents reach the host as workspace files — the
        host's other documented agent scope, `.github/agents/` — is still installable."""
        workspace_agents = framework / ".github" / "agents"

        result = _install(
            repo_root,
            framework,
            tmp_path,
            agents_dir=framework / "agents",
            agents_dest=workspace_agents,
        )

        assert result.returncode == 0, result.stderr
        assert sorted(path.name for path in workspace_agents.iterdir()) == sorted(
            f"{slug}.agent.md" for slug in ORCHESTRATORS
        )


class TestAgentScopedHookRender:
    def test_each_orchestrator_is_registered_with_its_own_slug_as_dispatch_argument(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the session-started boundary is an AGENT-scoped
        `UserPromptSubmit` hook in each orchestrator's `.agent.md` frontmatter, the scoping
        agent's slug passed as the trailing dispatch argument — host-fixed, not
        model-authored, since no top-level payload names the active agent."""
        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            entries = _frontmatter(_rendered(bundle, slug).read_text(encoding="utf-8"))[
                "hooks"
            ]["UserPromptSubmit"]

            assert len(entries) == 1
            assert entries[0]["type"] == "command"
            assert entries[0]["timeout"] == 30
            assert shlex.split(entries[0]["command"])[1:] == [
                "UserPromptSubmit",
                ADAPTER_ENV,
                slug,
            ]

    def test_a_non_orchestrator_agent_is_never_registered(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0: C7 holds structurally — the hook fires only while a framework
        ORCHESTRATOR is the active agent, so the bench carries no registration at all and
        no hook can fire for a foreign agent."""
        before = _snapshot(bundle)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        bench = _rendered(bundle, BENCH_AGENT)

        assert "hooks:" not in bench.read_text(encoding="utf-8")
        assert bench.read_bytes() == before[bench]
        assert sorted(
            path.name for path, blob in _snapshot(bundle).items() if blob != before[path]
        ) == sorted(f"{slug}.agent.md" for slug in ORCHESTRATORS)

    def test_the_orchestrator_set_is_derived_from_the_frameworks_own_workflow_catalog(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Preconditions): the scoping slug must resolve to a framework
        orchestrator. The framework's workflow catalog is what defines one, so the
        registration follows the catalog rather than a list the adapter hardcodes."""
        (framework / "conf" / "workflows" / "w-new.workflow.conf.yaml").write_text(
            "slug: w-new\norchestrator: product-manager\n", encoding="utf-8"
        )
        _rendered(bundle, "product-manager").write_text(
            _agent_file("product-manager"), encoding="utf-8"
        )

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr
        assert "UserPromptSubmit" in _rendered(bundle, "product-manager").read_text(
            encoding="utf-8"
        )

    def test_the_committed_agent_source_is_never_modified(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): the rendered registration is
        machine-specific — it pins this checkout's absolute dispatch path — so it lands in
        a generated artifact. The framework's committed agent sources stay byte-for-byte
        host-agnostic and machine-independent."""
        before = _snapshot(framework)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr
        assert _snapshot(framework) == before

    def test_existing_frontmatter_and_body_survive_the_injection_unchanged(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the block is ADDED to the orchestrator's own
        frontmatter — `name`, `description` and the agent's instructions are the agent's
        identity and must reach the host exactly as authored."""
        source = {
            slug: _rendered(bundle, slug).read_text(encoding="utf-8") for slug in ORCHESTRATORS
        }

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            rendered = _rendered(bundle, slug).read_text(encoding="utf-8")

            assert rendered.endswith(AGENT_BODY.format(slug=slug))
            assert f"name: {slug}\n" in rendered
            assert (
                _frontmatter(rendered)["description"]
                == _frontmatter(source[slug])["description"]
            )
            assert _frontmatter(rendered)["name"] == _frontmatter(source[slug])["name"]

    def test_no_placeholder_survives_in_a_rendered_agent_file(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): every `{{…}}` is substituted at render
        time — the host executes the registered command verbatim, so a surviving
        placeholder is a hook that cannot launch."""
        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            rendered = _rendered(bundle, slug).read_text(encoding="utf-8")

            assert "{{" not in rendered
            assert "}}" not in rendered

    def test_the_command_is_absolutized_from_the_renderers_own_location(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees, decision C): the `command` is absolutized
        from the RENDERER'S own location, never from `FRAMEWORK_DIR` — `adapters/` ships
        in the harness repo, so a framework-anchored command names nothing."""
        result = _install(
            repo_root,
            framework,
            tmp_path,
            bundle=bundle,
            env_overrides={"FRAMEWORK_DIR": "/nonexistent/wrong/anchor"},
        )

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            entry = _frontmatter(_rendered(bundle, slug).read_text(encoding="utf-8"))[
                "hooks"
            ]["UserPromptSubmit"][0]
            dispatch = Path(shlex.split(entry["command"])[0])

            assert dispatch == repo_root / "adapters" / "dispatch.sh"
            assert dispatch.is_file()
            assert framework not in dispatch.parents
            assert Path(entry["cwd"]) == framework.resolve()

    def test_rendering_twice_is_idempotent(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): installation is re-run whenever either
        checkout moves, and the destination is the delivered bundle itself — so the block
        is written back over an agent that already carries one. A second `hooks:` key is a
        frontmatter the host's own validation rejects, so it is replaced, never
        appended."""
        assert _install(repo_root, framework, tmp_path, bundle=bundle).returncode == 0

        first = {slug: _rendered(bundle, slug).read_bytes() for slug in ORCHESTRATORS}

        assert _install(repo_root, framework, tmp_path, bundle=bundle).returncode == 0

        for slug in ORCHESTRATORS:
            rendered = _rendered(bundle, slug)

            assert rendered.read_bytes() == first[slug]
            assert rendered.read_text(encoding="utf-8").count("UserPromptSubmit:") == 1
            assert rendered.read_text(encoding="utf-8").count("hooks:") == 1

    def test_a_stale_managed_block_is_replaced_not_appended(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): the installed registration is invalidated
        whenever either checkout moves — re-rendering over a block that names a stale
        dispatch path must REPLACE it, or the host keeps firing a command that is gone."""
        assert _install(repo_root, framework, tmp_path, bundle=bundle).returncode == 0

        stale = _rendered(bundle, ORCHESTRATORS[0])
        stale.write_text(
            stale.read_text(encoding="utf-8").replace(
                str(repo_root / "adapters"), "/moved/elsewhere/adapters"
            ),
            encoding="utf-8",
        )

        assert _install(repo_root, framework, tmp_path, bundle=bundle).returncode == 0

        rendered = stale.read_text(encoding="utf-8")

        assert "/moved/elsewhere/adapters" not in rendered
        assert rendered.count("UserPromptSubmit:") == 1
        assert rendered.endswith(AGENT_BODY.format(slug=ORCHESTRATORS[0]))

    def test_an_agent_without_frontmatter_fails_loudly_and_writes_nothing(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): rendering is all-or-nothing. An agent file
        with no frontmatter has nowhere to carry the block — the renderer refuses rather
        than inventing one and corrupting the agent."""
        _rendered(bundle, ORCHESTRATORS[0]).write_text(
            "# no frontmatter here\n", encoding="utf-8"
        )
        before = _snapshot(bundle)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert "frontmatter" in result.stderr.lower()
        assert _snapshot(bundle) == before

    def test_an_agent_with_unclosed_frontmatter_fails_loudly_and_writes_nothing(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): the output is validated in memory and
        written only if it passes — an unterminated frontmatter block cannot be parsed,
        so nothing is emitted for any agent."""
        _rendered(bundle, ORCHESTRATORS[1]).write_text(
            "---\nname: x\ndescription: 'y'\n\n# body\n", encoding="utf-8"
        )
        before = _snapshot(bundle)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert "frontmatter" in result.stderr.lower()
        assert _snapshot(bundle) == before

    def test_an_agent_declaring_its_own_hooks_block_is_refused(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the block is the adapter's to own. An agent that
        already declares an unmanaged `hooks:` key cannot receive a second one — YAML would
        carry a duplicate key and the host's own frontmatter validation would reject it."""
        agent = _rendered(bundle, ORCHESTRATORS[2])
        agent.write_text(
            agent.read_text(encoding="utf-8").replace(
                "---\n" + AGENT_BODY.format(slug=ORCHESTRATORS[2]),
                "hooks:\n  Stop:\n    - type: command\n      command: './x.sh'\n"
                "---\n" + AGENT_BODY.format(slug=ORCHESTRATORS[2]),
            ),
            encoding="utf-8",
        )
        before = _snapshot(bundle)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert "hooks" in result.stderr.lower()
        assert _snapshot(bundle) == before

    def test_an_orchestrator_with_no_agent_file_aborts_before_writing_any_agent(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Preconditions): a scoping slug that resolves to no agent is a
        `configuration-error`. Rendering is all-or-nothing — a partial install would leave
        some orchestrators registered and others silently inert, the exact H0 gap."""
        _rendered(bundle, ORCHESTRATORS[0]).unlink()
        before = _snapshot(bundle)

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert ORCHESTRATORS[0] in result.stderr
        assert _snapshot(bundle) == before

    def test_a_framework_with_no_orchestrator_is_refused(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0: without a registration the session-started boundary never
        fires and no session is ever opened. A framework whose catalog names no
        orchestrator is a misconfiguration to report, not an empty install to accept."""
        for path in (framework / "conf" / "workflows").glob("*.yaml"):
            path.unlink()

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert "orchestrator" in result.stderr.lower()

    def test_a_corrupt_managed_block_fails_loudly_rather_than_being_patched(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): a half-rendered registration is the failure
        this stage exists to prevent — a managed block whose delimiters were damaged is
        reported, never silently rewritten around."""
        assert _install(repo_root, framework, tmp_path, bundle=bundle).returncode == 0

        target = _rendered(bundle, ORCHESTRATORS[0])
        damaged = "".join(
            line
            for line in target.read_text(encoding="utf-8").splitlines(keepends=True)
            if "<<<" not in line
        )
        target.write_text(damaged, encoding="utf-8")

        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode != 0
        assert "block" in result.stderr.lower()
        assert target.read_text(encoding="utf-8") == damaged

    def test_the_rendered_agent_files_are_ignored_by_git(self, repo_root: Path):
        """Adapter spec (Rendering guarantees): the rendered registration pins this
        checkout's absolute dispatch path and one machine's framework root — it is
        generated at install time and cannot be committed, wherever it lands."""
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", ".github/agents/scrum-master.agent.md"],
            cwd=str(repo_root),
            capture_output=True,
        )

        assert ignored.returncode == 0

    def test_the_registered_command_is_emitted_on_one_line(
        self, repo_root: Path, framework: Path, bundle: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration) + host facts: the host executes `command` as a
        shell command line. Emitting it as a YAML-folded multi-line scalar leaves the
        registration correct only if the host's frontmatter parser folds it back — an
        avoidable bet on a preview feature, taken on every command over 80 columns."""
        result = _install(repo_root, framework, tmp_path, bundle=bundle)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            command_lines = [
                line
                for line in _rendered(bundle, slug).read_text(encoding="utf-8").splitlines()
                if "dispatch.sh" in line
            ]

            assert len(command_lines) == 1
            assert command_lines[0].lstrip().startswith("command:")
            assert slug in command_lines[0]
