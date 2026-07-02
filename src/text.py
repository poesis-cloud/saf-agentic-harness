"""Pure parsing kernel — the bottom layer (no intra-package dependencies).

String/YAML/frontmatter/markdown transforms and generic value coercion shared by the
models, mappers, and services layers. Everything here is a pure function over text or
values; filesystem and path concerns live in `mappers.workspace`, domain behaviour on
the model entities, and orchestration in the services. This module imports nothing from the
package, so it can never take part in an import cycle.
"""

from __future__ import annotations

import re
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    yaml = None


# --- generic value coercion -------------------------------------------------
def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# --- scalar / list literal parsing ------------------------------------------
def strip_comment(value: str) -> str:
    if " #" in value:
        return value.split(" #", 1)[0].strip()
    return value.strip()


def parse_scalar(value: str) -> Any:
    value = strip_comment(value)
    if value in {"", "null", "~"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_list_literal(value: str) -> list[str]:
    value = strip_comment(value).strip()
    if value in {"", "[]", "null", "~"}:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"\'') for item in inner.split(",") if item.strip()]
    return [value.strip().strip('"\'')]


# --- frontmatter extraction + parsing ---------------------------------------
def frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    return ""


def parse_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    if yaml is not None:
        try:
            data = yaml.safe_load(frontmatter_text) if frontmatter_text.strip() else {}
        except Exception:
            data = None
        if isinstance(data, dict):
            return json_safe(data)
        if data is None:
            return {}

    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in frontmatter_text.splitlines():
        if not raw_line.strip():
            continue
        top_level = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw_line)
        if top_level:
            current_key = top_level.group(1)
            value = top_level.group(2)
            result[current_key] = [] if value == "" else parse_scalar(value)
            continue
        if current_key and raw_line.startswith(" ") and raw_line.strip().startswith("- "):
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            result[current_key].append(parse_scalar(raw_line.strip()[2:].strip()))
    return result


def parse_contract(block: str) -> dict[str, Any]:
    # Prefer real YAML so nested blocks parse correctly.
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except Exception:
            data = None
        if isinstance(data, dict):
            return json_safe(data)
    contract: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and current_key:
            contract.setdefault(current_key, [])
            if isinstance(contract[current_key], list):
                contract[current_key].append(parse_scalar(stripped[2:].strip()))
            continue
        key_value = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", stripped)
        if key_value:
            current_key = key_value.group(1)
            value = key_value.group(2)
            contract[current_key] = [] if value == "" else parse_scalar(value)
    return contract


# --- markdown structure -----------------------------------------------------
def markdown_body(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def section_map(text: str) -> dict[str, str]:
    pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[heading] = text[body_start:body_end].strip("\n")
    return sections


def section_tree(text: str) -> dict[str, Any]:
    """Parse markdown headings (## and deeper) into a nested tree by heading depth."""
    pattern = re.compile(r"^(#{2,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(1, root)]
    for index, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        node: dict[str, Any] = {
            "__body": text[body_start:body_end].strip("\n"),
            "__level": level,
        }
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        stack[-1][1][heading] = node
        stack.append((level, node))
    return root


# --- template formatting ----------------------------------------------------
def format_template(value: str, **variables: str) -> str:
    result = value
    for key, replacement in variables.items():
        result = result.replace("{" + key + "}", replacement)
    return result
