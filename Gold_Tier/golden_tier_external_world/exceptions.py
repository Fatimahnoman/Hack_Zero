from typing import Optional


class WatcherError(Exception):
    def __init__(self, message: str, platform: Optional[str] = None) -> None:
        self.platform = platform
        prefix = f"[{platform}] " if platform else ""
        super().__init__(f"{prefix}{message}")


class RetryExhaustedError(WatcherError):
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        attempts: int = 0,
        last_exception: Optional[Exception] = None,
    ) -> None:
        self.attempts = attempts
        self.last_exception = last_exception
        detail = f" after {attempts} attempts" if attempts else ""
        if last_exception:
            message = f"{message}{detail}: {last_exception}"
        super().__init__(message, platform)


class HealthCheckError(WatcherError):
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        self.reason = reason
        detail = f" — {reason}" if reason else ""
        super().__init__(f"{message}{detail}", platform)


class AuthenticationError(WatcherError):
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        recoverable: bool = False,
    ) -> None:
        self.recoverable = recoverable
        super().__init__(message, platform)


class PollingError(WatcherError):
    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        recoverable: bool = True,
    ) -> None:
        self.recoverable = recoverable
        super().__init__(message, platform)
