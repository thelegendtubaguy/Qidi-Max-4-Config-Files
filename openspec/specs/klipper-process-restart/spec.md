# klipper-process-restart Specification

## Purpose

Klipper process restart handling controls activation of managed Python source across interactive, unattended, restore, uninstall, and auto-update flows.

## Requirements

### Requirement: Restart scope reflects changed artifacts
The installer SHALL require a Klipper service-process restart when managed Python source is installed, restored, rolled back, or remains pending activation; configuration-only changes MAY use Klipper's configuration restart.

#### Scenario: Python source changes
- **WHEN** a managed source destination is changed or restored
- **THEN** the runtime records that a Klipper service-process restart is required
- **AND** does not treat `POST /printer/restart` as sufficient activation

#### Scenario: Config-only install
- **WHEN** no managed Python source changed and no process-restart marker exists
- **THEN** the existing configuration-restart path remains available

### Requirement: Process restart is verified
A required process restart SHALL use Moonraker's machine-service restart API and SHALL succeed only after Klipper reports ready under a different process ID.

#### Scenario: Verified process restart
- **WHEN** `GET /printer/info` returns JSON `result.process_id` as a positive integer and `result.state` as a string
- **AND** the runtime records that process ID
- **AND** sends `POST /machine/services/restart` with content type `application/json` and JSON `{"service":"klipper"}`
- **AND** bounded polling observes `result.state: ready` with a different positive-integer `result.process_id`
- **THEN** the restart is successful

#### Scenario: Printer info process identity is invalid
- **WHEN** `result.process_id` is missing, malformed, boolean, zero, or negative before or after restart
- **THEN** restart verification fails
- **AND** the pending marker remains

#### Scenario: HTTP success without process replacement
- **WHEN** the service API returns success but the observed process ID does not change
- **THEN** restart verification fails

#### Scenario: Klipper does not return ready
- **WHEN** Klipper remains unavailable, startup, error, or shutdown until the verification deadline
- **THEN** restart verification fails with manual service-restart guidance

### Requirement: Pending process restart survives interrupted activation
The runtime SHALL create `/home/qidi/printer_data/.tltg_optimized_klipper_restart_required` with mode `0600` before changing managed Python source and SHALL remove it only after verified process restart. The marker SHALL bind each pending destination to the exact hash intended for activation.

#### Scenario: Source write requires activation
- **WHEN** installation writes a new managed Python payload
- **THEN** the pending marker exists before the source write

#### Scenario: Verified restart clears activation marker
- **WHEN** a new ready Klipper process is verified
- **THEN** the pending marker is atomically removed

#### Scenario: Restart fails or is declined
- **WHEN** a required restart fails or an interactive operator declines it
- **THEN** the pending marker remains
- **AND** a subsequent install, uninstall, restore, or auto-update retries the process restart

#### Scenario: Pending source hash is valid
- **WHEN** a retained marker is processed
- **AND** every live destination hash equals the marker's expected activation hash
- **THEN** automatic process restart may proceed

#### Scenario: Pending source hash has drifted
- **WHEN** any live destination hash differs from the marker's expected activation hash
- **THEN** automatic process restart is blocked
- **AND** the marker remains
- **AND** recovery guidance identifies the drifted target

### Requirement: Interactive installer uses service-process restart when required
Interactive install and uninstall SHALL identify the process-restart scope and verify an accepted service restart.

#### Scenario: Operator accepts required process restart
- **WHEN** managed Python activation is pending and the operator accepts the process-restart prompt
- **THEN** the runtime performs and verifies a service-process restart

#### Scenario: Operator declines required process restart
- **WHEN** the operator declines the process-restart prompt
- **THEN** installed files remain in their verified state
- **AND** the pending marker and service-restart instructions remain

### Requirement: Noninteractive source activation is mandatory
A `--yes` install and an auto-update child SHALL perform a required Klipper service-process restart without prompting after idle-printer preflight.

#### Scenario: Auto-update changes managed Python
- **WHEN** auto-update installs a bundle that changes managed Python source
- **THEN** the child installer performs and verifies the service-process restart before returning success

#### Scenario: Auto-update restart verification fails
- **WHEN** required service restart cannot be verified
- **THEN** the child installer returns nonzero
- **AND** auto-update does not advance `latest_checksum`
- **AND** the pending marker remains for retry

#### Scenario: Auto-update retries pending activation before checksum decisions
- **WHEN** a timer run finds a pending marker
- **THEN** it performs idle-printer and marker-hash validation before checksum fetch, matching-checksum return, or missing-state initialization
- **AND** retries and verifies the service-process restart

#### Scenario: Matching checksum has pending activation
- **WHEN** `latest_checksum` already matches the fetched checksum and a valid pending marker exists
- **THEN** auto-update resolves and verifies process activation before reporting already current

#### Scenario: Missing checksum state has pending activation
- **WHEN** checksum state is absent and a valid pending marker exists
- **THEN** auto-update resolves and verifies process activation before initializing checksum state

#### Scenario: Checksum fetch fails with pending activation
- **WHEN** checksum fetch is unavailable and a valid pending marker exists
- **THEN** auto-update still attempts local process activation
- **AND** leaves checksum state unchanged

### Requirement: Restore and uninstall activate restored Python
Restore helper and uninstall flows SHALL require verified process restart after restoring managed Python source.

#### Scenario: Uninstall restores vendor source
- **WHEN** uninstall restores the recorded vendor `homing.py` preimage
- **THEN** it records pending activation and offers or performs a verified service-process restart according to interaction mode

#### Scenario: Restore helper restores vendor source
- **WHEN** `restore.sh` writes a managed Python source snapshot
- **THEN** it does not report full activation until the Klipper service-process restart is verified or explicit manual instructions and the pending marker remain
