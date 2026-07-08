from fastapi import FastAPI
from db_server.src.routers import apps, reviews, admin

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


app.include_router(apps.router)
app.include_router(reviews.router)
app.include_router(admin.router)
