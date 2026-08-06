## 1. Runtime Motion

- [x] 1.1 Add shared optimized rear-bed scrape motion with the X380 cable-chain orientation path, shared chute approach speed, and Y392–Y395 scrape range.
- [x] 1.2 Route fresh-Box and external-spool scrape paths through the shared implementation without changing retained-filament or vendor cleanup ownership.
- [x] 1.3 Set Z-home travel to 750 mm/s and independent X/Y randomization to ±10 mm.

## 2. Specifications and Contracts

- [x] 2.1 Update the main optimized-printer-behavior specification with the rear-scrape and Z-home requirements.
- [x] 2.2 Update the start-print path contract and regenerate its Markdown and Mermaid views.
- [x] 2.3 Extend optimized macro contract tests for shared scrape motion, feed rates, coordinates, and Z-home globals.

## 3. Installer Version

- [x] 3.1 Bump installer/runtime/upgrade metadata to `26.08.06.1` and update version assertions.
- [x] 3.2 Add the `26.08.06.1` changelog entry.

## 4. Validation

- [x] 4.1 Format optimized Klipper configuration and run focused macro/path/version checks.
- [x] 4.2 Run installer core tests and strict OpenSpec validation.
- [x] 4.3 Review the final diff for stock-config isolation, generated artifacts, and unintended printer-deployment changes.
