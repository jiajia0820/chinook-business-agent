"""Deterministic, evidence-only response composition; no model loop."""

from .composer import FormalResponseComposer
from .models import ComposeContext, ResponseComposer

__all__ = ["ComposeContext", "ResponseComposer", "FormalResponseComposer"]
