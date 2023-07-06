import asyncio
import csv

from dependency_injector.wiring import Provide, inject
from loguru import logger
from prometheus_client import start_http_server
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from vapi.application import ICaptchaService, IQueueService
from vapi.infrastructure.service.discord_bot import Bot
from vapi.settings import Settings
from vapi.wiring import Container

tasks = {}


class FileChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        loop,
        path: str,
        queue_service: IQueueService,
        captcha_srv: ICaptchaService,
    ):
        self.loop = loop
        self._path = path
        self._queue = queue_service
        self._captcha_srv = captcha_srv

    def on_modified(self, event):
        print(event)
        if event.src_path.endswith(self._path):
            # self.reload_config()
            asyncio.run_coroutine_threadsafe(self.reload_config(), loop=self.loop)

    def _some(self):
        for_removal = list(tasks.keys())
        with open(self._path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                container = Bot.BotInitCont(
                    bot_id=row["id"],
                    bot_pool=row.get("pool", "common"),
                    high_priority=row["high_priority"],
                    channel_id=row["channel_id"],
                    server_id=row["server_id"],
                    user_access_token=row["user_access_token"],
                    human_name=row["human_name"],
                    proxy=row.get("proxy"),
                    captcha_service=self._captcha_srv,
                )

                if container.bot_id in tasks:
                    if hash(container) == tasks[container.bot_id][0]:
                        del for_removal[for_removal.index(container.bot_id)]
                        continue
                    tasks[container.bot_id][1].cancel()
                    del tasks[container.bot_id]
                bot = Bot(
                    init_cont=container,
                    queue_service=self._queue
                    # , loop=self.loop
                )
                logger.info("adding", human=bot.identity)
                task = self.loop.create_task(bot.start())
                tasks[container.bot_id] = (hash(container), task)
        for bot_id in for_removal:
            tasks[bot_id][1].cancel()
            del tasks[bot_id]
            logger.info("removal", human=bot_id)

    async def reload_config(self):
        self._some()


@inject
async def main(
    queue_service: IQueueService = Provide[Container.queue_service],
    captcha_srv: ICaptchaService = Provide[Container.captcha_service],
):
    settings = Settings()

    event_handler = FileChangeHandler(
        asyncio.get_running_loop(),
        path=settings.discord_identity_file,
        queue_service=queue_service,
        captcha_srv=captcha_srv,
    )
    observer = Observer()
    observer.schedule(event_handler, settings.discord_identity_file, recursive=True)
    observer.start()
    event_handler._some()
    await asyncio.gather(*[v[1] for v in tasks.values()])

    try:
        while observer.is_alive():
            await asyncio.sleep(2)
            observer.join(1)
    finally:
        observer.stop()
        observer.join()


def run():
    container = Container()
    container.wire(modules=[__name__])
    asyncio.run(main())


if __name__ == "__main__":
    start_http_server(8000)
    run()
