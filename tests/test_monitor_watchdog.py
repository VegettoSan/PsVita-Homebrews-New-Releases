#!/usr/bin/env python3
"""Offline regression tests for the schedule-recovery guard."""
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import monitor_watchdog as guard

NOW = datetime(2026, 11, 23, 21, 43, tzinfo=timezone.utc)


def run(hours_ago, status="completed", conclusion="success", *,
        identifier=1, updated_hours_ago=None):
    created = NOW - timedelta(hours=hours_ago)
    completed = NOW - timedelta(hours=(
        updated_hours_ago if updated_hours_ago is not None else hours_ago
    ))
    return {
        "id": identifier,
        "status": status,
        "conclusion": conclusion,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": completed.isoformat().replace("+00:00", "Z"),
    }


class ScheduleWatchdogTests(unittest.TestCase):
    def test_no_scans_requests_recovery(self):
        self.assertTrue(guard.recovery_decision([], NOW)[0])

    def test_recent_success_does_not_repeat_scan(self):
        self.assertFalse(guard.recovery_decision([run(2.95)], NOW)[0])

    def test_overdue_success_requests_recovery(self):
        self.assertTrue(guard.recovery_decision([run(3.1)], NOW)[0])

    def test_exactly_three_hours_is_due(self):
        self.assertTrue(guard.recovery_decision([run(3)], NOW)[0])

    def test_active_scan_is_never_duplicated(self):
        for state in guard.ACTIVE_STATUSES:
            with self.subTest(status=state):
                self.assertFalse(
                    guard.recovery_decision([run(5, status=state, conclusion=None)], NOW)[0]
                )

    def test_recent_failure_waits_before_retry(self):
        self.assertFalse(
            guard.recovery_decision([run(0.5, conclusion="failure")], NOW)[0]
        )
        self.assertTrue(
            guard.recovery_decision([run(1.1, conclusion="failure")], NOW)[0]
        )

    def test_latest_manual_full_scan_counts(self):
        scheduled = run(6, identifier=20)
        manual = run(1, identifier=21)
        self.assertFalse(guard.recovery_decision([scheduled, manual], NOW)[0])

    def test_catalog_refresh_is_not_in_scan_events(self):
        self.assertEqual(guard.SCAN_EVENTS, ("schedule", "workflow_dispatch"))

    def test_completion_time_is_used_instead_of_creation_time(self):
        self.assertFalse(
            guard.recovery_decision([run(5, updated_hours_ago=1)], NOW)[0]
        )

    def test_invalid_timestamp_fails_closed(self):
        broken = run(4)
        broken["created_at"] = "bad timestamp"
        self.assertFalse(guard.recovery_decision([broken], NOW)[0])

    def test_future_timestamp_fails_closed(self):
        self.assertFalse(guard.recovery_decision([run(-1)], NOW)[0])

    def test_queries_server_side_filtered_events(self):
        called = []

        def fake_api(repo, token, path, **kwargs):
            called.append(path)
            return {"workflow_runs": []}

        with patch.object(guard, "github_api", side_effect=fake_api):
            self.assertEqual(guard.recent_full_scans("owner/repo", "token"), [])
        self.assertEqual(len(called), 2)
        self.assertIn("event=schedule", called[0])
        self.assertIn("event=workflow_dispatch", called[1])
        self.assertNotIn("repository_dispatch", "".join(called))

    def test_unexpected_response_does_not_dispatch(self):
        with patch.object(guard, "github_api", return_value={}):
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                guard.recent_full_scans("owner/repo", "token")


if __name__ == "__main__":
    unittest.main()
