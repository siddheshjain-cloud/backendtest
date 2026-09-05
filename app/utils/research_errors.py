class ResearchValidationError(Exception):
    code = "validation_error"

    def __init__(self, details: dict[str, list[str]]):
        super().__init__("Request validation failed")
        self.details = details


class ResearchConflictError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ResearchNotFoundError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ResearchForbiddenError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
