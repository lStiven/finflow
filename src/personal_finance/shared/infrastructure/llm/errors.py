"""How a language model can fail, split by what the caller should do about it.

The distinction is the same one the mailbox adapters make: guessing
"permanent" wrongly throws away a real transaction, guessing "transient"
wrongly only costs a retry — so anything ambiguous is transient.
"""


class LLMError(Exception):
    """Base class for every failure of the model fallback."""


class LLMNotConfiguredError(LLMError):
    """Raised when something asks for a model and no API key was provided."""


class LLMTemporarilyUnavailableError(LLMError):
    """Rate limited, overloaded, or a server-side failure. Worth retrying.

    Callers must let this propagate: the queue message stays undeleted, comes
    back after the visibility timeout, and the same email is tried again.
    """


class LLMRequestRejectedError(LLMError):
    """The request itself was refused — bad key, unknown model, bad schema.

    This is our misconfiguration, not a hard email. It fails identically for
    every message, so it is never swallowed: silently deferring thousands of
    emails would hide a broken deployment behind a growing backlog.
    """


class LLMUnusableResponseError(LLMError):
    """The model answered, but not in the shape that was demanded."""
