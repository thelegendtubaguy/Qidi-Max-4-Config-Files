from __future__ import annotations

import http.client
import io
import unittest

from installer.runtime import messages
from installer.runtime.interaction import (
    confirm_yes,
    machine_service_restart_url,
    maybe_restart_klipper,
    moonraker_restart_url,
)


class _DummyReporter:
    def __init__(self):
        self.lines: list[str] = []
        self.prompt_prepared = 0

    def line(self, message: str = "") -> None:
        self.lines.append(message)

    def prepare_for_prompt(self) -> None:
        self.prompt_prepared += 1


class _DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b"{}"


class _IncompleteResponse(_DummyResponse):
    def read(self):
        raise http.client.IncompleteRead(b"")


class InteractionTests(unittest.TestCase):
    def test_confirm_yes_accepts_yes_responses_case_insensitively_after_trimming(self):
        for response in ("Y\n", "y\n", "Yes\n", "yes\n", "YES\n", " yes \n", "\tYeS\t\n"):
            with self.subTest(response=response.strip()):
                reporter = _DummyReporter()

                confirmed = confirm_yes(
                    reporter=reporter,
                    input_stream=io.StringIO(response),
                    question="Proceed?",
                    instruction="Type Y/YES to continue or N/NO to cancel.",
                    cancel_message="Cancelled.",
                )

                self.assertTrue(confirmed)
                self.assertEqual(reporter.prompt_prepared, 1)
                self.assertNotIn("Cancelled.", reporter.lines)

    def test_confirm_yes_declines_no_responses_case_insensitively_after_trimming(self):
        for response in ("N\n", "n\n", "No\n", "no\n", "NO\n", " no \n", "\tNo\t\n"):
            with self.subTest(response=response.strip()):
                reporter = _DummyReporter()

                confirmed = confirm_yes(
                    reporter=reporter,
                    input_stream=io.StringIO(response),
                    question="Proceed?",
                    instruction="Type Y/YES to continue or N/NO to cancel.",
                    cancel_message="Cancelled.",
                )

                self.assertFalse(confirmed)
                self.assertEqual(reporter.prompt_prepared, 1)
                self.assertIn("Cancelled.", reporter.lines)

    def test_confirm_yes_reprompts_after_unrecognized_input(self):
        for response in ("\\\nY\n", "maybe\nY\n", "\nY\n", " y e s\nY\n"):
            with self.subTest(response=response.splitlines()[0]):
                reporter = _DummyReporter()

                confirmed = confirm_yes(
                    reporter=reporter,
                    input_stream=io.StringIO(response),
                    question="Proceed?",
                    instruction="Type Y/YES to continue or N/NO to cancel.",
                    cancel_message="Cancelled.",
                )

                self.assertTrue(confirmed)
                self.assertEqual(reporter.prompt_prepared, 2)
                self.assertNotIn("Cancelled.", reporter.lines)

    def test_moonraker_restart_url_replaces_query_endpoint(self):
        self.assertEqual(
            moonraker_restart_url("http://127.0.0.1:7125/printer/objects/query?print_stats"),
            "http://127.0.0.1:7125/printer/restart",
        )
        self.assertEqual(
            moonraker_restart_url("http://127.0.0.1:7125/moonraker/printer/objects/query?print_stats"),
            "http://127.0.0.1:7125/moonraker/printer/restart",
        )

    def test_machine_service_restart_url_replaces_query_endpoint(self):
        self.assertEqual(
            machine_service_restart_url(
                "http://127.0.0.1:7125/printer/objects/query?print_stats"
            ),
            "http://127.0.0.1:7125/machine/services/restart",
        )
        self.assertEqual(
            machine_service_restart_url(
                "http://127.0.0.1:7125/moonraker/printer/objects/query?print_stats"
            ),
            "http://127.0.0.1:7125/moonraker/machine/services/restart",
        )

    def test_maybe_restart_klipper_accepts_yes_responses_and_posts_restart_request(self):
        for response in ("Y\n", "y\n", "Yes\n", "yes\n", "YES\n", " yes \n"):
            with self.subTest(response=response.strip()):
                reporter = _DummyReporter()
                seen = {}

                def fake_urlopen(request, timeout=0):
                    seen["full_url"] = request.full_url
                    seen["method"] = request.get_method()
                    seen["timeout"] = timeout
                    return _DummyResponse()

                restarted = maybe_restart_klipper(
                    reporter=reporter,
                    input_stream=io.StringIO(response),
                    moonraker_query_url="http://127.0.0.1:7125/printer/objects/query?print_stats",
                    urlopen=fake_urlopen,
                )

                self.assertTrue(restarted)
                self.assertEqual(reporter.prompt_prepared, 1)
                self.assertEqual(seen["full_url"], "http://127.0.0.1:7125/printer/restart")
                self.assertEqual(seen["method"], "POST")
                self.assertEqual(seen["timeout"], 10)
                self.assertIn(messages.RESTARTING_KLIPPER, reporter.lines)
                self.assertIn(messages.KLIPPER_RESTARTED, reporter.lines)

    def test_process_restart_posts_machine_service_request(self):
        reporter = _DummyReporter()
        seen = {}

        def fake_urlopen(request, timeout=0):
            seen["full_url"] = request.full_url
            seen["method"] = request.get_method()
            seen["timeout"] = timeout
            seen["content_type"] = request.headers.get("Content-type")
            seen["data"] = request.data
            return _DummyResponse()

        restarted = maybe_restart_klipper(
            reporter=reporter,
            input_stream=io.StringIO("Y\n"),
            moonraker_query_url="http://127.0.0.1:7125/printer/objects/query?print_stats",
            process_restart_required=True,
            urlopen=fake_urlopen,
        )

        self.assertTrue(restarted)
        self.assertEqual(
            seen["full_url"],
            "http://127.0.0.1:7125/machine/services/restart",
        )
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["timeout"], 10)
        self.assertEqual(seen["content_type"], "application/json")
        self.assertEqual(seen["data"], b'{"service": "klipper"}')
        self.assertIn(messages.RESTARTING_KLIPPER_SERVICE, reporter.lines)
        self.assertIn(messages.KLIPPER_SERVICE_RESTART_REQUESTED, reporter.lines)

    def test_incomplete_process_restart_response_requires_manual_service_restart(self):
        reporter = _DummyReporter()
        restarted = maybe_restart_klipper(
            reporter=reporter,
            input_stream=io.StringIO("Y\n"),
            moonraker_query_url="http://127.0.0.1:7125/printer/objects/query?print_stats",
            process_restart_required=True,
            urlopen=lambda request, timeout=0: _IncompleteResponse(),
        )
        self.assertFalse(restarted)
        self.assertIn(messages.COULD_NOT_RESTART_KLIPPER_SERVICE, reporter.lines)

    def test_noninteractive_process_restart_requires_manual_service_restart(self):
        reporter = _DummyReporter()
        restarted = maybe_restart_klipper(
            reporter=reporter,
            input_stream=None,
            moonraker_query_url="http://127.0.0.1:7125/printer/objects/query?print_stats",
            process_restart_required=True,
        )
        self.assertFalse(restarted)
        self.assertIn(messages.RESTART_KLIPPER_SERVICE_TO_APPLY, reporter.lines)

    def test_maybe_restart_klipper_decline_prints_manual_restart_message(self):
        for response in ("N\n", "n\n", "No\n", "no\n", "NO\n", " no \n"):
            with self.subTest(response=response.strip()):
                reporter = _DummyReporter()
                restarted = maybe_restart_klipper(
                    reporter=reporter,
                    input_stream=io.StringIO(response),
                    moonraker_query_url="http://127.0.0.1:7125/printer/objects/query?print_stats",
                )

                self.assertFalse(restarted)
                self.assertEqual(reporter.prompt_prepared, 1)
                self.assertIn(messages.RESTART_KLIPPER_TO_APPLY, reporter.lines)
