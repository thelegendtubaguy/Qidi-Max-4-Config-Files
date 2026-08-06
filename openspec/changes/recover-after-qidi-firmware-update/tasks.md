## 1. Auto-update recovery

- [x] 1.1 Persist durable auto-update enrollment after successful systemd setup and remove it during disable/uninstall.
- [x] 1.2 Detect missing or critically drifted enrolled installations before returning `already-current` or initializing missing checksum state.
- [x] 1.3 Route enrolled recovery through the checksum-verified current installer and preserve idle, failure, activation, and checksum controls.

## 2. Evidence and tests

- [x] 2.1 Add focused lifecycle tests for enrollment, unregistered checksum handling, same-release recovery, and uninstall cleanup.
- [x] 2.2 Add integration coverage for recovery after firmware-style config cleanup and managed-source replacement.
- [x] 2.3 Update `openspec/observations/qidi-platform.md` and the main installer lifecycle spec.

## 3. Validation

- [x] 3.1 Run the focused lifecycle tests and full installer core test suite.
- [x] 3.2 Run strict OpenSpec validation, installer known-version checks, and authored-file whitespace validation.
