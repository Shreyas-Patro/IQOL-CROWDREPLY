from abc import ABC, abstractmethod


class LLMTransientError(RuntimeError):
    """Raised when the LLM API fails with a transient server-side error (503/429 exhausted)."""


class LLMDailyQuotaError(RuntimeError):
    """Raised when the daily free-tier quota is exhausted. Retrying will not help until tomorrow."""


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Send a system + user message pair and return the raw response string."""
