from fastapi import FastAPI
from processing_server.src.routers import apps, reviews

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


app.include_router(apps.router)
app.include_router(reviews.router)
