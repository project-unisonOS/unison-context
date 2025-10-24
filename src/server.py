from fastapi import FastAPI
import uvicorn

app = FastAPI(title="unison-context")

@app.get("/health")
def health():
    return {"status": "ok", "service": "unison-context"}

@app.get("/ready")
def ready():
    # Future: check storage backend
    return {"ready": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
