"""One artifact lock: the local-only Git ref naming the step ref that holds a path."""

from __future__ import annotations

from dataclasses import dataclass

from stores.artifact_store.step_ref import StepRef

LOCK_REF_NAMESPACE = "refs/harness/locks/"

# Characters a path segment keeps verbatim in a ref name; everything else is
# percent-encoded, `%` included, so the encoding is reversible.
_VERBATIM = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


def _encode_segment(segment: str) -> str:
    """Encode one path segment under Git's ref-name rules.

    `git check-ref-format`: no segment starts with `.` or ends with `.lock`, no
    `..`, no control or special characters, no trailing `.`.
    """
    encoded = "".join(
        char if char in _VERBATIM else "".join(f"%{byte:02X}" for byte in char.encode())
        for char in segment
    )
    if encoded.startswith("."):
        encoded = "%2E" + encoded[1:]
    if encoded.endswith("."):
        encoded = encoded[:-1] + "%2E"
    if encoded.endswith(".lock"):
        encoded = encoded[: -len(".lock")] + "%2Elock"
    return encoded.replace("..", ".%2E")


def _decode_segment(segment: str) -> str:
    """Reverse `_encode_segment`."""
    raw = bytearray()
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "%" and index + 3 <= len(segment):
            raw.append(int(segment[index + 1 : index + 3], 16))
            index += 3
            continue
        raw.extend(char.encode())
        index += 1
    return raw.decode("utf-8", errors="replace")


def build_lock_ref_name(artifact_path: str) -> str:
    """Render the lock ref name of one artifact path, encoded where Git requires."""
    return LOCK_REF_NAMESPACE + "/".join(
        _encode_segment(segment) for segment in artifact_path.split("/")
    )


def parse_lock_ref_name(ref_name: str) -> str | None:
    """Recover the artifact path a lock ref name encodes, or none if foreign."""
    if not ref_name.startswith(LOCK_REF_NAMESPACE):
        return None
    return "/".join(
        _decode_segment(segment)
        for segment in ref_name[len(LOCK_REF_NAMESPACE) :].split("/")
    )


@dataclass(frozen=True)
class ArtifactLock:
    """Name the locked path, its lock ref, and the step ref holding it.

    Spec (Terminology, Artifact lock): the ref's value is a blob whose content is
    the holding step ref's name, so claiming it create-only and re-pointing it
    are ordinary compare-and-swap ref updates; `value` is that blob's id.
    """

    artifact_path: str
    holder: str
    value: str

    @property
    def name(self) -> str:
        """Render the full lock ref name."""
        return build_lock_ref_name(self.artifact_path)

    @property
    def holder_step_ref(self) -> StepRef | None:
        """Parse the holder's step ref name; its tip is not part of the lock."""
        return StepRef.parse_step_ref(self.holder, "")


__all__ = [
    "ArtifactLock",
    "LOCK_REF_NAMESPACE",
    "build_lock_ref_name",
    "parse_lock_ref_name",
]
