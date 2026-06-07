class VantageError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code

class AuthError(VantageError):
    pass

class NotFoundError(VantageError):
    pass

class RateLimitError(VantageError):
    pass

class ValidationError(VantageError):
    pass

def _raise_for_status(response) -> None:
    if response.status_code == 401:
        raise AuthError("Authentication required — provide api_key", 401)
    if response.status_code == 404:
        raise NotFoundError(f"Not found: {response.text}", 404)
    if response.status_code == 429:
        raise RateLimitError("Rate limit exceeded — slow down", 429)
    if response.status_code == 422:
        raise ValidationError(f"Validation error: {response.text}", 422)
    if response.status_code >= 400:
        raise VantageError(f"API error {response.status_code}: {response.text}", response.status_code)
