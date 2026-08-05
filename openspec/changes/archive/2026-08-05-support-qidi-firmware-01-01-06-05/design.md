## Context

The installer currently admits firmware `01.01.06.03` and `01.01.06.04`, requires complete firmware coverage for every guarded configuration and managed-source patch, and restores a full sanitized snapshot selected by detected firmware. Firmware `01.01.06.05` is represented by `Qidi-Max4-Defaults` commit `c75c0b662d1d4fd2a7dd19e49843b91e6544a1ed` and tag `qidi-max4-firmware-01.01.06.05-b1826d1aed274c7233b4a23a3a3e5c0b4e9655c5d03188a8b6f3561f0d3f2de7`.

Against the package-owned `.04` baseline, `.05` changes `config/printer.cfg` by setting the hotend fan `tachometer_poll_interval` to `0.0005` and commenting out the polar-cooler and beeper `smart_output_pin` sections. The remaining guarded configuration targets retain `.04` values. The `.05` package also contains the package-owned `M4031` Z-positioning sequence absent from the repository's older `.04` restore snapshot, so the `.05` snapshot cannot be derived by editing only three `printer.cfg` lines.

Firmware-managed Python and native modules add display, metadata, virtual-SD thumbnail, log retention, Box loading/retry, slot mapping, external-spool, and qidiclient behavior. Their public integration surfaces used by optimized macros remain compatible. The installer manages only `homing.py`; `.05` ships the sync-reset stock preimage already supported for one `.04` variant.

Constraints include preserving `.03` and `.04`, keeping package version `26.08.05.1`, excluding sensitive/runtime-owned files, leaving `config/fluidd.cfg` untouched, retaining QIDI's `.05` cooler policy, and avoiding slicer or start-path changes without evidence that they are required.

## Goals / Non-Goals

**Goals:**

- Admit, install, restore, uninstall, and upgrade optimized configuration on `.05` using exact firmware-scoped provenance.
- Preserve existing optimized desired values across `.03`, `.04`, and `.05`.
- Restore a complete `.05` stock configuration without copying sensitive or generated state.
- Reuse the validated sync-reset optimized homing payload for the exact `.05` stock hash.
- Preserve QIDI's direct-output-pin polar-cooler pause/resume behavior on `.05`.
- Record externally observed `.05` platform, Box, and qidiclient behavior with evidence qualifiers.

**Non-Goals:**

- Rebuilding, replacing, or patching qidiclient or firmware-managed compiled Klipper modules.
- Recreating `[smart_output_pin polar_cooler]` or `[smart_output_pin beeper]`.
- Adding explicit P4 pause/resume commands or changing start/end cooling policy.
- Changing OrcaSlicer, QIDI Studio, or the start-print path contract.
- Changing installer runtime schemas or snapshot-selection algorithms.
- Renaming package version `26.08.05.1`.

## Decisions

### Pin the `.05` release object rather than the defaults working tree

Snapshot and source evidence will use commit `c75c0b662d1d4fd2a7dd19e49843b91e6544a1ed` and its firmware tag. The sibling repository's current `main` intentionally omits firmware-wide line-ending churn and does not itself identify the complete `.05` tree.

Alternative: copy the sibling working tree and manually apply known deltas. Rejected because it would omit package-owned release content and weaken provenance.

### Extend `.04` configuration variants to `.05`

Every `.04` set-option and delete-section variant will also list `.05`; expected and desired values are unchanged for those 28 targets. `.03` variants remain separate where stock differs or sections do not exist.

Alternative: duplicate one `.05` variant per target. Rejected because identical entries add drift without expressing a distinct baseline.

### Add a discrete `.05` managed-source variant

The `qidi_homing` entry will bind `.05` stock SHA-256 `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761` to `klipper/qidi/homing-sync-reset.py`, desired SHA-256 `09a57808075b7022ad65619f5a23deeec80c5d682a43e8ee101f8d62c984f33a`. The `.04` standard preimage is not accepted for `.05`.

Alternative: treat `.05` as an alias of `.04` source provenance. Rejected because installed-state and rollback trust are firmware-scoped.

### Ship a complete canonical `.05` snapshot

The snapshot will contain the 26 release configuration files under `installer/stock/qidi-max4-defaults/firmwares/01.01.06.05/config/`, normalized to repository line endings, with approved comment translation/redaction conventions preserved. `MCU_ID.cfg`, `box.cfg`, `fluidd.cfg`, and `saved_variables.cfg` remain excluded.

Alternative: inherit from the `.04` snapshot at runtime. Rejected because snapshot inheritance is not part of the restore contract and the existing `.04` snapshot predates package-owned release content.

### Follow QIDI's `.05` polar-cooler state ownership

The optimized tree will continue to use direct `M106 P4` control at existing start, helper, end, and cancellation points. Pause and resume will emit no new P4 command. Without another vendor/client command, the current P4 state therefore persists across pause/resume on `.05`.

Alternative: recreate the smart object or emulate `.04` by forcing P4 off on pause and enabled on resume. Rejected by operator decision and because it would create new optimized behavior rather than firmware compatibility.

### Leave qidiclient static-GIF replacement unchanged

The `.05` upgrade restored active GIF bytes, while the new binary removed readable GIF decoder identifiers and most literal GIF paths. Runtime rendering remains unresolved. Existing archive replacement is path-compatible and non-blocking, so this change neither removes nor firmware-gates it.

Alternative: disable static-GIF replacement for `.05`. Rejected without runtime evidence and because it would expand installer runtime behavior beyond firmware admission.

### Keep package version `26.08.05.1`

The version already identifies unreleased work and was not fully merged or released. Manifest and supported-upgrade metadata will be amended in place, and tests will continue to require consistency across the package version, optimized globals, and known-version metadata.

## Risks / Trade-offs

- [The actual `.05` configuration uses CRLF while snapshots use LF] → Installer config reads normalize line endings, section hashes are normalized, and tests will exercise `.05` stock values without committing firmware-wide byte churn.
- [A partial snapshot omits package-owned release behavior] → Build from the pinned full release tree and verify the `M4031` Z-positioning sequence plus the three `.05` `printer.cfg` deltas.
- [Cross-firmware homing provenance is accepted accidentally] → Add `.05` lifecycle tests for the sync-reset hash and explicit rejection of unknown or `.04`-standard stock content.
- [Removed smart-pin behavior is silently recreated] → Add static assertions that optimized pause/resume do not emit P4 control and do not define the removed smart object.
- [Static GIF replacement is obsolete or affects a changed renderer] → Keep current behavior for this change and retain the captured `.05` qidiclient evidence as an unresolved observation.
- [Sensitive or generated printer state enters the snapshot] → Assert the four excluded files are absent and scan changed content before commit.

## Migration Plan

1. Add `.05` admission, shared configuration coverage, discrete homing provenance, and supported-upgrade metadata while retaining version `26.08.05.1`.
2. Add the sanitized full `.05` snapshot and update bundle snapshot validation.
3. Extend fixtures and lifecycle tests across all three firmware versions.
4. Update desired specifications and external observations.
5. Run installer manifest checks, core tests, bundle smoke validation, OpenSpec validation, path checks, and sensitive-content review.
6. After implementation validation, run a printer dry-run only when explicitly requested; the installer must report `.05` and classify all targets before any live installation.

Rollback uses the existing transactional backup, source preimage, snapshot restore, and recovery-sentinel mechanisms. No schema migration is introduced.

## Open Questions

None. Runtime GIF animation behavior and any qidiclient-side P4 intervention remain external observations and do not block installer compatibility.
