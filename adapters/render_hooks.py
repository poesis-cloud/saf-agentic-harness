#!/usr/bin/env python3
# Generic hook-registration renderer — shared by every environment adapter, exactly like
# dispatch.sh; nothing environment-specific lives in this file.
#
# An adapter's hooks.yaml is the SOURCE OF TRUTH for its firing surface, but it cannot be
# installed verbatim: the host executes a hook's `command` as a shell command line and
# resolves its `cwd` against $HOME by default, so both must be absolute by the time the host
# reads them. This script is that rendering stage.
#
# Two placeholders are substituted:
#   {{ADAPTERS_DIR}}   this directory, derived from THIS FILE'S own location — `adapters/`
#                      ships in the harness repo, never in the framework, so anchoring the
#                      dispatch path on any environment variable would name a file that does
#                      not exist;
#   {{FRAMEWORK_DIR}}  the absolute framework root, supplied explicitly and required to exist.
#
# The rendered file is machine-specific: it is generated at install time, never committed.
# Rendering is all-or-nothing — the output is validated in memory and the file is written only
# if every placeholder resolved and every entry is complete. A half-rendered registration is
# the failure this stage exists to prevent.

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

import yaml

RENDERED_FILENAME = "safe-harness.json"
_COMMAND_FIELD = "command"
_REQUIRED_FIELDS = ("type", "command", "cwd")
_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")


class RenderError(Exception):
    """A rendering refusal — reported to the operator, never written to disk."""


def _substitute(node, shell_values: dict, plain_values: dict, in_command: bool):
    if isinstance(node, dict):
        return {
            key: _substitute(value, shell_values, plain_values, key == _COMMAND_FIELD)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_substitute(item, shell_values, plain_values, in_command) for item in node]
    if isinstance(node, str):
        rendered = node
        # A `command` is a shell command line; every other field is a literal path.
        for placeholder, value in (shell_values if in_command else plain_values).items():
            rendered = rendered.replace(placeholder, value)
        return rendered
    return node


def _validate(hook_map: dict) -> None:
    hooks = hook_map.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise RenderError("the hook map has no 'hooks' object")

    leftover = sorted(set(_PLACEHOLDER.findall(json.dumps(hook_map))))
    if leftover:
        raise RenderError(
            "unsubstituted placeholder(s) survived rendering: "
            + ", ".join(leftover)
            + " — this renderer resolves only {{ADAPTERS_DIR}} and {{FRAMEWORK_DIR}}"
        )

    for event, entries in hooks.items():
        if not isinstance(entries, list) or not entries:
            raise RenderError(f"hook '{event}' registers no command entry")
        for index, entry in enumerate(entries):
            where = f"hook entry {event}[{index}]"
            if not isinstance(entry, dict):
                raise RenderError(f"{where} is not an object")

            missing = [field for field in _REQUIRED_FIELDS if field not in entry]
            if missing:
                raise RenderError(f"{where} is missing required field(s): {', '.join(missing)}")

            cwd = Path(entry["cwd"])
            if not cwd.is_absolute():
                raise RenderError(f"{where} has a relative cwd: {entry['cwd']}")
            if not cwd.is_dir():
                raise RenderError(f"{where} has a cwd that is not a directory: {entry['cwd']}")

            argv = shlex.split(entry[_COMMAND_FIELD])
            if not argv:
                raise RenderError(f"{where} has an empty command")

            program = Path(argv[0])
            if not program.is_absolute():
                raise RenderError(f"{where} has a relative command path: {argv[0]}")
            if not program.is_file():
                raise RenderError(f"{where} names a command that does not exist: {argv[0]}")


def render(hooks_source: Path, adapters_dir: Path, framework_root: Path) -> dict:
    if not hooks_source.is_file():
        raise RenderError(f"no hook map at {hooks_source}")

    dispatch = adapters_dir / "dispatch.sh"
    if not dispatch.is_file():
        raise RenderError(f"no dispatch script at {dispatch}")

    source = yaml.safe_load(hooks_source.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise RenderError(f"the hook map at {hooks_source} is not an object")

    rendered = _substitute(
        source,
        shell_values={
            "{{ADAPTERS_DIR}}": shlex.quote(str(adapters_dir)),
            "{{FRAMEWORK_DIR}}": shlex.quote(str(framework_root)),
        },
        plain_values={
            "{{ADAPTERS_DIR}}": str(adapters_dir),
            "{{FRAMEWORK_DIR}}": str(framework_root),
        },
        in_command=False,
    )
    _validate(rendered)
    return rendered


def _resolve_framework_root(raw: str | None) -> Path:
    if not raw:
        raise RenderError(
            "no framework root supplied — pass --framework-dir <path>; it is the cwd every "
            "rendered hook pins and the anchor the harness reads from the environment"
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise RenderError(f"the framework root does not exist: {root}")
    return root.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render an adapter's hooks.yaml into an installable host hook file."
    )
    parser.add_argument("--env", required=True, help="adapter environment id under adapters/")
    parser.add_argument("--framework-dir", help="absolute framework root; becomes every hook cwd")
    parser.add_argument(
        "--dest",
        help="directory to install into; defaults to <framework-dir>/.github/hooks",
    )
    parser.add_argument("--hooks", help="hook map to render; defaults to adapters/<env>/hooks.yaml")
    args = parser.parse_args(argv)

    adapters_dir = Path(__file__).resolve().parent
    try:
        framework_root = _resolve_framework_root(args.framework_dir)
        hooks_source = (
            Path(args.hooks) if args.hooks else adapters_dir / args.env / "hooks.yaml"
        )
        hook_map = render(hooks_source, adapters_dir, framework_root)
    except RenderError as error:
        print(f"render_hooks: {error}", file=sys.stderr)
        return 2

    dest = Path(args.dest) if args.dest else framework_root / ".github" / "hooks"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / RENDERED_FILENAME
    target.write_text(json.dumps(hook_map, indent=2) + "\n", encoding="utf-8")

    print(f"installed: {target}")
    print(f"  rendered from: {hooks_source}")
    print(f"  framework root (hook cwd): {framework_root}")
    print(f"  dispatch: {adapters_dir / 'dispatch.sh'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
