from pydantic import BaseSettings


class Settings(BaseSettings):
    mid_journey_id = 936929561302675456
    discord_identity_file: str = "discord_ids.csv"
    redis_dsn: str
