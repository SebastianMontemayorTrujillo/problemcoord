"""Typed error vocabulary (docs/contracts.md)."""

from enum import Enum


class ErrorCode(str, Enum):
    INPUT_REJECTED = "input_rejected"
    EXTRACTION_UNCERTAIN = "extraction_uncertain"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_JSON = "invalid_json"
    UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
    STALE_LEASE = "stale_lease"
    INVALID_TRANSITION = "invalid_transition"
    NOT_FOUND = "not_found"

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE


_RETRYABLE = {ErrorCode.TIMEOUT, ErrorCode.RATE_LIMITED, ErrorCode.PROVIDER_FAILURE,
              ErrorCode.INVALID_JSON}


class HWError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code.retryable

    def info(self):
        from hwreview.contracts import ErrorInfo
        return ErrorInfo(code=self.code.value, message=self.message, retryable=self.retryable)
