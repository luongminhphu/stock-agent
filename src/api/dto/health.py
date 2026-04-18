"""Health check DTOs."""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    env: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    db: bool
