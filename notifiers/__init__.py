"""Notifier framework for presenting issues to humans."""

from abc import ABC, abstractmethod
from checkers import Issue


class BaseNotifier(ABC):
    """Base class for notification channels."""

    @abstractmethod
    def notify(self, issues: list[Issue]) -> None:
        """Send notifications for detected issues."""
        ...

    @abstractmethod
    def listen(self) -> None:
        """Start listening for user responses (button clicks, replies, etc.)."""
        ...
