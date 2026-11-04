"""The adapter's view over its own `tools.yaml` / `models.yaml` binding.

Every payload key and tool name the adapter classifies on is DATA in `tools.yaml`, so a
runtime that renames a field is corrected in configuration, not in code (the runtime's
batched `preToolUse` shape was learned from captured payloads, not from the docs).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# The binding validator is shared by every adapter: it lives in `adapters/`.
_SHARED_ADAPTERS_DIR = str(Path(__file__).resolve().parents[2])
if _SHARED_ADAPTERS_DIR not in sys.path:
    sys.path.append(_SHARED_ADAPTERS_DIR)

from binding_contract import (  # noqa: E402
    MODELS_CONTRACT,
    TOOLS_CONTRACT,
    BindingContractError,
    load_contract_bound_yaml,
    require_catalogue_bound,
    require_non_empty,
)

from .apply_patch import PatchParseError, parse_patch_targets  # noqa: E402

TOOLS_FILENAME = "tools.yaml"
MODELS_FILENAME = "models.yaml"
_DELETE_ACTION = "delete"


class BindingError(BindingContractError):
    """Raised when the binding files are missing, malformed or off-contract."""


class TargetExtractionError(ValueError):
    """Raised when a declared write tool's target paths cannot be extracted (fail closed)."""


@dataclass(frozen=True)
class Subcommand:
    """A write tool whose action depends on one of its own arguments."""

    key: str
    read_only: frozenset[str]
    actions: Mapping[str, str]


@dataclass(frozen=True)
class ToolBinding:
    """The tool classes and payload keys of the Copilot CLI runtime."""

    write_tools: Mapping[str, str]
    delete_tools: frozenset[str]
    patch_tools: frozenset[str]
    patch_keys: tuple[str, ...]
    write_tool_subcommands: Mapping[str, Subcommand]
    path_keys: tuple[str, ...]
    nested_path_keys: tuple[str, ...]
    tool_keys: tuple[str, ...]
    input_keys: tuple[str, ...]
    tool_calls_keys: tuple[str, ...]
    tool_call_name_keys: tuple[str, ...]
    tool_call_args_keys: tuple[str, ...]
    tool_call_id_keys: tuple[str, ...]
    host_session_keys: tuple[str, ...]
    host_parent_session_keys: tuple[str, ...]
    host_step_session_keys: tuple[str, ...]
    host_step_actor_keys: tuple[str, ...]
    host_timestamp_keys: tuple[str, ...]
    host_cwd_keys: tuple[str, ...]
    dispatch_tools: frozenset[str]
    dispatch_agent_keys: tuple[str, ...]
    dispatch_model_keys: tuple[str, ...]
    dispatch_mode_keys: tuple[str, ...]
    async_dispatch_modes: frozenset[str]
    mediated_command_tools: frozenset[str]
    mediated_command_keys: tuple[str, ...]
    guarded_shell_tools: frozenset[str]
    model_bindings: Mapping[str, str] = field(default_factory=dict)
    agent_namespace_separator: str | None = None

    def probe(self, payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
        """Answer the first present, non-empty value among `keys` (first hit wins)."""
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    def agent_name(self, host_agent: str) -> str:
        """Answer the framework agent a host agent identity names (plugin namespace dropped)."""
        separator = self.agent_namespace_separator
        if separator and separator in host_agent:
            return host_agent.rsplit(separator, 1)[1]
        return host_agent

    def resolve_write_action(self, tool_name: str, args: Any) -> str | None:
        """Answer the ACL action a call performs, or None when it is not a write."""
        if tool_name in self.delete_tools:
            return _DELETE_ACTION
        action = self.write_tools.get(tool_name)
        if action is None and tool_name in self.patch_tools:
            action = "update"
        if action is None:
            return None
        subcommand = self.write_tool_subcommands.get(tool_name)
        if subcommand is not None and isinstance(args, Mapping):
            verb = args.get(subcommand.key)
            if isinstance(verb, str):
                if verb in subcommand.read_only:
                    return None
                return subcommand.actions.get(verb, action)
        return action

    def extract_write_targets(
        self, tool_name: str, args: Any
    ) -> tuple[tuple[str, str], ...]:
        """Answer every `(host path, action)` a write call targets.

        Raises `TargetExtractionError` when the tool is a declared write but no path can
        be extracted — the caller DENIES (adapter spec H3, fail closed).
        """
        action = self.resolve_write_action(tool_name, args)
        if action is None:
            return ()
        if tool_name in self.patch_tools:
            patch_text = args if isinstance(args, str) else None
            if patch_text is None and isinstance(args, Mapping):
                found = self.probe(args, self.patch_keys)
                patch_text = found if isinstance(found, str) else None
            try:
                return parse_patch_targets(patch_text or "")
            except PatchParseError as failure:
                raise TargetExtractionError(
                    f"'{tool_name}' patch targets cannot be extracted: {failure}"
                ) from failure
        if not isinstance(args, Mapping):
            raise TargetExtractionError(f"'{tool_name}' carries no argument object")
        paths = [
            str(args[key]) for key in self.path_keys if isinstance(args.get(key), str) and args[key]
        ]
        for nested in self.nested_path_keys:
            paths.extend(_probe_nested(args, nested))
        if not paths:
            raise TargetExtractionError(
                f"'{tool_name}' names no target path under {list(self.path_keys)}"
            )
        return tuple((path, action) for path in dict.fromkeys(paths))

    def is_dispatch_tool(self, tool_name: str) -> bool:
        return tool_name in self.dispatch_tools

    def is_mediated_command_tool(self, tool_name: str) -> bool:
        return tool_name in self.mediated_command_tools

    def is_guarded_shell_tool(self, tool_name: str) -> bool:
        return tool_name in self.guarded_shell_tools


def _probe_nested(args: Mapping[str, Any], spec: str) -> list[str]:
    """Probe an `array[].key` path spec, answering every string it reaches."""
    head, _, tail = spec.partition("[].")
    items = args.get(head)
    if not tail or not isinstance(items, Sequence) or isinstance(items, str):
        return []
    return [
        str(item[tail])
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get(tail), str) and item[tail]
    ]


def _strings(raw: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BindingError(f"tools.yaml '{key}' must be a list of strings")
    return tuple(value)


def _read_yaml(path: Path, contract: str) -> Mapping[str, Any]:
    try:
        loaded = load_contract_bound_yaml(path, contract)
    except BindingError:
        raise
    except BindingContractError as failure:
        raise BindingError(str(failure)) from failure
    if not isinstance(loaded, Mapping):
        raise BindingError(f"binding file is not a mapping: {path}")
    return loaded


# Every probe a gate depends on: empty, it would classify every firing as a pass-through.
_GATING_PROBES = (
    ("writeTools", "no write would ever be authorized (H3) or validated (H5)"),
    ("pathKeys", "no write target could be extracted — every write would be denied"),
    ("toolCallsKeys", "no batched preToolUse call would be gated"),
    ("toolCallNameKeys", "no batched tool call could be named"),
    ("toolCallArgsKeys", "no batched tool call's arguments could be read"),
    ("hostSessionKeys", "no event could be correlated to its session"),
    ("dispatchTools", "no step dispatch would be gated (I4)"),
    ("mediatedCommandTools", "no harness command would be mediated (H4)"),
)


def load_tool_binding(adapter_dir: Path, *, framework_dir: Path | None = None) -> ToolBinding:
    """Load this adapter's binding, contract-validated at load (fail fast, never empty).

    Given the framework root (the hook bootstrap always passes it), the framework model
    catalogue is checked against `models.yaml` on this very load: an unbound profile slug
    is refused (core function 4 — unroutable configurations die at load).
    """
    tools_path = adapter_dir / TOOLS_FILENAME
    raw = _read_yaml(tools_path, TOOLS_CONTRACT)
    for key, why in _GATING_PROBES:
        try:
            require_non_empty(raw, key, source=str(tools_path), why=why)
        except BindingContractError as failure:
            raise BindingError(str(failure)) from failure
    write_tools = raw.get("writeTools") or {}
    if not isinstance(write_tools, Mapping):
        raise BindingError("tools.yaml 'writeTools' must be a mapping")
    subcommands: dict[str, Subcommand] = {}
    for tool, spec in (raw.get("writeToolSubcommands") or {}).items():
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            raise BindingError(f"tools.yaml 'writeToolSubcommands.{tool}' needs a 'key'")
        subcommands[str(tool)] = Subcommand(
            key=spec["key"],
            read_only=frozenset(spec.get("readOnly") or ()),
            actions=dict(spec.get("actions") or {}),
        )
    models_path = adapter_dir / MODELS_FILENAME
    models_document = _read_yaml(models_path, MODELS_CONTRACT)
    if framework_dir is not None:
        try:
            require_catalogue_bound(models_document, framework_dir, source=str(models_path))
        except BindingContractError as failure:
            raise BindingError(str(failure)) from failure
    models = models_document.get("modelBindings") or {}
    return ToolBinding(
        write_tools={str(name): str(action) for name, action in write_tools.items()},
        delete_tools=frozenset(_strings(raw, "deleteTools")),
        patch_tools=frozenset(_strings(raw, "patchTools")),
        patch_keys=_strings(raw, "patchKeys"),
        write_tool_subcommands=subcommands,
        path_keys=_strings(raw, "pathKeys"),
        nested_path_keys=_strings(raw, "nestedPathKeys"),
        tool_keys=_strings(raw, "toolKeys"),
        input_keys=_strings(raw, "inputKeys"),
        tool_calls_keys=_strings(raw, "toolCallsKeys"),
        tool_call_name_keys=_strings(raw, "toolCallNameKeys"),
        tool_call_args_keys=_strings(raw, "toolCallArgsKeys"),
        tool_call_id_keys=_strings(raw, "toolCallIdKeys"),
        host_session_keys=_strings(raw, "hostSessionKeys"),
        host_parent_session_keys=_strings(raw, "hostParentSessionKeys"),
        host_step_session_keys=_strings(raw, "hostStepSessionKeys"),
        host_step_actor_keys=_strings(raw, "hostStepActorKeys"),
        host_timestamp_keys=_strings(raw, "hostTimestampKeys"),
        host_cwd_keys=_strings(raw, "hostCwdKeys"),
        dispatch_tools=frozenset(_strings(raw, "dispatchTools")),
        dispatch_agent_keys=_strings(raw, "dispatchAgentKeys"),
        dispatch_model_keys=_strings(raw, "dispatchModelKeys"),
        dispatch_mode_keys=_strings(raw, "dispatchModeKeys"),
        async_dispatch_modes=frozenset(_strings(raw, "asyncDispatchModes")),
        mediated_command_tools=frozenset(_strings(raw, "mediatedCommandTools")),
        mediated_command_keys=_strings(raw, "mediatedCommandKeys"),
        guarded_shell_tools=frozenset(_strings(raw, "guardedShellTools")),
        agent_namespace_separator=raw.get("hostAgentNamespaceSeparator") or None,
        model_bindings={
            str(slug): str((binding or {}).get("hostModelId"))
            for slug, binding in models.items()
        },
    )


__all__ = [
    "BindingError",
    "Subcommand",
    "TargetExtractionError",
    "ToolBinding",
    "load_tool_binding",
]
