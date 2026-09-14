class AlertsDomainError(Exception):
    """Base exception for alerts domain errors."""


class ChannelNotFoundError(AlertsDomainError):
    """Raised when no channel of that id belongs to the caller.

    One answer for "no such channel" and "somebody else's channel": telling
    the two apart would let anyone enumerate which ids exist.
    """


class ChannelAlreadyVerifiedError(AlertsDomainError):
    """Raised when a link would rebind a channel that is already bound.

    A spent token replayed against a verified channel lands here. Rebinding
    to a *different* chat is the case that matters — it would silently move
    somebody's purchases to another destination.
    """


class ChatAlreadyLinkedError(AlertsDomainError):
    """Raised when the chat that pressed Start belongs to another account.

    One chat, one account. Two people's movements arriving in one Telegram
    conversation is a leak neither of them agreed to, and any future "reply
    to see your balance" would have no way to tell whose balance to answer.
    """


class InvalidLinkTokenError(AlertsDomainError):
    """Raised when a link token is unknown, already spent, or expired.

    Deliberately one exception for all three. The webhook answers the same
    way regardless, so distinguishing them here would only create something
    for a future caller to leak.
    """


class TooManyChannelsError(AlertsDomainError):
    """Raised when an account already holds as many channels as it may."""
