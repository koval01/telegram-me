from app.services.gemini import gemini_service


def get_gemini_service() -> None:
    """Initialized Gemini service for handlers"""
    return gemini_service
