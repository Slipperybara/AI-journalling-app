"""MindForge AI — FastAPI entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import scheduler
from app.db import init_db
from app.graph_db import close as graph_close, init_graph
from app.routers import admin, conversations, dashboard, goals, messages, todos


app = FastAPI(title="MindForge AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
init_graph()

app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(todos.router)
app.include_router(goals.router)


@app.on_event("startup")
def _startup() -> None:
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()
    graph_close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
