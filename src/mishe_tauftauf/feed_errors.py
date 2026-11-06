"""The feed's error type, in a leaf module.

``chat_protocol`` subclasses it and ``feed`` imports ``chat_protocol`` at module
level, so the class cannot live in ``feed`` without a cycle. ``feed`` re-exports
it, so ``from .feed import FeedError`` keeps working.
"""


class FeedError(ValueError):
    pass
