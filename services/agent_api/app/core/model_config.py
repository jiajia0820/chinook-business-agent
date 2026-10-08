"""Future explicit online factories only; offline startup never calls from_env."""

from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

from pydantic import SecretStr


class ModelConfigurationError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("model configuration unavailable: " + reason)


@dataclass(frozen=True, slots=True, repr=False)
class ModelConfig:
    # All values kept out of repr; this object is never added to Settings/API.
    endpoint: str
    model_name: str
    api_key: SecretStr = field(repr=False)

    def __repr__(self):
        return "ModelConfig(<redacted>)"

    def __post_init__(self):
        if not isinstance(self.endpoint, str):
            raise ModelConfigurationError("ENDPOINT_INVALID")
        try:
            parts = urlsplit(self.endpoint)
            port = parts.port
        except (ValueError, TypeError):
            raise ModelConfigurationError("ENDPOINT_INVALID") from None
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or port is not None and not 1 <= port <= 65535 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.endpoint):
            raise ModelConfigurationError("ENDPOINT_INVALID")
        if not parts.path.rstrip("/").endswith("/chat/completions"):
            raise ModelConfigurationError("ENDPOINT_NOT_COMPLETE")
        if not isinstance(self.model_name, str) or not self.model_name.strip() or len(self.model_name) > 128 or any(char in self.model_name for char in "\r\n\x00"):
            raise ModelConfigurationError("MODEL_NAME_INVALID")
        if not isinstance(self.api_key, SecretStr):
            raise ModelConfigurationError("KEY_INVALID")
        key = self.api_key.get_secret_value()
        if not key or len(key) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key):
            raise ModelConfigurationError("KEY_INVALID")

    @classmethod
    def from_env(cls):
        # No C_SQL_MODEL_* fallback: exactly one team's canonical namespace.
        endpoint, model, key = (os.getenv(name) for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
        if not all((endpoint, model, key)):
            raise ModelConfigurationError("CONFIG_MISSING")
        return cls(endpoint=endpoint.strip(), model_name=model.strip(), api_key=SecretStr(key))

    def c_http_parameters(self):
        """Trusted future C constructor mapping, not a log/JSON/export method.

        No model is instantiated or contacted here. A future, separately
        approved live factory may pass these keywords to HTTPJSONModel.
        """
        return {"endpoint": self.endpoint, "model": self.model_name, "api_key": self.api_key.get_secret_value()}
