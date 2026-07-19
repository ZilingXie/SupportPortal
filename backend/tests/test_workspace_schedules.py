from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.services.workspace_schedules import is_shift_active, time_to_minutes


class WorkspaceScheduleTests(unittest.TestCase):
    def test_regular_shift_uses_start_inclusive_end_exclusive(self) -> None:
        shift = {
            "weekday": 0,
            "start_minute": time_to_minutes("09:00"),
            "end_minute": time_to_minutes("18:00"),
            "timezone": "Asia/Shanghai",
        }

        self.assertTrue(is_shift_active(shift, datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(is_shift_active(shift, datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)))

    def test_overnight_shift_carries_into_next_weekday(self) -> None:
        shift = {
            "weekday": 6,
            "start_minute": time_to_minutes("22:00"),
            "end_minute": time_to_minutes("06:00"),
            "timezone": "Asia/Shanghai",
        }

        self.assertTrue(is_shift_active(shift, datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)))
        self.assertTrue(is_shift_active(shift, datetime(2026, 7, 19, 21, 59, tzinfo=timezone.utc)))
        self.assertFalse(is_shift_active(shift, datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)))

    def test_full_day_shift_accepts_24_hour_end_boundary(self) -> None:
        shift = {
            "weekday": 0,
            "start_minute": time_to_minutes("00:00"),
            "end_minute": time_to_minutes("24:00", allow_24=True),
            "timezone": "Asia/Shanghai",
        }

        self.assertTrue(is_shift_active(shift, datetime(2026, 7, 19, 16, 0, tzinfo=timezone.utc)))
        self.assertTrue(is_shift_active(shift, datetime(2026, 7, 20, 15, 59, tzinfo=timezone.utc)))
        self.assertFalse(is_shift_active(shift, datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)))

    def test_24_hour_time_requires_explicit_end_time_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            time_to_minutes("24:00")

        self.assertEqual(time_to_minutes("24:00", allow_24=True), 1440)


if __name__ == "__main__":
    unittest.main()
