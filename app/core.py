from openai import OpenAI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    day_boundary_hour: int = 6

    class Config:
        env_file = ".env"


settings = Settings()
client = OpenAI(api_key=settings.openai_api_key)

DB_NAME = "journal.db"
