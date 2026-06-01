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
    # Phase 2 dev shim: used when supabase_jwt_secret is empty (local dev
    # without a Supabase project). Override per-shell to simulate users.
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"
    # Phase 3 auth. When supabase_jwt_secret is set, every request must
    # carry a valid Supabase Bearer token; the dev shim above is bypassed.
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    # Phase 4 deployment.
    # batch_webhook_secret: shared secret for the GitHub Actions nightly
    # batch webhook (POST /api/admin/run-batch). When empty, the webhook
    # refuses to fire — safe local default.
    batch_webhook_secret: str = ""
    # run_inline_scheduler: if true, APScheduler runs in-process and the
    # 06:00 cron + startup catch-up fire from the FastAPI worker. Default
    # true for local dev (single-machine), set to false on Render where
    # the GitHub Actions cron drives the batch over HTTP instead.
    run_inline_scheduler: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
client = OpenAI(api_key=settings.openai_api_key)
