"""Shared application exceptions for the event collector package."""


class EventCollectorError(Exception):
    """Base class for package-specific runtime errors."""


class MissingOpenAIKeyError(EventCollectorError, ValueError):
    """Raised when OPENAI_API_KEY is required but missing."""


class ArticleSummarizationError(EventCollectorError, RuntimeError):
    """Raised when a stored article cannot be summarized or reindexed."""

    def __init__(self, article_id: int, message: str):
        super().__init__(message)
        self.article_id = article_id


class InvalidEventSourceError(EventCollectorError, ValueError):
    """Raised when an invalid event source is provided."""


class InvalidEventTextError(EventCollectorError, ValueError):
    """Raised when event text is invalid (empty or too short)."""


class MissingAPIKeyError(EventCollectorError, ValueError):
    """Raised when a required API key is missing."""
