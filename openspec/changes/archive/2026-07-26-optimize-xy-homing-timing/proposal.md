## Why

QIDI's closed-loop X/Y homing spends substantial wall time in conservative transition dwells and uses a slower first strike than the printer handled reliably during supervised testing. The optimized installer also performs only a Klipper configuration restart, which cannot load managed Python source changes and is skipped entirely by noninteractive auto-update installs.

## What Changes

- Increase X/Y first-strike homing speed from the current optimized `65 mm/s` to `100 mm/s`; retain the `55 mm/s` second strike, two-pass validation, tolerance checks, retries, retract distance, and retract speed.
- Patch QIDI's `/home/qidi/klipper/klippy/extras/homing.py` so X/Y entry transitions use `100 ms` waits, recovery transitions use `50 ms` waits, and the per-axis pre-home dwell is `250 ms`; retain the `1 s` Z dwell.
- Add firmware-scoped, SHA-256-guarded deployment for the vendor Python source. Installation fails closed on an unknown preimage, writes atomically, verifies the installed hash, records the exact preimage for rollback and uninstall, and preserves drift rather than overwriting it.
- Support fresh stock `50 -> 100 mm/s` config patches and migration of installer-managed `65 -> 100 mm/s` values without treating the prior optimized value as user drift.
- Require a verified Klipper service-process restart whenever installation, auto-update, rollback, or uninstall changes managed Python source. A normal `POST /printer/restart` is insufficient for this path.
- Make unattended auto-update restart the Klipper service process after an idle source-changing install and verify that Klipper returns ready under a new process identity before recording the update checksum as complete.
- Remove temporary timing instrumentation from production artifacts; diagnostic commands and log markers are not installed.
- Bump the installer package version to `26.07.26.1` through `scripts/bump_installer_version.py`.
- Update installer, auto-update, restore, stock-vs-optimized, and homing behavior documentation and add focused install, migration, rollback, uninstall, restart, and auto-update tests.

## Capabilities

### New Capabilities
- `xy-homing-optimization`: Defines the optimized X/Y first and second strike speeds, transition waits, XY-only pre-home dwell, and retained safety behavior.
- `guarded-klipper-source-patching`: Defines firmware-scoped vendor-source preimage validation, atomic deployment, state recording, rollback, uninstall, and drift handling.
- `klipper-process-restart`: Defines when a service-process restart is required and how interactive install, unattended auto-update, rollback, and uninstall verify that imported Python code was reloaded.

### Modified Capabilities
- `firmware-baseline-support`: Extends each supported firmware baseline with an exact vendor `homing.py` preimage and desired patched hash.

## Impact

- Installer manifest, runtime models, manifest/state validation, preflight, path safety, free-space accounting, install transaction, rollback, uninstall, restart interaction, and auto-update completion semantics.
- `config/printer.cfg` guarded X/Y `homing_speed` patches and migration from prior installer-managed values.
- Bundled Klipper source-patch payload under `installer/klipper/` and runtime target `/home/qidi/klipper/klippy/extras/homing.py`.
- Installed-state and supported-upgrade metadata, while retaining schema compatibility for prior valid state ledgers.
- Installer package version `26.07.26.1`, runtime banner, release bundle metadata, tests, and documentation.
