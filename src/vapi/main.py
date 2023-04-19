import ConfigMidJourney
from BotMidJourney import BotMidJourney
import asyncio
import uvicorn
from fastapi import FastAPI, Body, Depends, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from typing import List, Optional
from pydantic import BaseModel, Field
import platform
import uuid as uuid_pkg
from enum import IntEnum, Enum
from pymongo import MongoClient
from PIL import Image
from io import BytesIO
import requests

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class NN(IntEnum):
    v2 = 2
    v3 = 3
    v4 = 4
    v5 = 5


class SS(IntEnum):
    low = 50
    medium = 100
    high = 250
    veryhigh = 750


class QQ(str, Enum):
    low = '0.25'
    medium = '0.5'
    base = '1.0'
    high = '2.0'


class CC(IntEnum):
    one = 1
    two = 2
    three = 3
    four = 4


class RequestImagine(BaseModel):
    uuid: uuid_pkg.UUID = Field(default_factory=uuid_pkg.uuid4)
    styles: List[str] = []
    chaos: int = Field(gt=-1, lt=101, description="Chaos parameter must be integer 0-100 ")
    stylization: SS = SS.medium
    quality: QQ = QQ.base
    ratio: Optional[str] = None
    prompt: str
    neg_prompt: Optional[str] = None
    nn: NN = NN.v5


class RequestUpscale(BaseModel):
    uuid: uuid_pkg.UUID = Field(default_factory=uuid_pkg.uuid4)
    bot_id: int
    message_id: int
    image_name: str
    command_index: CC


class RequestVariation(BaseModel):
    uuid: uuid_pkg.UUID = Field(default_factory=uuid_pkg.uuid4)
    bot_id: int
    message_id: int
    image_name: str
    command_index: CC




app = FastAPI()
app.bots = []

BotMidJourney.MID_JOURNEY_ID = ConfigMidJourney.GLOBAL["MID_JOURNEY_ID"]
BotMidJourney.COUNT_TRY = ConfigMidJourney.GLOBAL["COUNT_TRY"]
BotMidJourney.WEB_HOOK_URL = ConfigMidJourney.GLOBAL["WEB_HOOK_URL"]

for bot_config in ConfigMidJourney.BOTS:
    app.bots.append(BotMidJourney(bot_config))


@app.on_event("startup")
async def start_db():
    app.mongo_client = MongoClient(ConfigMidJourney.GLOBAL["MongoDBConnectionString"])
    for bot in app.bots:
        bot.mongo_client = app.mongo_client
        bot.discord_client.mongo_client = app.mongo_client
        loop = asyncio.get_running_loop()
        loop.create_task(bot.worker())
        loop.create_task(bot.discord_coroutine)


@app.on_event("shutdown")
def shutdown_db_client():
    app.mongo_client.close()


@app.get("/", response_class=HTMLResponse)
def root_html():
    return "<h2>Добро пожаловать!</h2>"

@app.get("/api/getimage")
async def getImage(
    url: str, 
    responses = { 200: { "content": {"image/png": {}} }}, 
    response_class="StreamingResponse"):

    img = Image.open(requests.get(url, stream=True).raw)
    imgio = BytesIO()
    img.save(imgio, 'PNG')
    imgio.seek(0)
    return StreamingResponse(content=imgio, media_type="image/jpeg")
    

@app.post("/api/imagine")
async def sendImagine(dataM: RequestImagine = Depends()):
    db = app.mongo_client["MidJourneyServiceDB"]
    income_collection = db["income"]
    data = jsonable_encoder(dataM)
    data["type_command"] = "imagine"
    result_insert = income_collection.insert_one(data)
    selected_bot = None
    if "id_bot" in data:
        selected_bot = next((x for x in app.bots if x.id == data["bot_id"]), None)
        if not selected_bot:
            return {"result": "error", "message": "Отсутствует бот с таким bot_id"}
    else:
        selected_bot = min(app.bots, key=lambda r: r.queue.qsize())
        income_collection.update_one({"_id": result_insert.inserted_id}, {"$set": {"id_bot": selected_bot.id}})
    added_record = income_collection.find_one({"_id": result_insert.inserted_id})
    await selected_bot.queue.put(added_record)
    return {"result": "success", "bot_id": selected_bot.id, "count_queue": selected_bot.queue.qsize(),
            "record_id": str(result_insert.inserted_id)}


@app.post("/api/upscale")
async def sendUpscale(dataM: RequestUpscale = Depends()):
    db = app.mongo_client["MidJourneyServiceDB"]
    income_collection = db["income"]
    data = jsonable_encoder(dataM)
    completed_collection = db["completed"]
    main_message = completed_collection.find_one({"message_id": data["message_id"]})
    if main_message:
        data["promt_text"] = main_message["promt_text"]
        data["type_command"] = "upscale"
        result_insert = income_collection.insert_one(data)
        selected_bot = next((x for x in app.bots if x.id == data["bot_id"]), None)
        if not selected_bot:
            return {"result": "error", "message": "not find bot with bot_id"}
        added_record = income_collection.find_one({"_id": result_insert.inserted_id})
        await selected_bot.queue.put(added_record)
        return {"result": "success", "bot_id": selected_bot.id, "count_queue": selected_bot.queue.qsize(),
                "record_id": str(result_insert.inserted_id)}
    else:
        return {"result": "error", "message": "not find message_id"}


@app.post("/api/variation")
async def sendVariation(dataM: RequestVariation = Depends()):
    db = app.mongo_client["MidJourneyServiceDB"]
    income_collection = db["income"]
    data = jsonable_encoder(dataM)
    completed_collection = db["completed"]
    main_message = completed_collection.find_one({"message_id": data["message_id"]})
    if main_message:
        data["promt_text"] = main_message["promt_text"]
        data["type_command"] = "variation"
        result_insert = income_collection.insert_one(data)
        selected_bot = next((x for x in app.bots if x.id == data["bot_id"]), None)
        if not selected_bot:
            return {"result": "error", "message": "not find bot with bot_id"}
        added_record = income_collection.find_one({"_id": result_insert.inserted_id})
        await selected_bot.queue.put(added_record)
        return {"result": "success", "bot_id": selected_bot.id, "count_queue": selected_bot.queue.qsize(),
                "record_id": str(result_insert.inserted_id)}
    else:
        return {"result": "error", "message": "not find message_id"}

if __name__ == "__main__":
    uvicorn.run(app)