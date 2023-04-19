import asyncio
import discord
import requests
import re
from typing import List


class MidJourneyDiscordClient(discord.Client):
    async def on_ready(self):
        print(f'Со мной все хорошо, зашел как {self.user}')

    async def on_message(self, message):
        if message.author.id == BotMidJourney.MID_JOURNEY_ID:
            if message.content.find("Waiting to start") != -1 or message.content.find("(paused)") != -1:
                db = self.mongo_client["MidJourneyServiceDB"]
                income_collection = db["income"]
                result_search_text = re.search(r'\*\*.+\*\*', message.content)
                if result_search_text:

                    income_collection.update_one({"_id": self.current_task_id}, {
                        "$set": {"id_message_wait": message.id, "promt_text": result_search_text[0]}})
                    task_db = income_collection.find_one({"_id": self.current_task_id})
                    response_web_hook = self.PassToWebHook(
                        {"type": "command_waiting", "text": "Команда принята, идет ожидание",
                         "message_content": message.content, "message_id": message.id,
                         "record_id": str(self.current_task_id)})
                    self.BotFather.is_blocked = False
                else:
                    response_web_hook = self.PassToWebHook(
                        {"type": "warning", "text": "Специфичный ответ", "message_content": message.content,
                         "message_id": message.id, "record_id": str(self.current_task_id)})

            elif len(message.attachments) > 0:
                db = self.mongo_client["MidJourneyServiceDB"]
                income_collection = db["income"]
                completed_collection = db["completed"]
                result_search_text = re.search(r'\*\*.+\*\*', message.content)
                if result_search_text:

                    task_db = income_collection.find_one({"promt_text": result_search_text[0]})
                    if task_db:
                        url = message.attachments[0].url
                        image_name = message.attachments[0].url.split("_")[-1].split(".")[0]
                        response_web_hook = self.PassToWebHook(
                            {"type": "command_complete", "text": "Все успешно", "url": url,
                             "image_name": image_name,
                             "message_content": message.content, "message_id": message.id,
                             "record_id": str(self.current_task_id), "uuid": task_db["uuid"]})
                        result_insert = completed_collection.insert_one({"promt_text": result_search_text[0],
                                                                         "message_id": message.id,
                                                                         "image_name": image_name,
                                                                         "url": url, "bot_id": self.BotFather.id})
                        income_collection.delete_one({"_id": task_db["_id"]})
                    else:
                        response_web_hook = self.PassToWebHook(
                            {"type": "warning", "text": "Ошибка, в бд не найдена задача по такому тексту",
                             "message_content": message.content, "message_id": message.id,
                             "record_id": str(self.current_task_id)})
                else:
                    response_web_hook = self.PassToWebHook(
                        {"type": "warning", "text": "Специфичный ответ", "message_content": message.content,
                         "message_id": message.id, "record_id": str(self.current_task_id)})

    async def on_message_edit(self, before, after):
        if after.author.id == BotMidJourney.MID_JOURNEY_ID:
            if after.content.find("%)") != -1:
                db = self.mongo_client["MidJourneyServiceDB"]
                income_collection = db["income"]
                task_db = income_collection.find_one({"id_message_wait": after.id})
                progress_match = re.search(r'.+> \((\d+)', after.content)
                progress_str = ""
                if progress_match and progress_match[1]:
                    progress_str = progress_match[1]
                response_web_hook = self.PassToWebHook(
                    {"type": "command_progress", "text": "Прогресс выполнения", "message_content": after.content,
                     "message_id": after.id, "progress": progress_str, "record_id": str(task_db["_id"]),
                     "uuid": task_db["uuid"]
                     })

    def PassPromptToSelfBot(self, opt: List[dict]) -> requests.Response:
        payload = {"type": 2, "application_id": "936929561302675456", "guild_id": self.BotFather.server_id,
                   "channel_id": self.BotFather.channel_id, "session_id": "2fb980f65e5c9a77c96ca01f2c242cf6",
                   "data": {"version": "1077969938624553050", "id": "938956540159881230", "name": "imagine", "type": 1,
                            "options": opt,
                            "application_command": {"id": "938956540159881230",
                                                    "application_id": "936929561302675456",
                                                    "version": "1077969938624553050",
                                                    "default_permission": True,
                                                    "default_member_permissions": None,
                                                    "type": 1, "nsfw": False, "name": "imagine",
                                                    "description": "Create images with Midjourney",
                                                    "dm_permission": True,
                                                    "options": [{"type": 3, "name": "prompt",
                                                                 "description": "The prompt to imagine",
                                                                 "required": True}]},
                            "attachments": []}}

        header = {
            'authorization': self.BotFather.access_token_user
        }
        response = requests.post("https://discord.com/api/v9/interactions",
                                 json=payload, headers=header)
        return response

    def PassVariationToSelfBot(self, messageId: int, image_name: str, index: int) -> requests.Response:
        payload = {"type": 3, "guild_id": self.BotFather.server_id,
                   "channel_id": self.BotFather.channel_id,
                   "message_flags": 0,
                   "message_id": messageId,
                   "application_id": "936929561302675456",
                   "session_id": "1f3dbdf09efdf93d81a3a6420882c92c",
                   "data": {"component_type": 2, "custom_id": "MJ::JOB::variation::{}::{}".format(index, image_name)}}

        header = {
            'authorization': self.BotFather.access_token_user
        }
        response = requests.post("https://discord.com/api/v9/interactions",
                                 json=payload, headers=header)
        return response

    def PassUpscaleToSelfBot(self, messageId: int, image_name: str, index: int) -> requests.Response:
        payload = {"type": 3,
                   "guild_id": self.BotFather.server_id,
                   "channel_id": self.BotFather.channel_id,
                   "message_flags": 0,
                   "message_id": messageId,
                   "application_id": "936929561302675456",
                   "session_id": "45bc04dd4da37141a5f73dfbfaf5bdcf",
                   "data": {"component_type": 2,
                            "custom_id": "MJ::JOB::upsample::{}::{}".format(index, image_name)}
                   }

        header = {
            'authorization': self.BotFather.access_token_user
        }
        response = requests.post("https://discord.com/api/v9/interactions",
                                 json=payload, headers=header)
        return response

    def PassToWebHook(self, data_web_hook):
        print(f"data {data_web_hook}")
        payload = data_web_hook
        response = requests.post(BotMidJourney.WEB_HOOK_URL, json=payload)
        return response


class BotMidJourney:
    """Наш прекрасный бот для MidJourney"""

    MID_JOURNEY_ID = 936929561302675456
    MID_JOURNEY_ID_STR = "936929561302675456"
    COUNT_TRY = 60
    WEB_HOOK_URL = "https://webhook.site/a6ca6fce-2cd0-4f6a-9f84-6a840a987b27"

    @staticmethod
    def getPromtByTask(current_task: dict) -> List[dict]:
        if "prompt" not in current_task or current_task['prompt'] is None:
            return None

        if "styles" in current_task and current_task['styles']:
            current_task['prompt'] = current_task['prompt'] + ', ' + ' '.join(current_task['styles'])

        if "ratio" in current_task and current_task['ratio']:
            current_task['prompt'] = current_task['prompt'] + ' --ar ' + current_task['ratio']

        if "chaos" in current_task and current_task['chaos']:
            current_task['prompt'] = current_task['prompt'] + ' --chaos ' + str(current_task['chaos'])

        if "neg_prompt" in current_task and current_task['neg_prompt']:
            current_task['prompt'] = current_task['prompt'] + ' --no ' + current_task['neg_prompt']

        if "nn" in current_task and current_task['nn']:
            current_task['prompt'] = current_task['prompt'] + ' --version ' + str(current_task['nn'])

        if "stylization" in current_task and current_task['stylization']:
            current_task['prompt'] = current_task['prompt'] + ' --s ' + str(current_task['stylization'])

        if "quality" in current_task and current_task['quality']:
            current_task['prompt'] = current_task['prompt'] + ' --s ' + current_task['quality']

        options = [{
            "type": 3,
            "name": "prompt",
            "value": current_task['prompt']
        }]

        return options

    def __init__(self, configSection):
        """Замечательный конструктор"""
        self.id = configSection["ID"]
        self.name = configSection["NAME"]
        self.access_token_bot = configSection["ACCESS_TOKEN_BOT"]
        self.access_token_user = configSection["ACCESS_TOKEN_USER"]
        self.channel_id = configSection["CHANNEL_ID"]
        self.channel_name = configSection["CHANNEL_NAME"]
        self.server_id = configSection["SERVER_ID"]
        self.server_name = configSection["SERVER_NAME"]
        self.queue = asyncio.Queue()
        self.targetID = ""
        self.targetHash = ""
        self.current_task = 0

        self.discord_client = MidJourneyDiscordClient(intents=discord.Intents.all())
        self.discord_client.BotFather = self
        self.discord_coroutine = self.discord_client.start(self.access_token_bot)
        self.is_blocked = False

    def __str__(self):
        return f"ID: {self.id}  Name: {self.name}  Token: {self.access_token_bot}"

    async def worker(self):
        while True:
            my_task = await self.queue.get()
            self.is_blocked = True
            self.discord_client.current_task_id = my_task["_id"]
            count_try = 0
            if my_task["type_command"] == "imagine":
                promt_command = BotMidJourney.getPromtByTask(my_task)
                response = self.discord_client.PassPromptToSelfBot(promt_command)
            elif my_task["type_command"] == "upscale":
                response = self.discord_client.PassUpscaleToSelfBot(my_task["message_id"], my_task["image_name"],
                                                                    my_task["command_index"])
            elif my_task["type_command"] == "variation":
                response = self.discord_client.PassVariationToSelfBot(my_task["message_id"], my_task["image_name"],
                                                                      my_task["command_index"])
            while count_try < BotMidJourney.COUNT_TRY and self.is_blocked:
                await asyncio.sleep(2)
                count_try = count_try + 1
            if self.is_blocked:
                print(f"Я бот с именем {self.name} и я ТАК И НЕ ДОЖДАЛСЯ ОТВЕТА по задаче {my_task}")
                self.is_blocked = False
                self.discord_client.current_task_id = None
