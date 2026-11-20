"""One error type for the whole app; every non-2xx body is {"error", "code"}."""

STATUS = {
    "BAD_INPUT": 400, "TOO_LARGE": 413, "NOT_FOUND": 404, "KEYS_LOCAL_ONLY": 403,
    "AI_NOT_CONFIGURED": 503, "AI_KEY_INVALID": 502, "AI_BUSY": 503, "AI_FAILED": 502,
    "TICKET_REQUIRED": 401, "TICKET_INVALID": 401, "TICKET_EXPIRED": 401, "TICKET_USED_UP": 429,
    "DAILY_CAP_REACHED": 429, "RATE_LIMITED": 429,
}
MESSAGES = {
    "AI_NOT_CONFIGURED": "No Groq key is set. Open API Keys and paste a free key from console.groq.com/keys.",
    "AI_KEY_INVALID": "Your Groq key was rejected. Open API Keys and paste a fresh key from console.groq.com/keys.",
    "AI_BUSY": "The AI service is busy right now. Wait a minute and try again.",
    "AI_FAILED": "The AI couldn't process this input.",
    "TICKET_REQUIRED": "Sign in on lastsem.com to try your own input.",
    "TICKET_INVALID": "This demo ticket is not valid. Open the demo again from lastsem.com.",
    "TICKET_EXPIRED": "This demo ticket has expired. Open the demo again from lastsem.com.",
    "TICKET_USED_UP": "You have used all live runs on this ticket. Samples still work.",
    "DAILY_CAP_REACHED": "The demo has reached its daily limit. Samples still work; try live runs tomorrow.",
}


class AppError(Exception):
    def __init__(self, code: str, message: str | None = None, headers: dict | None = None):
        self.code = code
        self.status = STATUS.get(code, 500)
        self.message = message or MESSAGES.get(code, "Something went wrong.")
        self.headers = headers
        super().__init__(self.message)
