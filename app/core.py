from openai import OpenAI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    database_url: str = "postgresql://localhost:5432/mindforge_dev"
    day_boundary_hour: int = 6
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mindforge"
    max_active_goals: int = 3
    cors_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()
client = OpenAI(api_key=settings.openai_api_key)
