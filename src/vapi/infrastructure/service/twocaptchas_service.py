from io import BytesIO
from typing import List

import aiohttp
from twocaptcha import TwoCaptcha
from vapi.application import ICaptchaService, NotFound
from vapi.utils import img2captcha

from ..counters import SERVICE_ERRORS, SERVICE_USAGE


class TwoCaptchasService(ICaptchaService):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._solver = TwoCaptcha(self._api_key)

    async def solve(self, img_url: str, labels: List[str]) -> str:
        async with aiohttp.ClientSession() as ses:
            async with ses.get(img_url) as resp:
                img_bytes = await resp.read()
                adapted, boxes = img2captcha(BytesIO(img_bytes), labels)
                res = self._solver.coordinates(
                    adapted.decode("utf-8"),
                    hintText="Please select which description best fits the image",
                    lang="en",
                )
                if "code" in res and ":" in res["code"]:
                    coords = res["code"].split(":")[1]
                    x, y = coords.split(",")
                    _, x = x.split("=")
                    _, y = y.split("=")
                    for k, v in boxes.items():
                        if (v[0] < int(x) < v[2]) and (v[1] < int(y) < v[3]):
                            SERVICE_USAGE.labels(
                                service=self.__class__.__name__,
                                account=self._api_key[:-6],
                                measurement="captcha",
                            ).inc()
                            return k
        SERVICE_ERRORS.labels(
            service=self.__class__.__name__,
            error="not_solved",
            account=self._api_key[:-6],
        ).inc()

        raise NotFound(f"solution for {img_url} wasn't found {res}")
