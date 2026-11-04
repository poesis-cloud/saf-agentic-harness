"""The adapter's OWN binding data, loaded with its own tools — never `ConfigLoader`."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

# The binding validator is shared by every adapter: it lives one level up, in `adapters/`.
_SHARED_ADAPTERS_DIR = str(Path(__file__).resolve().parent.parent)
if _SHARED_ADAPTERS_DIR not in sys.path:
    sys.path.append(_SHARED_ADAPTERS_DIR)

from binding_contract import (  # noqa: E402
    MODELS_CONTRACT,
    TOOLS_CONTRACT,
    BindingContractError,
    load_contract_bound_yaml,
    require_non_empty,
)

_TOOLS_FILENAME = "tools.yaml"
_MODELS_FILENAME = "models.yaml"
_MODEL_BINDINGS_KEY = "modelBindings"
_HOST_MODEL_ID_KEY = "hostModelId"
_ARRAY_MARKER = "[]"
_DELETE_ACTION = "delete"
_CREATE_ACTION = "create"
_UPDATE_ACTION = "update"
# Patch-document file headers (the `*** Begin Patch` format `apply_patch` carries) and the
# action each performs; `*** Move to:` re-targets the preceding `*** Update File:`.
_PATCH_ADD_HEADER = "*** Add File:"
_PATCH_UPDATE_HEADER = "*** Update File:"
_PATCH_DELETE_HEADER = "*** Delete File:"
_PATCH_MOVE_HEADER = "*** Move to:"


@dataclass(frozen=True)
class HookBinding:
    """Answer what this host's tools mean and where its payload fields live.

    Spec (adapter, I15): the binding is this adapter's own declarative data — its own
    `tools.yaml` / `models.yaml`, loaded with its own tools. It holds no dependency on
    the harness's `config`, `services`, or `stores`.
    """

    write_tools: Mapping[str, str]
    delete_tools: tuple[str, ...]
    dispatch_tools: tuple[str, ...]
    mediated_command_tools: tuple[str, ...]
    guarded_shell_tools: tuple[str, ...]
    tool_keys: tuple[str, ...]
    input_keys: tuple[str, ...]
    path_keys: tuple[str, ...]
    nested_path_keys: tuple[str, ...]
    host_session_keys: tuple[str, ...]
    host_step_session_keys: tuple[str, ...]
    host_step_actor_keys: tuple[str, ...]
    dispatch_agent_keys: tuple[str, ...]
    dispatch_model_keys: tuple[str, ...]
    mediated_command_keys: tuple[str, ...]
    model_ids: Mapping[str, str]
    guarded_path_markers: tuple[str, ...] = ()
    patch_keys: tuple[str, ...] = ()

    def is_dispatch_tool(self, tool_name: str | None) -> bool:
        """Tell whether this tool opens a step session (H2/H6)."""
        return tool_name in self.dispatch_tools

    def is_write_class_tool(self, tool_name: str | None) -> bool:
        """Tell whether this tool writes into the workspace (H3/H5)."""
        return tool_name in self.write_tools or tool_name in self.delete_tools

    def is_mediated_command_tool(self, tool_name: str | None) -> bool:
        """Tell whether this tool can carry a harness command invocation (H4)."""
        return tool_name in self.mediated_command_tools

    def is_guarded_shell_tool(self, tool_name: str | None) -> bool:
        """Tell whether this tool falls under the advisory shell guard (I9)."""
        return tool_name in self.guarded_shell_tools

    def resolve_write_action(self, tool_name: str) -> str | None:
        """Resolve the action verb this write tool performs.

        Spec (adapter, H3): `action` maps from `tool_name` via the `writeTools` verb map;
        `deleteTools` map to `delete`.
        """
        action = self.write_tools.get(tool_name)
        if action is not None:
            return action
        return _DELETE_ACTION if tool_name in self.delete_tools else None

    def extract_artifact_paths(self, tool_input: Mapping[str, Any]) -> tuple[Path, ...]:
        """Extract every distinct artifact path a tool call names, in call order.

        Spec (adapter, H3 / I8): flat probes per `pathKeys`, plus the dotted
        `nestedPathKeys` expressions with `[]` array fan-out
        (`replacements[].filePath`) — one path per replacement — plus every file header
        of a patch document found under `patchKeys`.
        """
        return tuple(path for path, _ in self._iter_path_actions(tool_input, None))

    def resolve_path_actions(
        self, tool_name: str, tool_input: Mapping[str, Any]
    ) -> tuple[tuple[Path, str], ...]:
        """Pair every distinct artifact path of a call with the action it performs.

        Spec (adapter, H3): a path reached by a declared key takes its tool's action; a
        patch header carries its own (`Add` -> create, `Update` -> update, `Delete` ->
        delete; a move deletes its source and creates its destination).
        """
        return self._iter_path_actions(tool_input, self.resolve_write_action(tool_name))

    def _iter_path_actions(
        self, tool_input: Mapping[str, Any], tool_action: str | None
    ) -> tuple[tuple[Path, str], ...]:
        found: dict[Path, str] = {}
        operations = [(raw, tool_action) for raw in self._iter_declared_paths(tool_input)]
        for key in self.patch_keys:
            patch = tool_input.get(key)
            if isinstance(patch, str):
                operations.extend(_parse_patch_operations(patch))
        for raw_path, action in operations:
            path = Path(raw_path)
            if path not in found or action == _DELETE_ACTION:
                found[path] = action or ""
        return tuple(found.items())

    def resolve_model_id(self, canonical_slug: str) -> str | None:
        """Resolve the exact host model string this host expects for `runSubagent`."""
        return self.model_ids.get(canonical_slug)

    def probe_payload_value(
        self, payload: Mapping[str, Any], keys: Sequence[str]
    ) -> Any | None:
        """Probe a payload for the first of these declared field names that hits."""
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    def _iter_declared_paths(self, tool_input: Mapping[str, Any]) -> Iterator[str]:
        for key in self.path_keys:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                yield value
        for expression in self.nested_path_keys:
            yield from _iter_nested_values(tool_input, expression.split("."))


def _iter_nested_values(value: Any, segments: Sequence[str]) -> Iterator[str]:
    """Walk one dotted path expression, fanning out on `[]` array segments."""
    if not segments:
        if isinstance(value, str) and value:
            yield value
        return
    if not isinstance(value, Mapping):
        return
    segment = segments[0]
    if segment.endswith(_ARRAY_MARKER):
        items = value.get(segment[: -len(_ARRAY_MARKER)])
        if isinstance(items, Iterable) and not isinstance(items, (str, bytes, Mapping)):
            for item in items:
                yield from _iter_nested_values(item, segments[1:])
        return
    yield from _iter_nested_values(value.get(segment), segments[1:])


def _parse_patch_operations(patch: str) -> list[tuple[str, str]]:
    """Read the (path, action) pairs a patch document's file headers declare."""
    operations: list[tuple[str, str]] = []
    headers = (
        (_PATCH_ADD_HEADER, _CREATE_ACTION),
        (_PATCH_UPDATE_HEADER, _UPDATE_ACTION),
        (_PATCH_DELETE_HEADER, _DELETE_ACTION),
    )
    for line in patch.splitlines():
        if line.startswith(_PATCH_MOVE_HEADER):
            destination = line[len(_PATCH_MOVE_HEADER):].strip()
            if destination and operations and operations[-1][1] == _UPDATE_ACTION:
                operations[-1] = (operations[-1][0], _DELETE_ACTION)
            if destination:
                operations.append((destination, _CREATE_ACTION))
            continue
        for header, action in headers:
            if line.startswith(header):
                path = line[len(header):].strip()
                if path:
                    operations.append((path, action))
                break
    return operations


def load_hook_binding(
    adapter_dir: Path, guarded_path_markers: tuple[str, ...] = ()
) -> HookBinding:
    """Load this adapter's binding from its own YAML sources.

    Spec (adapter, I15): loaded with the adapter's own tools — the harness's
    `ConfigLoader` is not a dependency of this component. Both files are validated
    against their adapter contracts at load (fail-fast): a malformed binding raises
    `BindingContractError` rather than degrading into empty probes.
    """
    tools_path = adapter_dir / _TOOLS_FILENAME
    tools = load_contract_bound_yaml(tools_path, TOOLS_CONTRACT)
    _require_gating_probes(tools, source=str(tools_path))
    models = load_contract_bound_yaml(adapter_dir / _MODELS_FILENAME, MODELS_CONTRACT)
    model_bindings = models.get(_MODEL_BINDINGS_KEY) or {}
    return HookBinding(
        write_tools=dict(tools.get("writeTools") or {}),
        delete_tools=_as_tuple(tools.get("deleteTools")),
        dispatch_tools=_as_tuple(tools.get("dispatchTools")),
        mediated_command_tools=_as_tuple(tools.get("mediatedCommandTools")),
        guarded_shell_tools=_as_tuple(tools.get("guardedShellTools")),
        tool_keys=_as_tuple(tools.get("toolKeys")),
        input_keys=_as_tuple(tools.get("inputKeys")),
        path_keys=_as_tuple(tools.get("pathKeys")),
        nested_path_keys=_as_tuple(tools.get("nestedPathKeys")),
        patch_keys=_as_tuple(tools.get("patchKeys")),
        host_session_keys=_as_tuple(tools.get("hostSessionKeys")),
        host_step_session_keys=_as_tuple(tools.get("hostStepSessionKeys")),
        host_step_actor_keys=_as_tuple(tools.get("hostStepActorKeys")),
        dispatch_agent_keys=_as_tuple(tools.get("dispatchAgentKeys")),
        dispatch_model_keys=_as_tuple(tools.get("dispatchModelKeys")),
        mediated_command_keys=_as_tuple(tools.get("mediatedCommandKeys")),
        model_ids={
            slug: binding[_HOST_MODEL_ID_KEY]
            for slug, binding in model_bindings.items()
            if isinstance(binding, Mapping) and _HOST_MODEL_ID_KEY in binding
        },
        guarded_path_markers=tuple(guarded_path_markers),
    )


def _require_gating_probes(tools: Mapping[str, Any], *, source: str) -> None:
    """Refuse a binding whose declared gates could never fire (fail closed, not open)."""
    for key in ("toolKeys", "inputKeys", "hostSessionKeys"):
        require_non_empty(
            tools, key, source=source, why="no firing could be classified or attributed"
        )
    if (tools.get("writeTools") or tools.get("deleteTools")) and not (
        tools.get("pathKeys") or tools.get("nestedPathKeys") or tools.get("patchKeys")
    ):
        raise BindingContractError(
            f"adapter binding '{source}' declares no 'pathKeys', 'nestedPathKeys' or "
            "'patchKeys': "
            "declared write tools would name no artifact path"
        )
    if tools.get("mediatedCommandTools") or tools.get("guardedShellTools"):
        require_non_empty(
            tools,
            "mediatedCommandKeys",
            source=source,
            why="declared command tools would carry no command string",
        )


def _as_tuple(value: Any) -> tuple[str, ...]:
    return tuple(value) if value else ()


__all__ = ["BindingContractError", "HookBinding", "load_hook_binding"]
