import asyncio
import csv
from unittest.mock import AsyncMock

import aiohttp
import pytest
from vapi.infrastructure import Bot
from vapi.settings import Settings


@pytest.fixture
def bot():
    settings = Settings()
    path = settings.discord_identity_file
    qs = AsyncMock()
    qs.get_next_task_id.return_value = None
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            container = Bot.BotInitCont(
                bot_id=row["id"],
                bot_pool=row.get("pool", "common"),
                high_priority=row["high_priority"],
                channel_id=row["channel_id"],
                server_id=row["server_id"],
                user_access_token=row["user_access_token"],
                bot_access_token=row["bot_access_token"],
                human_name=row["human_name"],
                proxy=row.get("proxy"),
            )
            return Bot(init_cont=container, queue_service=qs)


@pytest.mark.asyncio
async def test_info(bot: Bot, event_loop):
    task = event_loop.create_task(bot.start())
    # await asyncio.sleep(10)
    # await bot.send_info_cmd()
    await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_teleg():
    token = "6085315593:AAHLqx3KiscXuRqlun0pPmfqvZbtuvW2UPE"
    chat_id = -1001850006791
    text = f"TEST!"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status > 299:
                text = await response.text()
                print(text)


@pytest.mark.parametrize(
    "s1,s2,o",
    (
        (
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.",
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (0%) (relaxed, stealth)",
            True,
        ),
        (
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (0%) (relaxed, stealth)",
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (10%) (relaxed, stealth)",
            True,
        ),
        (
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (93%) (relaxed, stealth)",
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (Open on website for full quality) (relaxed, stealth)",
            True,
        ),
        (
            "A bocce ball player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (93%) (relaxed, stealth)",
            "A bocce bll player carefully analyzing the court and planning their next move, with focus on the pallino and opponent's balls. The player considers the terrain and backboard while strategizing their throw.** - <@1056855935194234880> (93%) (relaxed, stealth)",
            False,
        ),
        (
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5",
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5 --v 5.1 --q 2** - <@1096470137110020137> (Waiting to start)",
            True,
        ),
        (
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5",
            "Minotaur in iron armor, one horn, bloody background, 8k, unreal engine 5  ",
            False,
        ),
        (
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5 --v 5.1 --q 2** - <@1096470137110020137> (Waiting to start)",
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5 --v 5.1 --q 2** - <@1096470137110020137> (0%) (fast, stealth)",
            True,
        ),
        (
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5 --v 5.1 --q 2** - <@1096470137110020137> (0%) (fast, stealth)",
            "Minotaur in iron armor, one horn, red background, 8k, unreal engine 5 --v 5.1 --q 2** - <@1096470137110020137> (fast, stealth)",
            True,
        ),
    ),
)
def test_substring(s1: str, s2: str, o: bool, bot: Bot):
    assert bot.str_in_str(s1, s2) == o
