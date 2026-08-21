"""Unit tests for `Clock` — the journal's one ordering-key rendering."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from utils.clock import Clock

_CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class TestClock:
    def test_read_timestamp_renders_a_fixed_width_utc_instant(self) -> None:
        """Spec (Logging, "Ordering — `timestamp` plus single-driver invariant"): "every
        entry's `timestamp` (the log entry's wall-clock write time) is the cross-log
        total ordering key: entries across every session log sort by `timestamp`" — the
        store sorts those strings, so the rendering must be fixed width, UTC, and
        terminated the same way every time."""
        stamped = Clock().read_timestamp()

        assert _CANONICAL.match(stamped), stamped
        read_back = datetime.strptime(stamped, Clock.TIMESTAMP_FORMAT).replace(
            tzinfo=timezone.utc
        )
        assert read_back <= datetime.now(timezone.utc)

    def test_string_sorting_the_rendering_is_sorting_the_instants(self) -> None:
        """Spec (Logging, Ordering): the cross-log order is a sort over the rendered
        `timestamp` strings, so lexicographic order MUST agree with instant order across
        the sub-second, second, and year boundaries alike."""
        instants = (
            datetime(2026, 8, 17, 14, 59, 59, 999999, tzinfo=timezone.utc),
            datetime(2026, 8, 17, 15, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 17, 15, 0, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 17, 15, 0, 0, 500, tzinfo=timezone.utc),
            datetime(2026, 8, 17, 15, 0, 0, 1000, tzinfo=timezone.utc),
            datetime(2027, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc),
        )
        rendered = [instant.strftime(Clock.TIMESTAMP_FORMAT) for instant in instants]

        assert sorted(rendered) == rendered

    def test_one_rendering_repairs_the_inversion_three_renderings_produced(self) -> None:
        """Spec (Logging, Ordering): "entries across every session log sort by
        `timestamp`, giving a single total order for the instance view regardless of
        which session wrote which entry" — the defect: the ordering key was rendered
        three ways (milliseconds+`Z`, microseconds+`Z`, microseconds+`+00:00`), and
        because `+` sorts before every digit and `Z` sorts after them, the store's string
        sort put an EARLIER entry last."""
        earlier = datetime(2026, 8, 17, 15, 0, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 8, 17, 15, 0, 0, 500, tzinfo=timezone.utc)

        milliseconds_and_z = earlier.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        microseconds_and_offset = later.isoformat()
        assert sorted([milliseconds_and_z, microseconds_and_offset]) == [
            microseconds_and_offset,
            milliseconds_and_z,
        ], "the defect: the later instant sorted first"

        canonical = [
            earlier.strftime(Clock.TIMESTAMP_FORMAT),
            later.strftime(Clock.TIMESTAMP_FORMAT),
        ]
        assert sorted(canonical) == canonical
