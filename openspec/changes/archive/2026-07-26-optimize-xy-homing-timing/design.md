## Context

The optimized manifest currently writes `stepper_x.homing_speed` and `stepper_y.homing_speed` from stock `50` to optimized `65`, while retaining `second_homing_speed: 55.0`. QIDI's closed-loop two-pass implementation is in `/home/qidi/klipper/klippy/extras/homing.py`, outside the installer-managed `config/` tree.

Supervised firmware `01.01.06.04` testing produced first-attempt X/Y homes at `100/55 mm/s` with `100 ms` entry waits, `50 ms` recovery waits, and a `250 ms` XY pre-axis dwell. The final production patch retains QIDI's two strikes, `20 mm` retract, tolerance validation, retry loop, controller-state command order, final backoff, and the `1 s` Z pre-axis dwell. Timing instrumentation and `TLTG_HOME_TIME_MARK` diagnostics are excluded.

The supported vendor source preimages are:

| Firmware | Stock `homing.py` SHA-256 | Desired SHA-256 |
|---|---|---|
| `01.01.06.03` | `89428b465b7f3d62bd8b65b3155b8aa8e93cd917f59779e40a246b5d89ff8d71` | `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3` |
| `01.01.06.04` | `ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0` | `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3` |

The `.03` preimage contains `G4 P200SET_HOMING_MODE` in each recovery string; the desired payload restores the command separator while applying the `50 ms` waits. Both firmware variants converge on one syntax-checked desired payload.

`installer/runtime/interaction.py` currently sends Moonraker `POST /printer/restart`. That rebuilds Klipper configuration but does not re-import Python modules. `install.sh --yes`, including the child install launched by `installer/runtime/auto_update.py`, skips that prompt and performs no Klipper restart. Managed Python changes therefore require a separate service-process restart contract.

## Goals / Non-Goals

**Goals:**

- Install the validated `100/55 mm/s` X/Y homing behavior and reduced waits on both supported firmware baselines.
- Fail before backup or writes when vendor source does not match a supported stock, prior-managed, or desired hash.
- Preserve exact vendor source bytes and mode across rollback, uninstall, and restore-helper recovery.
- Distinguish a configuration restart from a Klipper service-process restart and verify that the latter produced a new ready process.
- Make an idle unattended auto-update activate managed Python changes before recording its checksum as complete.
- Migrate prior installer-managed X/Y speed `65` to `100` without treating it as user drift, while preserving user-modified values.
- Publish installer version `26.07.26.1` with complete compatibility metadata and tests.

**Non-Goals:**

- Changing `second_homing_speed`, retract distance, retract speed, tolerance, retries, closed-loop trigger values, backoff distance, or Z homing timing.
- Installing timing loggers, macro markers, or permanent diagnostic G-code commands.
- Applying a source patch to an unknown QIDI firmware or an unrecognized `homing.py` hotfix.
- Removing synchronization waits that protect controller-state transitions or backoff completion.
- Automatically moving the toolhead during installer validation.

## Decisions

### 1. Manage `homing.py` as a firmware-scoped source patch

Add `install.source_patches[]` to `installer/package.yaml`. Each entry defines:

- a unique ID;
- a bundle source path under `installer/klipper/`;
- a destination relative to `/home/qidi/klipper`, restricted to `klippy/extras/`;
- firmware variants containing `expected_sha256` and `desired_sha256`.

The bundle contains one production `homing.py` payload with SHA-256 `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3`. Build and installer preflight compile the payload and verify its declared hash. Runtime preflight requires the destination to be a non-symlink regular file and classifies it as stock preimage, prior-managed preimage, already desired, or unsupported drift.

A full payload is selected over textual search-and-replace during installation. The exact desired hash, Python syntax, postflight state, and rollback bytes are deterministic. Firmware-specific preimage hashes prevent replacing vendor hotfixes that reuse a firmware version.

### 2. Record first preimage bytes and source-patch state

Extend schema-version-1 installed state with an optional `source_patches[]` collection. Each record stores the ID, destination, detected firmware, original SHA-256, desired SHA-256, original mode, base64 original bytes, and install classification.

State validation requires unique IDs and destinations, a normalized relative destination under `klippy/extras/`, strict base64 decoding, decoded bytes matching `original_sha256`, mode in `0000..0777`, valid SHA-256 strings, and a supported install classification. Compatibility validation binds the record to the detected firmware and manifest destination, requires the original hash to equal that firmware variant's stock `expected_sha256`, and permits prior-managed migration only when the prior desired hash equals the live hash. A future baseline migration that needs a different original hash must enumerate that provenance in `supported_upgrade_sources.yaml`; matching arbitrary ledger bytes to an arbitrary ledger hash is never sufficient.

Fresh install captures the live stock bytes. Upgrade carries forward the first original preimage from a valid prior ledger when the live file matches that ledger's desired hash. Reinstall at the current desired hash is a no-op and retains the same original preimage. Unknown live hashes fail closed.

Uninstall atomically restores the original bytes and mode only when the live hash still equals the recorded desired hash. A live hash matching the original is a no-op. Any other hash is reported as user drift and is not overwritten.

### 3. Include source destinations in transactions and backup recovery

Extend path-safety checks, free-space accounting, rollback tracking, install/uninstall postflight, and recovery reporting to cover source-patch destinations under `/home/qidi/klipper`.

Installer-created backup archives add a format-versioned external-file manifest and the exact pre-write `homing.py` bytes. Each manifest entry binds one unique source-patch ID and destination to one safe archive member, SHA-256, and mode. Validation rejects duplicate IDs, destinations, or members; non-regular ZIP entries; symlink attributes; missing or extra declared members; hash mismatches; absolute paths; and traversal.

Backup-label provenance separates legacy config-only archives from source-aware archives. Only archives whose parsed installer package version is in an explicit pre-source-patch compatibility set may omit the external manifest. Archives labeled `26.07.26.1` or later, unknown-format archives, and archives whose config state declares source patches require one valid complete external manifest and exact-once declared members; removing current-format external metadata cannot downgrade the archive to config-only. `restore.sh` restores only manifest destinations allowlisted by the current installer using same-directory atomic writes.

A failed transaction restores both `config/` and tracked source files. If rollback cannot restore either root, the recovery sentinel identifies the failed external target and the selected backup remains sufficient for `restore.sh` to restore it. The sentinel is not cleared until config and declared external snapshots match the archive.

### 4. Migrate `65 -> 100` only through prior managed state

Change the X/Y manifest desired `homing_speed` to `100`; retain the firmware stock expectation `50`.

Patch classification accepts `65` as a managed migration only when a valid prior state ledger contains the same target with recorded desired `65`, the live value is `65`, and the prior install result indicates installer management. The new ledger carries forward the prior original expected value, normally `50`, so uninstall restores stock. A ledger-free or mismatched live `65` remains user-modified and is not overwritten.

This avoids a broad `expected: [50, 65]` rule that could claim an operator-selected `65` on a fresh install.

### 5. Use a verified Moonraker service-process restart

Add a process-restart operation separate from the existing configuration restart:

1. Query local Moonraker `GET /printer/info`; require JSON `result.process_id` to be a positive integer and record it, and require `result.state` to be a string.
2. Send `POST /machine/services/restart` with content type `application/json` and body `{"service":"klipper"}`.
3. Poll `GET /printer/info` for a bounded interval while connection and startup states are transient.
4. Succeed only when `result.state == "ready"` and `result.process_id` is a positive integer different from the recorded value.

Missing, malformed, boolean, zero, or unchanged process IDs fail verification. HTTP success without a changed ready process is failure. QIDI/Moonraker response fixtures cover the observed `{"result":{"state":"ready","process_id":N,...}}` shape. `POST /printer/restart` remains available only for config-only changes.

Create `/home/qidi/printer_data/.tltg_optimized_klipper_restart_required` with mode `0600` before changing or restoring managed Python source. The marker is a validated structured record containing the operation, pre-restart process ID, and unique source targets with destination and exact hash expected to be activated. Before any marker-driven restart, the runtime revalidates every live source hash against the marker. Unknown source drift blocks automatic restart, retains the marker, and prints recovery guidance instead of activating unverified Python. Remove the marker only after verified process restart.

Interactive flows prompt specifically for a Klipper service-process restart when the marker exists. `--yes` and the auto-update child perform the required restart without prompting after the existing idle-printer preflight. A required restart failure is fatal to unattended auto-update, so `config/tltg_optimized_auto_update_state.json latest_checksum` is not advanced.

### 6. Keep update completion behind activation

`run_auto_update_check()` checks a pending process-restart marker before checksum fetch, matching-checksum return, or missing-state initialization. When a marker exists, auto-update performs the idle-printer check, validates every marker destination against its expected activation hash, and retries verified process restart. Network checksum failure does not suppress activation of an already-installed verified source payload.

After pending activation is resolved, normal checksum handling continues. A matching checksum may report current and a missing checksum state may initialize only after the marker is cleared. The replacement child installer exits nonzero when a newly required process restart cannot be verified, and `run_auto_update_check()` writes the new checksum only after the child exits successfully.

Interactive install may retain an installed result when the operator declines restart, but it leaves the restart-required marker and prints service-process-specific instructions. Unattended paths cannot decline.

### 7. Bump the package using the version tool

Run:

```bash
python3 scripts/bump_installer_version.py 26.07.26.1
```

This updates `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, and `installer/klipper/tltg-optimized-macros/globals.cfg`. The new compatibility entry includes the existing guarded config tuples; source-patch allowlisting is validated through its own destination and hash ledger.

## Risks / Trade-offs

- **[Vendor hotfix changes `homing.py` without changing firmware version]** → Exact firmware-scoped preimage hashes fail before backup or writes; a new reviewed baseline is required.
- **[Reduced controller transition waits are too short on another machine]** → Retain command order, two-pass tolerance/retry logic, `100 ms` entry waits, `50 ms` recovery waits, and firmware guards; validate repeated cold and heat-soaked homes before release.
- **[Service restart succeeds but Klipper does not return ready]** → Keep the restart-required marker, fail unattended update, preserve the installed source and state ledger, and provide manual recovery guidance.
- **[External source rollback fails]** → Include source bytes in the installer archive, track the file in the rollback journal, record failed paths in the recovery sentinel, and extend `restore.sh` verification across both roots.
- **[Prior optimized `65` is mistaken for user configuration]** → Migrate only when the prior valid patch ledger proves installer ownership.
- **[Full vendor source payload increases maintenance]** → Pin source and desired hashes, compile in tests and bundle smoke tests, and require a baseline update for each changed vendor source.
- **[Auto-update restarts Klipper unexpectedly]** → Existing auto-update idle checks remain mandatory; process restart occurs only when managed Python activation or a retained restart marker requires it.
- **[A retained marker points at drifted Python]** → Revalidate every marker destination and expected hash immediately before restart; unknown drift blocks activation and retains recovery state.
- **[Tampered source ledger or backup redirects restore]** → Strictly bind unique IDs, destinations, members, hashes, modes, decoded bytes, firmware, and manifest allowlists before backup, write, uninstall, or restore.

## Migration Plan

1. Add source-patch manifest/state models, validation, transactional deployment, external backup entries, restore handling, and tests.
2. Add the syntax-checked production `homing.py` payload and both firmware preimage hashes.
3. Add prior-ledger migration for X/Y `65 -> 100` and preserve original uninstall expectations.
4. Add process-restart marker creation, verified service restart, interactive handling, and unattended auto-update activation.
5. Run `python3 scripts/bump_installer_version.py 26.07.26.1`.
6. Update `docs/optimized_vs_stock.md`, `docs/installer_runtime_contract.md`, `docs/installer_restore_helper.md`, and homing path documentation where branch-level invariants change.
7. Run installer known-version checks, installer core tests, G-code path generation/checks when applicable, and bundle smoke tests.
8. Validate fresh install, upgrade, repeated homing, auto-update, uninstall, rollback, and restore on supported firmware fixtures before printer deployment.

Rollback restores the exact pre-install source payload and config values from the installed ledger or selected backup, then requires a verified Klipper process restart before activation is considered complete.

## Open Questions

- Confirm the release validation sample count for cold and heat-soaked repeated X/Y homes; the implementation contract requires first-attempt success tracking and tolerance data but does not prescribe a machine-count threshold.
