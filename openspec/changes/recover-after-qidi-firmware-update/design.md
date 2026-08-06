## Context

QIDI SOC package `preinst` cleanup removes files under `/home/qidi/printer_data/config` unless their basenames are allowlisted. TLTG's optimized macro tree, installed-state ledger, and checksum state are removed, while `/etc/systemd/system/tltg-optimized-auto-update.{service,timer}` and `~/tltg-optimized-macros` survive. The next timer run sees no checksum and initializes it without invoking the installer.

## Goals / Non-Goals

**Goals:**

- Preserve explicit auto-update enrollment across QIDI firmware config cleanup.
- Re-run the checksum-verified current installer when an enrolled installation is missing or fails critical postflight checks.
- Keep the existing idle-printer, archive-validation, installer-preflight, rollback, and checksum-advancement controls.
- Avoid reinstalling from a manually invoked bundle that was never enrolled for auto-updates.

**Non-Goals:**

- Prevent QIDI firmware from replacing configuration or Klipper source files.
- Install optimized behavior on unsupported firmware.
- Treat slicer-side version comments as installed runtime version evidence.

## Decisions

### Persist enrollment at printer-data root

`enable_auto_updates` writes `/home/qidi/printer_data/.tltg_optimized_auto_update_enrolled` with mode `0600` only after systemd setup succeeds. The path survives QIDI's `config/` cleanup. `disable_auto_updates` removes enrollment before sudo authentication or systemd mutation; even if privileged unit cleanup fails, a surviving service cannot install. Checksum state is removed after successful unit cleanup.

A systemd-unit existence check alone is not durable intent: failed uninstall cleanup can leave unit files. A config-local marker cannot survive the firmware operation being recovered from.

### Reconcile only enrolled installations

Without the durable marker, a fetched checksum is recorded without installation whether prior checksum state is missing or changed. This keeps a surviving timer inert after uninstall and preserves non-enrolled manual checks. When the marker is present, missing checksum or installed state triggers the current checksum-verified release installer after the existing idle check.

For a matching checksum, the updater checks the installed-state identity and critical postflight surfaces: managed macro files, optimized include wiring, and firmware-scoped managed Klipper source. Drift triggers the same installer path and returns a reconciliation action instead of `already-current`.

The normal installer remains the sole mutation path. It validates firmware support and stock/prior-managed values, creates backups, restores on failure, activates Klipper source changes, and writes a new installed-state ledger.

### Seed enrollment through normal release upgrade

Existing auto-update users do not have the durable marker. A changed-release install repairs the already-configured systemd units through `enable_auto_updates`, which creates the marker. The reported printer initialized the `26.08.06.1` checksum before this change, so the changed checksum for `26.08.06.2` selects the normal update path. If a pre-`26.08.06.2` updater performs its first post-cleanup check only after `26.08.06.2` is already current, it initializes that checksum without downloading the recovery code; direct execution of `install.sh` or a later changed release is required once for that rollout case.

## Risks / Trade-offs

- **Marker survives a manually removed timer** → Reconciliation still requires explicit execution of `auto-update.sh --run`; disabling through the supported command removes the marker.
- **Unsupported new firmware reaches reconciliation** → The normal installer rejects it before backup or live writes and checksum state does not advance.
- **Critical checks do not exhaustively compare every guarded patch** → Firmware replacement removes include wiring and managed Klipper source as part of the same package operation; the installer performs exhaustive guarded preflight once reconciliation starts.
- **Auto-update disable cannot authenticate sudo** → Uninstall reports the unit-cleanup failure but removes durable enrollment first, leaving any surviving timer unable to install or reconcile.
