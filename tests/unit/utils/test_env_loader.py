"""Unit tests for .env loading mechanics."""

from __future__ import annotations

import pytest

from utils import EnvLoader


class TestEnvLoader:
    """Verify domain-free KEY=VALUE parsing."""

    def test_loads_key_value_pairs_ignoring_comments_and_blank_lines(self, tmp_path) -> None:
        """Spec: EnvLoader parses .env KEY=VALUE, comments, and blank lines."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n# comment\nFRAMEWORK_DIR=framework\nNAME = poesis saf\nTOKEN=left=right\n",
            encoding="utf-8",
        )

        values = EnvLoader().load_environment(env_file)

        assert dict(values) == {
            "FRAMEWORK_DIR": "framework",
            "NAME": "poesis saf",
            "TOKEN": "left=right",
        }
        with pytest.raises(TypeError):
            values["NEW"] = "value"  # type: ignore[index]

    def test_rejects_malformed_lines_loudly(self, tmp_path) -> None:
        """Spec: utilities fail fast rather than silently accepting bad input."""
        env_file = tmp_path / ".env"
        env_file.write_text("VALID=value\nBROKEN\n", encoding="utf-8")

        with pytest.raises(ValueError, match="line 2"):
            EnvLoader().load_environment(env_file)
