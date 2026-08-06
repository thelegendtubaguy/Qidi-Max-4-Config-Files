# installer-lifecycle Specification

## Purpose

The installer admits supported QIDI firmware, changes only validated installer-owned state, and keeps install, update, restore, and uninstall recoverable.

## Requirements

### Requirement: Firmware-gated ownership
The installer SHALL select one complete baseline for the detected supported firmware and change only recognized stock state or state proven to be installer-owned.

#### Scenario: Supported firmware selects one baseline
- **WHEN** installation starts on firmware declared in `installer/package.yaml`
- **THEN** exactly one complete firmware-scoped variant is selected for every required target
- **AND** configuration and managed-source expectations come from that baseline

#### Scenario: Invalid admission fails before unsafe work
- **WHEN** firmware is unreadable or unsupported
- **THEN** the operation fails before reading installed state, creating a backup, or writing live files
- **AND** incomplete or ambiguous baseline data or an unclassifiable managed target fails before backup or mutation
- **AND** unowned live content is preserved

#### Scenario: Legacy installation is migrated conservatively
- **WHEN** legacy optimized markers exist without valid installer state
- **THEN** migration requires an idle printer and a validated stock snapshot for the detected firmware
- **AND** vendor-managed, user-specific, and sensitive configuration remains preserved

### Requirement: Recoverable configuration lifecycle
Install, reinstall, restore, and uninstall SHALL execute as serialized, idle-printer transactions with validated inputs, recoverable preimages, atomic committed state, and drift-safe reversal.

#### Scenario: Mutation begins only after complete preflight
- **WHEN** a mutating operation starts
- **THEN** it validates printer state, prior state, targets, paths, bundle contents, and required storage before backup or writes
- **AND** printing, paused, unknown, malformed, concurrent, or recovery-blocked state fails closed

#### Scenario: Installation commits atomically
- **WHEN** installation changes validated targets
- **THEN** recoverable preimages exist before the first write
- **AND** the managed tree and guarded patches converge to the selected release
- **AND** installed state is committed only after postflight verifies the resulting files and ownership ledger
- **AND** failure restores preimages or records a recovery blocker when compensation cannot complete

#### Scenario: Uninstall respects ownership
- **WHEN** uninstall processes a valid ownership ledger
- **THEN** only unchanged installer-owned state is reverted
- **AND** user-modified state is preserved and reported
- **AND** installed state is removed only after successful postflight

#### Scenario: Restore reconstructs the archived runtime
- **WHEN** restore receives explicit confirmation for a validated installer archive
- **THEN** archived configuration and eligible external members are staged before replacing live runtime state
- **AND** partial failure restores the pre-restore state
- **AND** every restored root is verified before success and recovery remains blocked until incomplete compensation is resolved

#### Scenario: Non-mutating and interrupted runs remain safe
- **WHEN** dry-run, help, demo, alternate reporting, or interruption is selected
- **THEN** safety decisions remain equivalent to the normal flow
- **AND** no unapproved backup, pruning, or live mutation occurs
- **AND** interruption prevents later actions and exits without a traceback

### Requirement: Managed Klipper source activation
The installer SHALL deploy firmware-scoped Klipper source only from validated provenance and consider a source change active only after a replacement Klipper process is verified ready.

#### Scenario: Managed source deployment is provenance-bound
- **WHEN** a managed source target matches selected stock content or valid prior-managed state
- **THEN** its original bytes and metadata are retained before atomic deployment
- **AND** the payload is verified against the selected release before installed state commits
- **AND** unknown, escaped, symlinked, tampered, or cross-firmware provenance fails before mutation

#### Scenario: Source restoration preserves drift
- **WHEN** rollback, restore, or uninstall processes managed source
- **THEN** unchanged installer-owned content may return to its retained preimage
- **AND** content already at the preimage is unchanged
- **AND** any other live content is preserved and reported as drift

#### Scenario: Activation requires a replacement process
- **WHEN** managed source changes or pending source activation exists
- **THEN** activation remains pending until Klipper becomes ready under a different valid process identity
- **AND** failed or drifted activation remains pending and blocks unsafe continuation
- **AND** every installer entrypoint resolves pending activation before further release work

### Requirement: Non-owning QIDI Box reconciliation
The installer SHALL keep an available QIDI Box usable without claiming ownership of vendor saved variables or silently replacing existing non-empty tool mappings.

#### Scenario: Optional Box state is reconciled conservatively
- **WHEN** the Box stack is available
- **THEN** explicit interactive or `--yes` policy may enable it
- **AND** missing active mappings are created as identity mappings
- **AND** existing non-empty mappings change only after explicit interactive approval

#### Scenario: Automatic reconciliation preserves vendor state
- **WHEN** noninteractive install or update observes Box topology while the printer is idle
- **THEN** missing active mappings are created and existing non-empty mappings are preserved
- **AND** busy or unknown printer state causes no reconciliation writes
- **AND** saved-variable changes are excluded from installer ownership and uninstall

### Requirement: Opt-in recoverable host optimization
The installer SHALL apply host OS optimizations only under explicit persisted policy, preserve recoverable preimages, and keep host-operation failures separate from a verified printer-configuration result.

#### Scenario: Enabled policy reconciles only recognized host state
- **WHEN** system optimizations are enabled
- **THEN** each declared operation validates its live preconditions before mutation
- **AND** installer-owned drift is reconciled without replacing first restore preimages
- **AND** unowned, unknown, or user-modified state is preserved and reported
- **AND** operation failure rolls back journaled host work without deleting a verified configuration install

#### Scenario: Multi-plate 3MF metadata follows the selected plate
- **WHEN** enabled Moonraker optimization reads a `.gcode.3mf` archive
- **THEN** G-code, metadata, and thumbnail selection use its valid selected plate index
- **AND** missing or invalid plate metadata falls back to plate 1

#### Scenario: Uninstall follows the operator's host-state decision
- **WHEN** uninstall finds host restore preimages
- **THEN** accepted restoration reverts only unchanged installer-owned targets
- **AND** declined restoration or explicit keep policy leaves current host state unchanged

#### Scenario: Host reboot is deferred until safe
- **WHEN** an applied operation requires a host reboot
- **THEN** the requirement is persisted without embedding executable commands
- **AND** reboot is scheduled only after successful transaction completion, explicit authorization, and a fresh idle-printer check
- **AND** later execution clears the requirement only after post-boot verification succeeds
- **AND** dry-run, active, or unknown printer state performs no reboot

### Requirement: Safe unattended updates
Automatic updates SHALL use the same admission, ownership, recovery, activation, and persisted host-policy rules as direct installation while advancing release state only after successful activation.

#### Scenario: Release installation is idle and integrity checked
- **WHEN** a new release checksum is observed
- **THEN** update proceeds only while the printer is idle and after archive integrity and path safety are verified
- **AND** the bundled noninteractive installer performs the change
- **AND** release state advances only after install and required Klipper activation succeed
- **AND** any host reboot is considered only after that state is durable and printer idleness is revalidated

#### Scenario: No-op and failure paths preserve release state
- **WHEN** release discovery fails, checksum state is first initialized, activation remains pending, or child installation fails
- **THEN** no release is falsely recorded as installed and no reboot is scheduled
- **AND** an already-current release may reconcile enabled installer-owned policy while idle

#### Scenario: Update plumbing is ownership-bound
- **WHEN** automatic updates are enabled, repaired, disabled, or removed during uninstall
- **THEN** only installer-owned scheduling and checksum state is created, repaired, or removed
- **AND** plumbing failure is reported separately from successful configuration or uninstall work
