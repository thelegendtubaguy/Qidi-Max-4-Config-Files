## Why

The rear-bed scrape approach can leave the toolhead cable-chain pivot rotated toward the rear enclosure, allowing the pivot hardware to strike the printer. The Z-home center travel and randomized probe location also need to adopt the validated faster travel and wider wear-distribution range.

## What Changes

- Add a deterministic rear-scrape approach that moves 50 mm forward from the chute, traverses to X380 and back to X188 at 400 mm/s to orient the cable chain, and moves rearward to Y392 at the chute final-approach speed.
- Move the rear-bed scrape range from Y395–Y397 to Y392–Y395.
- Share one optimized scrape implementation between fresh-Box and external-spool start paths.
- Increase Z-home XY travel to 750 mm/s and independently randomize X and Y by up to 10 mm around bed center.
- Start optimized installer version `26.08.06.1`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: Change rear-bed scrape clearance motion, scrape coordinates, and optimized Z-homing travel behavior.

## Impact

- Optimized runtime macros under `installer/klipper/tltg-optimized-macros/`.
- Start-print path contract and generated views under `openspec/contracts/gcode-paths/`.
- Optimized printer behavior specification and macro contract tests.
- Installer package, upgrade-source metadata, changelog, and version assertions for `26.08.06.1`.
