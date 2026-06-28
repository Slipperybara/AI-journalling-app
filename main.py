"""MindForge AI — FastAPI entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import analytics, scheduler
from app.core import settings
from app.db import close_pool, init_db
from app.graph_db import close as graph_close, init_graph
from app.routers import (
    account, admin, agui, conversations, dashboard, devices, goals, messages,
    notifications, profile, stream,
)


app = FastAPI(title="MindForge AI")

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
# Graph init is best-effort: if Neo4j is unreachable (e.g. staging without a
# graph instance), the app must still boot. The live journaling path doesn't
# touch Neo4j; only the analytical GraphRAG path does, and it degrades to a
# graceful apology when the graph is down.
try:
    init_graph()
except Exception as exc:  # pragma: no cover - depends on external service
    print(f"[startup] init_graph failed ({exc}); continuing without graph — analytical path degraded")

app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(agui.router)
app.include_router(stream.router)
app.include_router(devices.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(goals.router)
app.include_router(profile.router)
app.include_router(notifications.router)
app.include_router(account.router)


@app.on_event("startup")
def _startup() -> None:
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()
    graph_close()
    close_pool()
    analytics.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
