"""Recipe execution admission failures shared by server recipe and tool code."""


class RecipeExecutionAdmissionError(RuntimeError):
    """Stable pre-launch attested-execution rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
