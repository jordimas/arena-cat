import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.exceptions import TASK_TOKEN_INVALID, TaskTokenError
from app.routes import auth, categories, ranking, task, vote

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

app = FastAPI(title="arena-cat backend")


@app.exception_handler(TaskTokenError)
async def task_token_error_handler(request: Request, exc: TaskTokenError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": TASK_TOKEN_INVALID},
    )


# CORS permissiu per a desenvolupament local
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(task.router, prefix="/api", tags=["Task"])
app.include_router(vote.router, prefix="/api", tags=["Vote"])
app.include_router(ranking.router, prefix="/api", tags=["Ranking"])
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(categories.router, prefix="/api", tags=["Categories"])
