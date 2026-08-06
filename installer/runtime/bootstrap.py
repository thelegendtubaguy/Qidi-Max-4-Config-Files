from __future__ import annotations

import sys
from pathlib import Path


class BootstrapError(RuntimeError):
    pass



def bundle_root_from_file(file_path: str) -> Path:
    return Path(file_path).resolve().parents[2]



def prepare_sys_path(bundle_root: Path) -> Path:
    vendor_root = bundle_root / "installer" / "runtime" / "vendor"
    cwd = Path.cwd().resolve()
    cleaned = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            cleaned.append(entry)
            continue
        if resolved == cwd:
            continue
        if str(resolved) in {str(bundle_root), str(vendor_root)}:
            continue
        cleaned.append(entry)
    sys.path[:] = [str(vendor_root), str(bundle_root)] + cleaned
    return vendor_root



def validate_vendored_imports(vendor_root: Path) -> None:
    from installer.runtime.vendor_imports import (
        VendoredImportError,
        import_required_vendored_module,
    )

    try:
        import_required_vendored_module("yaml", vendor_root)
    except VendoredImportError as exc:
        raise BootstrapError(
            "Vendored yaml import did not resolve inside installer/runtime/vendor."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    bundle_root = bundle_root_from_file(__file__)
    try:
        vendor_root = prepare_sys_path(bundle_root)
        validate_vendored_imports(vendor_root)
        from installer.runtime.cli import main as cli_main
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted. No further installer actions will run.\n")
        return 130
    except (BootstrapError, ImportError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    try:
        return cli_main(argv=argv, bundle_root=bundle_root)
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted. No further installer actions will run.\n")
        return 130



if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
