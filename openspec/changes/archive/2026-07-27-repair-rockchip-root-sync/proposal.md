## Why

The QIDI Max 4 vendor `rockchip.service` remounts the root filesystem with `sync`, then fails before creating `/usr/local/first_boot_flag` because its RK3308 branch selects unsupported `rk3208` package setup. The resulting synchronous root mount repeats at every boot and materially increases small-write and metadata latency.

## What Changes

- Add an opted-in system optimization that prevents the defective first-boot service command from running while leaving the vendor unit and script unchanged.
- Remount the live root filesystem without the `sync` option and verify the effective unit command, service result, and mount options.
- Guard the operation against unsupported vendor shapes and preserve pre-existing operator-managed systemd overrides.
- Record the first durable preimage and integrate apply, reconciliation, rollback, dry-run, and uninstall behavior with the existing system-optimization ledger.
- Add safe host-reboot orchestration with a durable pending marker, final idle-state check, deferred systemd reboot, and post-boot verification.
- Apply the remediation through existing enabled system-optimization policy during both changed-release auto-update installation and already-current reconciliation, and complete any required reboot only after auto-update checksum state is committed.
- Record the observed vendor failure and controlled Max 4 performance measurements as OpenSpec evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: Enabled system optimizations neutralize the recognized defective Rockchip first-boot service, auto-update installs and reconciles the repair, and the installer can safely schedule and verify a host OS reboot.

## Impact

- `installer/package.yaml` system-optimization policy and validation
- `installer/runtime/models.py`, `manifest.py`, `system_optimizations.py`, `auto_update.py`, `cli.py`, and a host-reboot runtime helper
- `installer/release/install.sh` host-reboot CLI plumbing
- Installer system-optimization, auto-update, host-reboot, CLI, and integration tests
- `openspec/specs/installer-lifecycle/spec.md` and `openspec/observations/qidi-platform.md`
- Printer OS state under `/etc/systemd/system/rockchip.service.d/` and the live `/` mount
