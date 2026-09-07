"""Sovereign QM (Quartermaster) harness adapter, v0.1.

Translates a credential-free QM harness receipt into a Sovereign
``experience.v0.1`` candidate. The adapter carries no authority: its output is
an OBSERVED candidate and nothing more.
"""

from .qm_adapter import (
    AdapterError,
    CredentialMaterialSuspected,
    ReceiptRejected,
    map_outcome,
    receipt_to_experience,
    validate_experience,
    validate_receipt,
)

__all__ = [
    "AdapterError",
    "CredentialMaterialSuspected",
    "ReceiptRejected",
    "map_outcome",
    "receipt_to_experience",
    "validate_experience",
    "validate_receipt",
]
