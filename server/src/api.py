from fastapi import FastAPI
from server.src.routers import apps

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


app.include_router(apps.router)
