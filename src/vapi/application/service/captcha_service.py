import abc
from typing import List


class ICaptchaService(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def solve(self, img_url: str, labels: List[str]) -> str:
        pass
