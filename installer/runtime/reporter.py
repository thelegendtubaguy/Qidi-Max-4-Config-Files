from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from . import messages
from .errors import PreflightTargetsError, RollbackFailedError
from .models import DriftRecord, InstallPlan, PatchResult, PreflightReport, UninstallPlan
from .patches import (
    INSTALL_APPLIED,
    INSTALL_NOOP_DESIRED,
    UNINSTALL_NOOP_EXPECTED,
    UNINSTALL_REVERTED,
    USER_MODIFIED,
)
from .vendor_imports import (
    VendoredImportError,
    default_vendor_root,
    import_optional_vendored_module,
    vendor_root_from_bundle_root,
)


STATUS_STAGE_INDEX = {
    messages.CHECKING_FIRMWARE_VERSION: 1,
    messages.CHECKING_PACKAGE_VERSION: 2,
    messages.CHECKING_INSTALLED_PACKAGE: 2,
    messages.PERFORMING_PREFLIGHT_CHECKS: 3,
    messages.PERFORMING_UNINSTALL_PREFLIGHT_CHECKS: 3,
    messages.CREATING_BACKUP: 4,
    messages.INSTALLING: 5,
    messages.UNINSTALLING: 5,
}
STAGE_COUNT = 5
PANEL_TITLE = "QIDI Max 4 Optimized installer"
COUNTER_LABELS = {
    "files": "files",
    "sections": "sections",
    "lines": "lines",
    "patch_targets": "patch targets",
    "ensure_directories": "directories",
    "managed_trees": "managed tree",
    "ensure_lines": "include lines",
    "patches": "guarded patches",
    "postflight": "postflight",
    "state_write": "state file",
    "include_removal": "include removal",
    "managed_tree_drift": "drift check",
    "managed_tree_removal": "managed-tree removal",
    "state_remove": "state removal",
}


@dataclass(frozen=True)
class DetailGroup:
    heading: str
    rows: tuple[str, ...]


@dataclass(frozen=True)
class RichModules:
    Console: type
    Group: object
    Live: type
    Panel: type
    ProgressBar: type
    Table: type
    Text: type
    box: object


@dataclass
class ReporterState:
    stage_index: int | None = None
    current_status: str | None = None
    preflight_counter_label: str | None = None
    preflight_counters: dict[str, tuple[int, int]] = field(default_factory=dict)
    operation_counter_label: str | None = None
    operation_counters: dict[str, tuple[int, int]] = field(default_factory=dict)


class PlainReporter:
    def __init__(self, stream: TextIO | None = None, *, debug_enabled: bool = False):
        self.stream = stream or sys.stdout
        self.debug_enabled = debug_enabled

    def status(self, message: str) -> None:
        for line in format_status_lines(message):
            self.line(line)

    def debug(self, *, event: str, **fields) -> None:
        if not self.debug_enabled:
            return
        self.line(format_debug_line(event, fields))

    def emit_install_preflight_counters(
        self,
        *,
        files: tuple[int, int],
        sections: tuple[int, int],
        lines: tuple[int, int],
        patch_targets: tuple[int, int],
    ) -> None:
        return

    def emit_uninstall_preflight_counters(
        self,
        *,
        files: tuple[int, int],
        sections: tuple[int, int],
        lines: tuple[int, int],
        patch_targets: tuple[int, int],
    ) -> None:
        return

    def emit_install_counters(self, **counters: tuple[int, int]) -> None:
        return

    def emit_uninstall_counters(self, **counters: tuple[int, int]) -> None:
        return

    def line(self, message: str = "") -> None:
        self.stream.write(f"{message}\n")
        self.stream.flush()

    def prepare_for_prompt(self) -> None:
        return

    def emit_prompt(self, *, question: str, instruction: str) -> None:
        self.prepare_for_prompt()
        self.line(question)
        self.line(instruction)

    def emit_detail_groups(self, groups: tuple[DetailGroup, ...]) -> None:
        self._emit_groups(groups)

    def emit_backup_choices(self, entries: tuple[tuple[int, str, str, str], ...]) -> None:
        self.line("Available installer backups:")
        for index, created_at, label, path in entries:
            self.line(f"  {index}. {created_at} | {label} | {path}")

    def emit_restore_selection(self, *, label: str, path: str) -> None:
        self.line(f"Selected backup: {label}")
        self.line(f"Backup path: {path}")

    def emit_restore_warning(self, *, config_path: str) -> None:
        self.line(f"Warning: restore will overwrite current config changes under {config_path}.")
        self.line("This helper restores the full archived config snapshot.")
        self.line("This helper does not clear the recovery sentinel.")

    def emit_restore_complete(self, *, verified_path: str) -> None:
        self.line("Restore complete.")
        self.line(f"Verified restored tree: {verified_path}")
        self.line("Recovery sentinel was not cleared.")
        self.line("After verifying the restored tree, clear it with: install.sh --clear-recovery-sentinel")

    def emit_error(self, error: Exception) -> None:
        self.line(getattr(error, "message", str(error)))
        if isinstance(error, RollbackFailedError):
            for line in rollback_context_lines(error):
                self.line(line)
            self._emit_groups(rollback_failure_groups(error))
            self._emit_groups(rollback_recovery_groups(error))
        if isinstance(error, PreflightTargetsError):
            self._emit_groups(preflight_report_groups(error.report))

    def emit_install_success(
        self, *, patch_results: tuple[PatchResult, ...], managed_tree_drift: tuple[DriftRecord, ...]
    ) -> None:
        self.line(messages.INSTALLED)
        self._emit_groups(user_modified_patch_groups(patch_results))
        if managed_tree_drift:
            self._emit_groups(
                (
                    DetailGroup(
                        messages.MANAGED_TREE_DRIFT_OVERWRITTEN,
                        managed_tree_drift_rows(managed_tree_drift),
                    ),
                )
            )

    def emit_uninstall_success(
        self, *, patch_results: tuple[PatchResult, ...], managed_tree_drift: tuple[DriftRecord, ...]
    ) -> None:
        self.line(messages.UNINSTALLED)
        self._emit_groups(user_modified_patch_groups(patch_results))
        if managed_tree_drift:
            self._emit_groups(
                (
                    DetailGroup(
                        messages.MANAGED_TREE_DRIFT,
                        managed_tree_drift_rows(managed_tree_drift),
                    ),
                )
            )

    def emit_install_dry_run(self, *, plan: InstallPlan) -> None:
        self.line("Dry-run summary:")
        self.line(f"Backup label: {plan.backup_label}")
        self._emit_groups(install_dry_run_groups(plan))
        self.line("Dry-run complete. No changes made.")

    def emit_uninstall_dry_run(self, *, plan: UninstallPlan) -> None:
        self.line("Dry-run summary:")
        self.line(f"Backup label: {plan.backup_label}")
        self._emit_groups(uninstall_dry_run_groups(plan))
        self.line("Dry-run complete. No changes made.")

    def emit_nothing_to_uninstall(self) -> None:
        self.line(messages.NOTHING_TO_UNINSTALL)

    def emit_clear_recovery_sentinel(self, removed: bool) -> None:
        if removed:
            self.line("Recovery sentinel cleared.")
        else:
            self.line("Recovery sentinel was not present.")

    def _emit_groups(self, groups: tuple[DetailGroup, ...]) -> None:
        for group in groups:
            self.line(group.heading)
            for row in group.rows:
                self.line(f"  - {row}")


class RichReporter:
    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        bundle_root: Path | None = None,
        debug_enabled: bool = False,
    ):
        self.stream = stream or sys.stdout
        self.debug_enabled = debug_enabled
        self.rich = load_rich_modules(bundle_root=bundle_root)
        self.console = self.rich.Console(file=self.stream, highlight=False)
        self.state = ReporterState()
        self._live = None

    def line(self, message: str = "") -> None:
        self._print_line(message)

    def status(self, message: str) -> None:
        previous_stage = self.state.stage_index
        self.state.current_status = message
        self.state.stage_index = STATUS_STAGE_INDEX.get(message)
        if self.state.stage_index != previous_stage:
            if self.state.stage_index != 3:
                self.state.preflight_counter_label = None
                self.state.preflight_counters.clear()
            if self.state.stage_index != 5:
                self.state.operation_counter_label = None
                self.state.operation_counters.clear()
        self._refresh_live()

    def debug(self, *, event: str, **fields) -> None:
        if not self.debug_enabled:
            return
        self._print_line(format_debug_line(event, fields))

    def emit_install_preflight_counters(
        self,
        *,
        files: tuple[int, int],
        sections: tuple[int, int],
        lines: tuple[int, int],
        patch_targets: tuple[int, int],
    ) -> None:
        self.state.preflight_counter_label = "Preflight checks"
        self.state.preflight_counters = {
            "files": files,
            "sections": sections,
            "lines": lines,
            "patch_targets": patch_targets,
        }
        self._refresh_live_if_active()

    def emit_uninstall_preflight_counters(
        self,
        *,
        files: tuple[int, int],
        sections: tuple[int, int],
        lines: tuple[int, int],
        patch_targets: tuple[int, int],
    ) -> None:
        self.state.preflight_counter_label = "Uninstall preflight checks"
        self.state.preflight_counters = {
            "files": files,
            "sections": sections,
            "lines": lines,
            "patch_targets": patch_targets,
        }
        self._refresh_live_if_active()

    def emit_install_counters(self, **counters: tuple[int, int]) -> None:
        self.state.operation_counter_label = "Install progress"
        self.state.operation_counters = dict(counters)
        self._refresh_live_if_active()

    def emit_uninstall_counters(self, **counters: tuple[int, int]) -> None:
        self.state.operation_counter_label = "Uninstall progress"
        self.state.operation_counters = dict(counters)
        self._refresh_live_if_active()

    def emit_error(self, error: Exception) -> None:
        self._print_message_panel(
            getattr(error, "message", str(error)),
            title="Installer stopped",
            border_style="red",
            style="bold red",
        )
        if isinstance(error, RollbackFailedError):
            self._print_lines(rollback_context_lines(error))
            self._print_groups(rollback_failure_groups(error))
            self._print_groups(rollback_recovery_groups(error))
        if isinstance(error, PreflightTargetsError):
            self._print_groups(preflight_report_groups(error.report))

    def emit_install_success(
        self, *, patch_results: tuple[PatchResult, ...], managed_tree_drift: tuple[DriftRecord, ...]
    ) -> None:
        self._print_message_panel(
            messages.INSTALLED,
            title="Complete",
            border_style="green",
            style="bold green",
        )
        self._print_groups(user_modified_patch_groups(patch_results))
        if managed_tree_drift:
            self._print_groups(
                (
                    DetailGroup(
                        messages.MANAGED_TREE_DRIFT_OVERWRITTEN,
                        managed_tree_drift_rows(managed_tree_drift),
                    ),
                )
            )

    def emit_uninstall_success(
        self, *, patch_results: tuple[PatchResult, ...], managed_tree_drift: tuple[DriftRecord, ...]
    ) -> None:
        self._print_message_panel(
            messages.UNINSTALLED,
            title="Complete",
            border_style="green",
            style="bold green",
        )
        self._print_groups(user_modified_patch_groups(patch_results))
        if managed_tree_drift:
            self._print_groups(
                (
                    DetailGroup(
                        messages.MANAGED_TREE_DRIFT,
                        managed_tree_drift_rows(managed_tree_drift),
                    ),
                )
            )

    def emit_install_dry_run(self, *, plan: InstallPlan) -> None:
        self._print_message_panel(
            "Dry-run summary:",
            title="Dry run",
            border_style="cyan",
            style="bold cyan",
        )
        self._print_key_values("Dry-run metadata", (("Backup label", plan.backup_label),))
        self._print_groups(install_dry_run_groups(plan))
        self._print_message_panel(
            "Dry-run complete. No changes made.",
            title="Complete",
            border_style="green",
            style="bold green",
        )

    def emit_uninstall_dry_run(self, *, plan: UninstallPlan) -> None:
        self._print_message_panel(
            "Dry-run summary:",
            title="Dry run",
            border_style="cyan",
            style="bold cyan",
        )
        self._print_key_values("Dry-run metadata", (("Backup label", plan.backup_label),))
        self._print_groups(uninstall_dry_run_groups(plan))
        self._print_message_panel(
            "Dry-run complete. No changes made.",
            title="Complete",
            border_style="green",
            style="bold green",
        )

    def emit_nothing_to_uninstall(self) -> None:
        self._print_message_panel(
            messages.NOTHING_TO_UNINSTALL,
            title="No action",
            border_style="yellow",
            style="bold yellow",
        )

    def emit_clear_recovery_sentinel(self, removed: bool) -> None:
        if removed:
            self._print_message_panel(
                "Recovery sentinel cleared.",
                title="Recovery",
                border_style="green",
                style="bold green",
            )
        else:
            self._print_message_panel(
                "Recovery sentinel was not present.",
                title="Recovery",
                border_style="yellow",
                style="bold yellow",
            )

    def _refresh_live(self) -> None:
        if self._live is None:
            self._live = self.rich.Live(
                self._render_live(),
                console=self.console,
                auto_refresh=False,
                refresh_per_second=8,
                transient=True,
            )
            self._live.start()
            return
        self._live.update(self._render_live(), refresh=True)

    def _refresh_live_if_active(self) -> None:
        if self._live is not None:
            self._live.update(self._render_live(), refresh=True)

    def _stop_live(self) -> None:
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    def _print_lines(self, lines: tuple[str, ...]) -> None:
        if not lines:
            return
        self._print_key_values("Run context", tuple(_split_context_line(line) for line in lines))

    def _print_line(self, message: str = "") -> None:
        self._stop_live()
        self.console.print(message, markup=False, highlight=False, soft_wrap=True)

    def _print_message_panel(
        self,
        message: str,
        *,
        title: str,
        border_style: str,
        style: str = "",
    ) -> None:
        self._stop_live()
        self.console.print(
            self.rich.Panel(
                self.rich.Text(message, style=style),
                title=title,
                border_style=border_style,
                box=self.rich.box.ROUNDED,
            )
        )

    def _print_key_values(
        self,
        title: str,
        rows: tuple[tuple[str, str], ...],
        *,
        border_style: str = "cyan",
    ) -> None:
        if not rows:
            return
        self._stop_live()
        table = self.rich.Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(ratio=1)
        for key, value in rows:
            table.add_row(
                self.rich.Text(str(key), style="bold"),
                self.rich.Text(str(value)),
            )
        self.console.print(
            self.rich.Panel(
                table,
                title=title,
                border_style=border_style,
                box=self.rich.box.ROUNDED,
            )
        )

    def _print_groups(self, groups: tuple[DetailGroup, ...]) -> None:
        self._stop_live()
        for group in groups:
            if not group.rows:
                continue
            bullet_table = self.rich.Table.grid(padding=(0, 1))
            bullet_table.add_column(no_wrap=True)
            bullet_table.add_column(ratio=1)
            for row in group.rows:
                bullet_table.add_row(
                    self.rich.Text("•", style="cyan"),
                    self.rich.Text(row),
                )
            self.console.print(
                self.rich.Panel(
                    bullet_table,
                    title=group.heading,
                    border_style="cyan",
                    box=self.rich.box.ROUNDED,
                )
            )

    def prepare_for_prompt(self) -> None:
        self._stop_live()

    def emit_prompt(self, *, question: str, instruction: str) -> None:
        self._stop_live()
        body = self.rich.Table.grid(padding=(0, 0))
        body.add_row(self.rich.Text(question, style="bold"))
        body.add_row(self.rich.Text(instruction, style="dim"))
        self.console.print(
            self.rich.Panel(
                body,
                title="Confirmation required",
                border_style="yellow",
                box=self.rich.box.ROUNDED,
            )
        )

    def emit_detail_groups(self, groups: tuple[DetailGroup, ...]) -> None:
        self._print_groups(groups)

    def emit_backup_choices(self, entries: tuple[tuple[int, str, str, str], ...]) -> None:
        self._stop_live()
        table = self.rich.Table(
            title="Available installer backups",
            box=self.rich.box.ROUNDED,
            header_style="bold cyan",
            show_lines=False,
        )
        table.add_column("#", justify="right", no_wrap=True)
        table.add_column("Created", no_wrap=True)
        table.add_column("Label")
        table.add_column("Path")
        for index, created_at, label, path in entries:
            table.add_row(
                self.rich.Text(str(index)),
                self.rich.Text(created_at),
                self.rich.Text(label),
                self.rich.Text(path),
            )
        self.console.print(table)

    def emit_restore_selection(self, *, label: str, path: str) -> None:
        self._print_key_values(
            "Selected backup",
            (("Selected backup:", label), ("Backup path:", path)),
        )

    def emit_restore_warning(self, *, config_path: str) -> None:
        self._print_groups(
            (
                DetailGroup(
                    "Restore warning:",
                    (
                        f"Warning: restore will overwrite current config changes under {config_path}.",
                        "This helper restores the full archived config snapshot.",
                        "This helper does not clear the recovery sentinel.",
                    ),
                ),
            )
        )

    def emit_restore_complete(self, *, verified_path: str) -> None:
        self._print_message_panel(
            "Restore complete.",
            title="Complete",
            border_style="green",
            style="bold green",
        )
        self._print_groups(
            (
                DetailGroup(
                    "Restore verification:",
                    (
                        f"Verified restored tree: {verified_path}",
                        "Recovery sentinel was not cleared.",
                        "After verifying the restored tree, clear it with: install.sh --clear-recovery-sentinel",
                    ),
                ),
            )
        )

    def _render_live(self):
        return self.rich.Group(self._render_status_panel())

    def _render_status_panel(self):
        table = self.rich.Table.grid(padding=(0, 1))
        table.expand = True
        stage_index = self.state.stage_index or 0
        stage_label = f"stage {stage_index}/{STAGE_COUNT}"
        table.add_row(self.rich.Text(stage_label, style="bold cyan"))
        table.add_row(self.rich.ProgressBar(total=STAGE_COUNT, completed=stage_index))
        if self.state.current_status is not None:
            table.add_row(self.rich.Text(self.state.current_status, style="bold"))
        if self.state.preflight_counters:
            table.add_row(
                self._render_counter_section(
                    self.state.preflight_counter_label or "Preflight checks",
                    self.state.preflight_counters,
                )
            )
        if self.state.operation_counters:
            table.add_row(
                self._render_counter_section(
                    self.state.operation_counter_label or "Progress",
                    self.state.operation_counters,
                )
            )
        return self.rich.Panel(
            table,
            title=PANEL_TITLE,
            border_style="cyan",
            box=self.rich.box.ROUNDED,
        )

    def _render_counter_section(self, title: str, counters: dict[str, tuple[int, int]]):
        table = self.rich.Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(ratio=1)
        table.add_column(justify="right", no_wrap=True)
        table.add_row("", self.rich.Text(title, style="bold magenta"), "")
        for name, value in counters.items():
            completed, total = value
            if total == 0:
                continue
            done = completed >= total
            in_progress = completed > 0 and not done
            icon_style = "green" if done else "cyan" if in_progress else "dim"
            icon = "✓" if done else "›" if in_progress else "•"
            table.add_row(
                self.rich.Text(icon, style=icon_style),
                self.rich.Text(format_counter_label(name)),
                self.rich.Text(f"{completed}/{total}", style=icon_style),
            )
        return table



def create_reporter(
    stream: TextIO | None = None,
    *,
    prefer_plain: bool = False,
    bundle_root: Path | None = None,
    environ: dict[str, str] | None = None,
    debug: bool = False,
):
    target_stream = stream or sys.stdout
    env = os.environ if environ is None else environ
    if prefer_plain or not stream_supports_live_tui(target_stream, environ=env):
        return PlainReporter(target_stream, debug_enabled=debug)
    try:
        return RichReporter(target_stream, bundle_root=bundle_root, debug_enabled=debug)
    except (ImportError, VendoredImportError):
        return PlainReporter(target_stream, debug_enabled=debug)



def load_rich_modules(*, bundle_root: Path | None = None) -> RichModules:
    vendor_root = (
        vendor_root_from_bundle_root(bundle_root)
        if bundle_root is not None
        else default_vendor_root()
    )
    import_optional_vendored_module("rich", vendor_root)
    from rich import box as rich_box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress_bar import ProgressBar
    from rich.table import Table
    from rich.text import Text

    return RichModules(
        Console=Console,
        Group=Group,
        Live=Live,
        Panel=Panel,
        ProgressBar=ProgressBar,
        Table=Table,
        Text=Text,
        box=rich_box,
    )



def stream_supports_live_tui(
    stream: TextIO,
    *,
    environ: dict[str, str] | None = None,
) -> bool:
    isatty = getattr(stream, "isatty", None)
    if isatty is None or not isatty():
        return False
    env = os.environ if environ is None else environ
    return env.get("TERM") != "dumb"



def format_status_lines(message: str) -> tuple[str, ...]:
    stage_index = STATUS_STAGE_INDEX.get(message)
    if stage_index is None:
        return (message,)
    return (f"stage {stage_index}/{STAGE_COUNT}", message)



def format_counter_label(name: str) -> str:
    return COUNTER_LABELS.get(name, name.replace("_", " "))



def _split_context_line(line: str) -> tuple[str, str]:
    key, separator, value = line.partition(":")
    if separator:
        return f"{key}:", value.strip()
    return "", line




def format_debug_line(event: str, fields: dict[str, object]) -> str:
    parts = [f"debug | event={event}"]
    for key, value in sorted(fields.items()):
        parts.append(f"{key}={_format_debug_value(value)}")
    return " | ".join(parts)



def preflight_report_groups(report: PreflightReport) -> tuple[DetailGroup, ...]:
    groups = []
    if report.missing_files:
        groups.append(DetailGroup("Files:", tuple(report.missing_files)))
    if report.missing_sections:
        groups.append(
            DetailGroup(
                "Sections:",
                tuple(f"{item.file} :: [{item.section}]" for item in report.missing_sections),
            )
        )
    if report.missing_lines:
        groups.append(
            DetailGroup(
                "Lines:",
                tuple(f"{item.file} :: {item.line}" for item in report.missing_lines),
            )
        )
    if report.patch_target_issues:
        groups.append(
            DetailGroup(
                "Patch targets:",
                tuple(
                    f"{item.reason}: {item.file} :: [{item.section}] :: {item.option} ({item.id})"
                    for item in report.patch_target_issues
                ),
            )
        )
    return tuple(groups)



def user_modified_patch_groups(patch_results: tuple[PatchResult, ...]) -> tuple[DetailGroup, ...]:
    rows = tuple(
        f"{item.id} | {item.file} | [{item.section}] | {item.option} | {item.current}"
        for item in patch_results
        if item.classification == USER_MODIFIED
    )
    if not rows:
        return ()
    return (DetailGroup(messages.USER_MODIFIED_PATCH_TARGETS, rows),)



def managed_tree_drift_rows(managed_tree_drift: tuple[DriftRecord, ...]) -> tuple[str, ...]:
    return tuple(f"{drift.path} | {drift.sha256_before_remove}" for drift in managed_tree_drift)



def rollback_context_lines(error: RollbackFailedError) -> tuple[str, ...]:
    details = error.details
    lines = []
    if details.backup_label:
        lines.append(f"Backup label: {details.backup_label}")
    if details.backup_zip_path:
        lines.append(f"Backup zip: {details.backup_zip_path}")
    if details.restore_target_path:
        lines.append(f"Restore target: {details.restore_target_path}")
    if details.recovery_sentinel_path:
        lines.append(f"Recovery sentinel: {details.recovery_sentinel_path}")
    return tuple(lines)



def rollback_failure_groups(error: RollbackFailedError) -> tuple[DetailGroup, ...]:
    details = error.details
    if not details.failed_paths:
        return ()
    return (DetailGroup("Rollback failures:", details.failed_paths),)



def rollback_recovery_groups(error: RollbackFailedError) -> tuple[DetailGroup, ...]:
    details = error.details
    rows = []
    if details.backup_zip_path and details.restore_target_path:
        rows.append(
            f"Restore the full archived snapshot from {details.backup_zip_path} into {details.restore_target_path}."
        )
        rows.append(
            f"Verify {details.restore_target_path} matches the archive before clearing the recovery sentinel."
        )
    elif details.backup_zip_path:
        rows.append(f"Restore the full archived snapshot from {details.backup_zip_path}.")
    if details.recovery_sentinel_path:
        rows.append(f"Recovery sentinel remains at {details.recovery_sentinel_path}.")
    rows.append(f"Clear the recovery sentinel with: {details.clear_command}")
    return (DetailGroup("Recovery actions:", tuple(rows)),)



def install_dry_run_groups(plan: InstallPlan) -> tuple[DetailGroup, ...]:
    return (
        DetailGroup("Managed-tree intent:", managed_tree_install_intent_rows(plan)),
        DetailGroup("External-file intent:", external_file_intent_rows(plan.external_file_intents)),
        DetailGroup("Include-line intent:", install_include_intent_rows(plan)),
        DetailGroup("Guarded patch intent:", install_patch_intent_rows(plan.patch_results)),
        DetailGroup("Managed tree drift detection:", drift_report_rows(plan.managed_tree_drift)),
        DetailGroup("State-file intent:", (f"would write {plan.state_file_intent.path}",)),
    )



def uninstall_dry_run_groups(plan: UninstallPlan) -> tuple[DetailGroup, ...]:
    return (
        DetailGroup("Managed-tree intent:", managed_tree_uninstall_intent_rows(plan)),
        DetailGroup("External-file intent:", external_file_intent_rows(plan.external_file_intents)),
        DetailGroup("Include-line intent:", uninstall_include_intent_rows(plan)),
        DetailGroup(
            "Guarded patch reversal intent:",
            uninstall_patch_intent_rows(plan.patch_results),
        ),
        DetailGroup("Managed tree drift detection:", drift_report_rows(plan.managed_tree_drift)),
        DetailGroup("State-file intent:", (f"would delete {plan.state_file_intent.path}",)),
    )



def external_file_intent_rows(intents) -> tuple[str, ...]:
    rows = []
    for item in intents:
        if item.source is None:
            rows.append(f"would {item.action} {item.destination}")
        else:
            rows.append(f"would {item.action} {item.source} -> {item.destination}")
    return tuple(rows)


def install_patch_intent_rows(patch_results: tuple[PatchResult, ...]) -> tuple[str, ...]:
    rows = []
    for item in patch_results:
        if item.classification == INSTALL_APPLIED:
            rows.append(
                f"would apply {item.id} | {item.file} | [{item.section}] | {item.option} | {item.expected} -> {item.desired}"
            )
            continue
        if item.classification == INSTALL_NOOP_DESIRED:
            rows.append(
                f"already desired {item.id} | {item.file} | [{item.section}] | {item.option} | {item.current}"
            )
            continue
        rows.append(
            f"would preserve user-modified {item.id} | {item.file} | [{item.section}] | {item.option} | {item.current}"
        )
    return tuple(rows)



def uninstall_patch_intent_rows(patch_results: tuple[PatchResult, ...]) -> tuple[str, ...]:
    rows = []
    for item in patch_results:
        if item.classification == UNINSTALL_REVERTED:
            rows.append(
                f"would revert {item.id} | {item.file} | [{item.section}] | {item.option} | {item.desired} -> {item.expected}"
            )
            continue
        if item.classification == UNINSTALL_NOOP_EXPECTED:
            rows.append(
                f"already expected {item.id} | {item.file} | [{item.section}] | {item.option} | {item.current}"
            )
            continue
        rows.append(
            f"would preserve user-modified {item.id} | {item.file} | [{item.section}] | {item.option} | {item.current}"
        )
    return tuple(rows)



def drift_report_rows(managed_tree_drift: tuple[DriftRecord, ...]) -> tuple[str, ...]:
    if not managed_tree_drift:
        return ("none",)
    return managed_tree_drift_rows(managed_tree_drift)



def managed_tree_install_intent_rows(plan: InstallPlan) -> tuple[str, ...]:
    intent = plan.managed_tree_intent
    return (f"mirror {intent.source} -> {intent.destination}",)



def managed_tree_uninstall_intent_rows(plan: UninstallPlan) -> tuple[str, ...]:
    return (f"would remove {plan.managed_tree_intent.destination}",)



def install_include_intent_rows(plan: InstallPlan) -> tuple[str, ...]:
    rows = []
    for intent in plan.include_line_intents:
        if intent.action == "ensure":
            rows.append(f"would ensure {intent.file} :: {intent.line} after {intent.after}")
        else:
            rows.append(f"already present {intent.file} :: {intent.line}")
    return tuple(rows)



def uninstall_include_intent_rows(plan: UninstallPlan) -> tuple[str, ...]:
    rows = []
    for intent in plan.include_line_intents:
        if intent.action == "remove":
            rows.append(f"would remove {intent.file} :: {intent.line}")
        else:
            rows.append(f"already absent {intent.file} :: {intent.line}")
    return tuple(rows)



def _format_debug_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    if isinstance(value, Path):
        text = str(value)
    else:
        text = str(value)
    if not text:
        return "''"
    if any(char.isspace() or char in {"|", "="} for char in text):
        return repr(text)
    return text
