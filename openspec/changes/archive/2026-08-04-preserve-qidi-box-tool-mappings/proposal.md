## Why

QIDI Box touchscreen selection and filament-runout handling can silently rewrite persistent `value_t0` through `value_t15` tool-to-slot mappings, causing a later direct OrcaSlicer print to use a different physical slot than its tool index implies. The optimized print lifecycle needs to preserve mappings selected for the active print while restoring a predictable identity baseline after successful completion.

## What Changes

- Create missing active QIDI Box tool mappings as identity mappings during installation and topology reconciliation without prompting.
- During an interactive installation or manual update, offer to reset existing non-identity mappings after creating any missing mappings; preserve them when declined.
- Preserve existing non-identity mappings during every noninteractive installation or update, including automatic updates.
- At optimized print start, repair only missing or empty active mappings and preserve all non-empty mappings selected by the touchscreen or runout handling.
- After successful end-of-print filament retention or unloading, begin nozzle cooldown before resetting non-identity tool mappings to identity mappings.
- Preserve mappings on cancellation, print error, or interrupted end sequences where filament state may be unresolved.
- Provide an idle-only console macro for manually resetting tool mappings from Fluidd or the Klipper console.
- Release the behavior as package version `26.08.05.1` with changelog entries for tool-mapping normalization and the GPLv3/third-party licensing files already included in the release bundle.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: Distinguish interactive mapping alignment from missing-mapping repair and noninteractive update reconciliation.
- `optimized-printer-behavior`: Define start-time mapping preservation, successful-end identity reset ordering, interrupted-print behavior, and manual console reset behavior.

## Impact

- Installer mapping reconciliation under `installer/runtime/box_enablement.py` and its install-mode integration.
- Optimized QIDI Box filament macros under `installer/klipper/tltg-optimized-macros/`, without new tool-mapping commands in slicer G-code.
- Installer and macro validation covering interactive and noninteractive installs, automatic updates, normal completion, cancellation, and manual reset.
- Package metadata in `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, and optimized globals, plus `CHANGELOG.md` release notes.
