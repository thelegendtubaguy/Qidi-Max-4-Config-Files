## ADDED Requirements

### Requirement: Default optimized preferences preserve operator values
The installer SHALL initialize absent optimized saved-variable preferences to release defaults without replacing operator-controlled values.

#### Scenario: Absent retention preference receives the installed default
- **WHEN** install or update finds no `tltg_keep_loaded_between_prints` entry in Klipper saved variables
- **THEN** it atomically saves value `1` within the recoverable install transaction
- **AND** the preference remains outside the installed-state ownership ledger

#### Scenario: Existing retention preference is preserved
- **WHEN** install or update finds an existing `tltg_keep_loaded_between_prints` value, including `0`
- **THEN** it leaves that value unchanged
- **AND** uninstall does not remove or reset it
