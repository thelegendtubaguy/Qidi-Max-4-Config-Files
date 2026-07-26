from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import messages
from .models import PreflightReport


class InstallerError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class FirmwareDetectionError(InstallerError):
    def __init__(self):
        super().__init__(messages.COULD_NOT_DETECT_FIRMWARE_VERSION)


class UnsupportedFirmwareError(InstallerError):
    def __init__(self):
        super().__init__(messages.YOUR_FIRMWARE_VERSION_IS_NOT_SUPPORTED)


class PreviousPackageValidationError(InstallerError):
    def __init__(self):
        super().__init__(messages.COULD_NOT_VALIDATE_PREVIOUS_PACKAGE_VERSION)


class PreflightTargetsError(InstallerError):
    def __init__(self, report: PreflightReport):
        super().__init__(messages.CANNOT_CONTINUE_MISSING_THINGS)
        self.report = report


class PrinterStateError(InstallerError):
    def __init__(self):
        super().__init__(messages.COULD_NOT_DETERMINE_PRINTER_STATE)


class ManagedTreeSourceError(InstallerError):
    pass


class ExternalFileError(InstallerError):
    pass


class PathSafetyError(InstallerError):
    pass


class ActivePrintError(InstallerError):
    def __init__(self):
        super().__init__(messages.CANNOT_CONTINUE_ACTIVE_PRINT)


class FreeSpaceError(InstallerError):
    def __init__(self):
        super().__init__(messages.NOT_ENOUGH_FREE_SPACE)


class InstalledPackageValidationError(InstallerError):
    def __init__(self):
        super().__init__(messages.COULD_NOT_VALIDATE_INSTALLED_PACKAGE_STATE)


class RecoveryRequiredError(InstallerError):
    def __init__(self):
        super().__init__(messages.PREVIOUS_RECOVERY_DID_NOT_COMPLETE)


class LockAcquisitionError(InstallerError):
    pass


class RecoverySentinelClearError(InstallerError):
    pass


class OperationCancelled(InstallerError):
    pass


@dataclass(frozen=True)
class RollbackFailureDetails:
    backup_label: Optional[str]
    backup_zip_path: Optional[Path]
    failed_paths: tuple[str, ...]
    recovery_sentinel_path: Optional[Path]
    restore_target_path: Optional[Path]
    clear_command: str


class RollbackFailedError(InstallerError):
    def __init__(
        self,
        original_error: Exception,
        backup_label: Optional[str],
        backup_zip_path: Optional[Path],
        failed_paths: tuple[str, ...],
        *,
        recovery_sentinel_path: Optional[Path] = None,
        restore_target_path: Optional[Path] = None,
        clear_command: str = "install.sh --clear-recovery-sentinel",
    ):
        message = getattr(original_error, "message", str(original_error))
        super().__init__(message)
        self.original_error = original_error
        self.details = RollbackFailureDetails(
            backup_label=backup_label,
            backup_zip_path=backup_zip_path,
            failed_paths=failed_paths,
            recovery_sentinel_path=recovery_sentinel_path,
            restore_target_path=restore_target_path,
            clear_command=clear_command,
        )
