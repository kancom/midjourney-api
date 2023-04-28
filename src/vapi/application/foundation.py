from enum import Enum


class Priority(str, Enum):
    VIP = "VIP"
    High = "High"
    Normal = "Normal"
    Low = "Low"


class Command(str, Enum):
    New = "New"
    Variation = "Variation"
    Upscale = "Upscale"


class ImagePosition(Enum):
    LeftTop = 1
    RightTop = 2
    LeftBottom = 3
    RightBottom = 4


class Outcome(str, Enum):
    Success = "Success"
    Failure = "Failure"
    Pending = "Pending"
    New = "New"


class NotInCollection(Exception):
    pass
