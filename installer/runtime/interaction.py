from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, TextIO

from . import messages, safety
from .errors import ActivePrintError, PrinterStateError

UrlOpenFn = Callable[..., object]

_YES_RESPONSES = {"Y", "YES"}
_NO_RESPONSES = {"N", "NO"}


def prompt_yes(
    *,
    reporter,
    input_stream: TextIO,
    question: str,
    instruction: str,
) -> bool:
    while True:
        if hasattr(reporter, "emit_prompt"):
            reporter.emit_prompt(question=question, instruction=instruction)
        else:
            reporter.prepare_for_prompt()
            reporter.line(question)
            reporter.line(instruction)
        raw_response = input_stream.readline()
        if raw_response == "":
            return False
        response = raw_response.strip().upper()
        if response in _YES_RESPONSES:
            return True
        if response in _NO_RESPONSES:
            return False


def confirm_yes(
    *,
    reporter,
    input_stream: TextIO | None,
    question: str,
    instruction: str,
    cancel_message: str,
) -> bool:
    if input_stream is None:
        return True
    if prompt_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=question,
        instruction=instruction,
    ):
        return True
    reporter.line(cancel_message)
    return False



def maybe_restart_pending_service(
    *,
    paths,
    allowed_entries,
    reporter,
    input_stream: TextIO | None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
    prompt: bool = True,
) -> bool:
    from .process_restart import ProcessRestartError, restart_pending

    if prompt and input_stream is not None and not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.KLIPPER_SERVICE_RESTART_PROMPT,
        instruction=messages.KLIPPER_SERVICE_RESTART_PROMPT_INSTRUCTION,
        cancel_message=messages.KLIPPER_SERVICE_RESTART_PENDING,
    ):
        if hasattr(reporter, "debug"):
            reporter.debug(event="klipper.process_restart.declined")
        return False
    try:
        restart_pending(paths, allowed_entries=allowed_entries, urlopen=urlopen)
    except ProcessRestartError as exc:
        if hasattr(reporter, "debug"):
            reporter.debug(event="klipper.process_restart.failed", message=exc.message)
        reporter.line(messages.KLIPPER_SERVICE_RESTART_FAILED)
        if input_stream is None:
            raise
        return False
    if hasattr(reporter, "debug"):
        reporter.debug(event="klipper.process_restart.verified")
    reporter.line(messages.KLIPPER_SERVICE_RESTARTED)
    return True


def maybe_restart_pending_service_if_idle(
    *,
    paths,
    allowed_entries,
    reporter,
    input_stream: TextIO | None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
    except ActivePrintError:
        reporter.debug(event="klipper.process_restart.deferred", reason="active_print")
        reporter.line(messages.KLIPPER_SERVICE_RESTART_DEFERRED_ACTIVE_PRINT)
        return False
    except PrinterStateError:
        reporter.debug(event="klipper.process_restart.deferred", reason="unknown_state")
        reporter.line(messages.KLIPPER_SERVICE_RESTART_DEFERRED_UNKNOWN_STATE)
        return False
    return maybe_restart_pending_service(
        paths=paths,
        allowed_entries=allowed_entries,
        reporter=reporter,
        input_stream=input_stream,
        urlopen=urlopen,
        prompt=False,
    )


def maybe_restart_klipper(
    *,
    reporter,
    input_stream: TextIO | None,
    moonraker_query_url: str,
    process_restart_required: bool = False,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    manual_message = (
        messages.RESTART_KLIPPER_SERVICE_TO_APPLY
        if process_restart_required
        else messages.RESTART_KLIPPER_TO_APPLY
    )
    if input_stream is None:
        reporter.line(manual_message)
        return False
    if not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=(
            messages.RESTART_KLIPPER_SERVICE_PROMPT
            if process_restart_required
            else messages.RESTART_KLIPPER_PROMPT
        ),
        instruction=messages.RESTART_KLIPPER_PROMPT_INSTRUCTION,
        cancel_message=manual_message,
    ):
        return False

    if process_restart_required:
        reporter.line(messages.RESTARTING_KLIPPER_SERVICE)
        request = urllib.request.Request(
            machine_service_restart_url(moonraker_query_url),
            data=json.dumps({"service": "klipper"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        success_message = messages.KLIPPER_SERVICE_RESTART_REQUESTED
        failure_message = messages.COULD_NOT_RESTART_KLIPPER_SERVICE
    else:
        reporter.line(messages.RESTARTING_KLIPPER)
        request = urllib.request.Request(
            moonraker_restart_url(moonraker_query_url),
            data=b"",
            method="POST",
        )
        success_message = messages.KLIPPER_RESTARTED
        failure_message = messages.COULD_NOT_RESTART_KLIPPER
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except (OSError, http.client.HTTPException, urllib.error.URLError, ValueError):
        reporter.line(failure_message)
        return False
    reporter.line(success_message)
    return True



def moonraker_restart_url(moonraker_query_url: str) -> str:
    return _moonraker_url(moonraker_query_url, "/printer/restart")


def machine_service_restart_url(moonraker_query_url: str) -> str:
    return _moonraker_url(moonraker_query_url, "/machine/services/restart")


def _moonraker_url(moonraker_query_url: str, endpoint: str) -> str:
    parts = urllib.parse.urlsplit(moonraker_query_url)
    prefix = ""
    if parts.path.endswith("/printer/objects/query"):
        prefix = parts.path[: -len("/printer/objects/query")]
    path = f"{prefix}{endpoint}" if prefix else endpoint
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
