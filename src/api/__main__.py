"""Run the API: python -m src.api"""
import uvicorn
from src.platform.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
        log_config=None,  # structlog handles logging
    )
