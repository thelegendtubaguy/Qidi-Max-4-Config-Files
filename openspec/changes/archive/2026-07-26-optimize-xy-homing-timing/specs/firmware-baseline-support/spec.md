## ADDED Requirements

### Requirement: Firmware-specific Klipper source baselines
Each supported firmware baseline SHALL define the exact accepted stock SHA-256 for `/home/qidi/klipper/klippy/extras/homing.py` and the exact desired optimized payload SHA-256.

#### Scenario: Firmware 01.01.06.03 source baseline
- **WHEN** detected firmware is `01.01.06.03`
- **THEN** the accepted stock `homing.py` SHA-256 is `89428b465b7f3d62bd8b65b3155b8aa8e93cd917f59779e40a246b5d89ff8d71`
- **AND** the desired SHA-256 is `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3`

#### Scenario: Firmware 01.01.06.04 source baseline
- **WHEN** detected firmware is `01.01.06.04`
- **THEN** the accepted stock `homing.py` SHA-256 is `ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0`
- **AND** the desired SHA-256 is `32a8545c440a640b67d1f88f0bbc6ed86b0302c96efda3af8a39ebf22e25fda3`

#### Scenario: Firmware and source baseline disagree
- **WHEN** detected firmware is supported but live `homing.py` matches neither that firmware's stock hash, the desired hash, nor a prior-managed hash
- **THEN** installation fails before backup or writes

### Requirement: Firmware 01.01.06.03 recovery command separation
The optimized desired source SHALL preserve a command separator between the second recovery wait and `SET_HOMING_MODE STEPPER=y VALUE=2` for firmware `01.01.06.03`.

#### Scenario: Shared desired payload is compiled
- **WHEN** the `.03` stock source is transformed to the desired payload
- **THEN** `G4 P50` and `SET_HOMING_MODE STEPPER=y VALUE=2` are separate G-code lines in both recovery scripts
- **AND** the resulting Python source compiles
- **AND** its SHA-256 equals the shared desired hash

### Requirement: Source-baseline documentation and validation
Firmware support documentation and tests SHALL record the vendor source hashes, desired payload hash, syntax validation, and fail-closed behavior.

#### Scenario: Source baseline validation runs
- **WHEN** installer validation is executed
- **THEN** tests cover `.03` stock application, `.04` stock application, already-desired no-op, and unknown-source rejection
- **AND** bundle validation compiles the desired Python payload and verifies its SHA-256
