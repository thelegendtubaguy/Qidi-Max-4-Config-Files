## ADDED Requirements

### Requirement: Firmware-scoped source-patch manifest
The installer SHALL describe managed vendor Python source with a unique ID, bundle source, destination relative to `/home/qidi/klipper`, and firmware variants containing exact expected and desired SHA-256 hashes.

#### Scenario: Valid source-patch manifest
- **WHEN** manifest validation reads a source-patch entry
- **THEN** its bundle source is a non-symlink regular file under `installer/klipper/`
- **AND** its destination is a relative non-traversing path under `klippy/extras/`
- **AND** every firmware variant references a supported firmware and valid SHA-256 values

#### Scenario: Invalid source destination
- **WHEN** a source-patch destination is absolute, traverses with `..`, or is outside `klippy/extras/`
- **THEN** manifest validation fails

### Requirement: Guarded source preflight
Installation SHALL fail before backup or writes unless each active source destination matches the selected firmware stock hash, the selected desired hash, or a desired hash proven by a valid prior source-patch ledger.

#### Scenario: Stock source is accepted
- **WHEN** the destination hash equals the selected firmware `expected_sha256`
- **THEN** source preflight classifies the patch as applicable

#### Scenario: Desired source is accepted
- **WHEN** the destination hash equals the selected firmware `desired_sha256`
- **THEN** source preflight classifies the patch as an installed no-op

#### Scenario: Prior managed source is accepted for upgrade
- **WHEN** a valid prior ledger records the destination's live hash as its installed desired hash
- **THEN** source preflight permits migration to the new desired payload
- **AND** preserves the first original preimage

#### Scenario: Unknown source drift fails closed
- **WHEN** the destination hash matches none of the accepted guarded hashes
- **THEN** installation fails before backup creation or runtime writes
- **AND** reports the source-patch target as drift

#### Scenario: Symlink source target is rejected
- **WHEN** the destination or any managed destination component is a symlink
- **THEN** preflight fails before backup or writes

### Requirement: Atomic source deployment and verification
The installer SHALL deploy an applicable source payload with a same-directory atomic replacement, preserve the destination mode, and verify the final SHA-256 before committing installed state.

#### Scenario: Applicable source patch is installed
- **WHEN** the destination matches the stock or prior-managed preimage
- **THEN** the installer records the preimage before writing
- **AND** atomically writes the desired payload
- **AND** verifies the declared desired SHA-256

#### Scenario: Source postflight fails
- **WHEN** the installed destination does not match the declared desired SHA-256
- **THEN** installation fails
- **AND** transaction rollback restores the tracked preimage

### Requirement: Source-patch installed-state ledger
Schema-version-1 installed state SHALL optionally record each source patch's destination, firmware, original SHA-256, desired SHA-256, original bytes, original mode, and install classification.

#### Scenario: Fresh source patch state is written
- **WHEN** a stock source file is patched successfully
- **THEN** the state ledger stores the exact stock preimage and installed desired hash

#### Scenario: Existing state without source patches is loaded
- **WHEN** a valid prior schema-version-1 ledger has no `source_patches` field
- **THEN** state validation treats the field as empty

#### Scenario: Managed source upgrade carries the original preimage
- **WHEN** a prior source ledger is upgraded to a new desired payload
- **THEN** the new ledger retains the prior original bytes, mode, and original SHA-256
- **AND** records the new desired SHA-256

#### Scenario: Source ledger is validated before trust
- **WHEN** installed state contains source-patch records
- **THEN** IDs and destinations are unique
- **AND** each destination is normalized under `klippy/extras/`
- **AND** original bytes decode with strict base64 and match `original_sha256`
- **AND** `original_sha256` equals the ledger firmware variant's manifest `expected_sha256` or an explicitly enumerated supported-upgrade baseline hash
- **AND** mode is an integer from `0000` through `0777`
- **AND** firmware, destination, and desired hash are compatible with the active or supported-upgrade manifest

#### Scenario: Self-consistent arbitrary original payload is rejected
- **WHEN** ledger original bytes match the ledger `original_sha256` but that hash is not an allowed firmware stock or supported-upgrade baseline hash
- **THEN** install and uninstall reject the ledger before backup or writes

#### Scenario: Source ledger is malformed or tampered
- **WHEN** any source record has duplicate identity, invalid path, invalid hash, invalid mode, malformed base64, decoded-byte hash mismatch, or incompatible manifest binding
- **THEN** install and uninstall fail before backup or writes

### Requirement: External source backup and transactional rollback
Installer backup and rollback SHALL cover every source-patch destination in addition to the `config/` tree.

#### Scenario: Backup precedes source write
- **WHEN** installation or uninstall will change managed vendor source
- **THEN** the backup archive contains a versioned whitelisted external-file manifest and the exact pre-write source bytes and mode before the first source write
- **AND** every manifest entry uniquely binds its source-patch ID and destination to one regular archive member, SHA-256, and mode

#### Scenario: External backup manifest is invalid
- **WHEN** an archive has duplicate IDs, destinations, or members; undeclared or missing members; non-regular or symlink entries; invalid modes or hashes; hash mismatch; absolute paths; traversal; or destinations outside the current installer allowlist
- **THEN** backup restore validation rejects it before live writes

#### Scenario: Failure after source write
- **WHEN** installation or uninstall fails after changing source
- **THEN** rollback restores the tracked source bytes and mode atomically
- **AND** verifies the restored hash

#### Scenario: Rollback cannot restore source
- **WHEN** transactional rollback cannot restore a source target
- **THEN** the recovery sentinel records the failed target and backup archive
- **AND** blocks subsequent install or uninstall until recovery is verified

### Requirement: Guarded source uninstall
Uninstall SHALL restore the recorded original source only when the live destination remains at the recorded desired hash or already matches the original hash.

#### Scenario: Installed source is unchanged
- **WHEN** the live hash equals the ledger desired hash
- **THEN** uninstall atomically restores the recorded original bytes and mode

#### Scenario: Original source is already restored
- **WHEN** the live hash equals the ledger original hash
- **THEN** uninstall treats source restoration as a no-op

#### Scenario: Installed source has drifted
- **WHEN** the live hash matches neither the desired nor original ledger hash
- **THEN** uninstall preserves the live file
- **AND** reports source drift instead of overwriting it

### Requirement: Restore helper supports managed source snapshots
`restore.sh` SHALL restore and verify whitelisted external source entries from installer archives while remaining compatible with older config-only archives.

#### Scenario: Archive includes managed source
- **WHEN** a selected backup contains a valid external-file manifest and source snapshot
- **THEN** restore stages and validates the source bytes before writes
- **AND** restores the config tree and source file
- **AND** verifies both roots before success

#### Scenario: Proven legacy archive is config-only
- **WHEN** a valid selected backup's parsed package-version label is in the explicit pre-source-patch compatibility set
- **AND** the archive contains only a `config/` snapshot
- **THEN** restore retains existing config-only behavior
- **AND** does not infer or write an external destination

#### Scenario: Source-aware archive is missing external metadata
- **WHEN** an archive is labeled `26.07.26.1` or later, has unknown format provenance, or contains installed state declaring source patches
- **AND** its external manifest or any declared external member is absent
- **THEN** restore rejects the archive before live writes
- **AND** does not reinterpret it as a legacy config-only archive

#### Scenario: Archive external path is not allowlisted
- **WHEN** archive metadata names an absolute, traversing, or unsupported external target
- **THEN** restore rejects the archive before live writes
