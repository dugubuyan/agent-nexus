"""
Error types and response models for the Doc Exchange Center services.
"""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: dict | None = None


class DocExchangeError(Exception):
    """Raised by service layer to signal a domain error."""

    def __init__(self, error_code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            error_code=self.error_code,
            message=self.message,
            details=self.details,
        )
