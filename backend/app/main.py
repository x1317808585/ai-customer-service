from fastapi import FastAPI

app = FastAPI(title="AI 客服")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
