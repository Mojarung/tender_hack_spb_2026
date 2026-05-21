class PricePulseError(Exception):
    """Base error for the application."""


class ScraperError(PricePulseError):
    """Raised when an adapter cannot return any result for a recoverable reason."""


class CaptchaChallenge(ScraperError):
    """The source responded with an unsolvable (or unsolved) CAPTCHA."""


class RateLimited(ScraperError):
    """The source rate-limited us; the orchestrator should back off."""
