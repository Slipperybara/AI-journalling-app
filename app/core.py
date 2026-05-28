from openai import OpenAI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    day_boundary_hour: int = 6
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mindforge"

    class Config:
        env_file = ".env"


settings = Settings()
client = OpenAI(api_key=settings.openai_api_key)

DB_NAME = "journal.db"
