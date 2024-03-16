from fastapi import FastAPI
from routes import body, more

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    docs_url="/")

app.include_router(body.router)
app.include_router(more.router)
