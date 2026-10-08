"""Opaque, invocation-bound server authorization; not a JSON API credential.

The seal prevents dictionaries from being mistaken for validated evidence. This
is an in-process trust boundary, not protection against arbitrary server code.
"""

from .models import CalculationRequest


_SEAL = object()


class CalculationPermit:
    __slots__ = ("_seal", "_payload", "_request_id", "_session_id")

    def __init__(self, *, seal, request: CalculationRequest, request_id: str, session_id: str):
        if seal is not _SEAL:
            raise ValueError("permit must be issued by server validation")
        object.__setattr__(self, "_seal", seal)
        object.__setattr__(self, "_payload", request.model_dump_json())
        object.__setattr__(self, "_request_id", request_id)
        object.__setattr__(self, "_session_id", session_id)

    def __setattr__(self, name, value):
        raise AttributeError("immutable calculation permit")

    def matches(self, request: CalculationRequest, *, request_id: str, session_id: str) -> bool:
        return self._seal is _SEAL and self._request_id == request_id and self._session_id == session_id and self._payload == request.model_dump_json()

    def _authorized_request(self) -> CalculationRequest:
        return CalculationRequest.model_validate_json(self._payload)


def _issue_permit(request: CalculationRequest, *, request_id: str, session_id: str) -> CalculationPermit:
    # Only the evidence validator calls this; never a request/document parser.
    return CalculationPermit(seal=_SEAL, request=request, request_id=request_id, session_id=session_id)
