"""The adapter's OWN binding data, loaded with its own tools — never `ConfigLoader`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

_TOOLS_FILENAME = "tools.yaml"
_MODELS_FILENAME = "models.yaml"
_MODEL_BINDINGS_KEY = "modelBindings"
_HOST_MODEL_ID_KEY = "hostModelId"
_ARRAY_MARKER = "[]"
_DELETE_ACTION = "delete"


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
        (`replacements[].filePath`) — one path per replacement.
        """
        found: list[Path] = []
        for raw_path in self._iter_declared_paths(tool_input):
            path = Path(raw_path)
            if path not in found:
                found.append(path)
        return tuple(found)

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


def load_hook_binding(
    adapter_dir: Path, guarded_path_markers: tuple[str, ...] = ()
) -> HookBinding:
    """Load this adapter's binding from its own YAML sources.

    Spec (adapter, I15): loaded with the adapter's own tools — the harness's
    `ConfigLoader` is not a dependency of this component.
    """
    tools = _load_yaml(adapter_dir / _TOOLS_FILENAME)
    models = _load_yaml(adapter_dir / _MODELS_FILENAME)
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


def _load_yaml(path: Path) -> Mapping[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _as_tuple(value: Any) -> tuple[str, ...]:
    return tuple(value) if value else ()


__all__ = ["HookBinding", "load_hook_binding"]
