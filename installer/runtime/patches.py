from __future__ import annotations

from . import klipper_cfg
from .manifest import select_patch_variant
from .models import PatchLedgerEntry, PatchResult, PatchSpec, SectionPatchSpec


INSTALL_APPLIED = "applied"
INSTALL_NOOP_DESIRED = "noop_desired"
UNINSTALL_REVERTED = "reverted"
UNINSTALL_NOOP_EXPECTED = "noop_expected"
USER_MODIFIED = "user_modified"
SECTION_DELETED = "__TLTG_SECTION_DELETED__"


def classify_install_patch(current: str, patch: PatchSpec, firmware_version: str, prior_state=None) -> PatchResult:
    variant = select_patch_variant(patch, firmware_version)
    if current == variant.desired:
        classification = INSTALL_NOOP_DESIRED
    elif current == variant.expected:
        classification = INSTALL_APPLIED
    elif current == "65" and _prior_managed_65(prior_state, patch):
        classification = INSTALL_APPLIED
    else:
        classification = USER_MODIFIED
    return PatchResult(
        id=patch.id,
        file=patch.file,
        section=patch.section,
        option=patch.option,
        current=current,
        expected=variant.expected,
        desired=variant.desired,
        classification=classification,
    )



def _prior_managed_65(prior_state, patch: PatchSpec) -> bool:
    if prior_state is None or patch.id not in {"stepper_x_homing_speed", "stepper_y_homing_speed"}:
        return False
    return any(
        entry.id == patch.id
        and entry.target_tuple == patch.target_tuple
        and entry.expected == select_patch_variant(patch, prior_state.runtime_firmware).expected
        and entry.desired == "65"
        and entry.install_result in {INSTALL_APPLIED, INSTALL_NOOP_DESIRED}
        for entry in prior_state.patch_ledger
    )


def classify_install_section_delete(
    current: str | None, patch: SectionPatchSpec, firmware_version: str
) -> PatchResult:
    variant = select_patch_variant(patch, firmware_version)
    if current is None:
        classification = INSTALL_NOOP_DESIRED
        expected = variant.expected_normalized_sha256
        current_value = SECTION_DELETED
    else:
        current_hash = klipper_cfg.normalized_section_sha256(current)
        if current_hash == variant.expected_normalized_sha256:
            classification = INSTALL_APPLIED
        else:
            classification = USER_MODIFIED
        expected = current if classification == INSTALL_APPLIED else variant.expected_normalized_sha256
        current_value = current
    return PatchResult(
        id=patch.id,
        file=patch.file,
        section=patch.section,
        option=patch.option,
        current=current_value,
        expected=expected,
        desired=SECTION_DELETED,
        classification=classification,
    )



def classify_uninstall_patch(current: str, entry: PatchLedgerEntry) -> PatchResult:
    if current == entry.desired:
        classification = UNINSTALL_REVERTED
    elif current == entry.expected:
        classification = UNINSTALL_NOOP_EXPECTED
    else:
        classification = USER_MODIFIED
    return PatchResult(
        id=entry.id,
        file=entry.file,
        section=entry.section,
        option=entry.option,
        current=current,
        expected=entry.expected,
        desired=entry.desired,
        classification=classification,
    )

