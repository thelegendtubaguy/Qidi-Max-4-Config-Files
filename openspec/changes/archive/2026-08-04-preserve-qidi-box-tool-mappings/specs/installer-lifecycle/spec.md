## MODIFIED Requirements

### Requirement: QIDI Box saved-variable reconciliation
The installer SHALL keep QIDI Box enablement and logical tool mappings usable without claiming ownership of vendor-managed saved variables or silently normalizing non-empty mappings during noninteractive operation.

#### Scenario: Box support is optional
- **WHEN** `config/box.cfg` or `[box_extras]` is absent
- **THEN** Box reconciliation is skipped
- **AND** installation preflight and optimized non-Box behavior remain available

#### Scenario: Detected Box may be enabled
- **WHEN** `[box_extras]` exists, `box_count > 0`, and `enable_box = 0`
- **THEN** interactive install offers to set `enable_box = 1`
- **AND** `--yes` applies the enablement without prompting
- **AND** a declined prompt preserves the disabled value

#### Scenario: Required tool mappings are present
- **WHEN** `box_count` requires logical tools `0` through `min(box_count * 4, 16) - 1`
- **THEN** missing or empty `value_tN` entries are written as `'slotN'` without a separate prompt
- **AND** existing non-empty mappings remain available for alignment evaluation

#### Scenario: Interactive operation offers mapping alignment
- **WHEN** an interactive installation or manual update finds a non-empty `value_tN` that differs from `'slotN'`
- **THEN** the mismatches are presented to the operator
- **AND** accepted alignment rewrites those mappings to identity values
- **AND** declined alignment preserves them

#### Scenario: Noninteractive operation preserves mapping mismatches
- **WHEN** installation or update runs without an interactive input stream, including automatic update
- **THEN** missing or empty active mappings are created
- **AND** every existing non-empty mapping is preserved without an alignment prompt or implicit approval

#### Scenario: Vendor saved variables remain outside installer ownership
- **WHEN** enablement or mapping reconciliation changes `config/saved_variables.cfg`
- **THEN** those values are not recorded in `config/tltg_optimized_state.yaml`
- **AND** uninstall does not revert them

#### Scenario: Box-count changes reconcile only while idle
- **WHEN** auto-update observes a changed `box_count` while Klipper is reachable and idle
- **THEN** missing or empty mappings are added
- **AND** existing non-empty mappings are preserved
- **AND** `config/tltg_optimized_runtime_state.json last_observed_box_count` is updated
- **AND** busy or unknown printer state causes reconciliation to skip without writes
