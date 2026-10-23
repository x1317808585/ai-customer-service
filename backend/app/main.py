from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionDep

app = FastAPI(title="AI 客服")


@app.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from error
    return {"status": "ok"}
