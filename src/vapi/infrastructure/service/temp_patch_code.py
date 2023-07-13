import aiohttp
import asyncio


async def get_data_from_midjourney() -> dict:
    url = "https://discord.com/api/v9/channels/1008571141507534928/application-commands/search?type=1&limit=25&include_applications=true"
    #    headers = {"Authorization": self._user_access_token}
    headers = {
        "Authorization": "MTA1Njg1MDA1NDM3MzE4MzUyOA.G7ir-4.g99a8tsRDsnEN7u_sFHb_dseD8m0luxRAxtnco"
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            raw_data = await response.json()
            return raw_data.get("application_commands")


def extract_initial_values(data_list: list, description: str) -> dict:
    for idx in range(len(data_list)):
        if data_list[idx]["description"] == description:
            return {
                "application_id": data_list[idx]["application_id"],
                "version": data_list[idx]["version"],
                "id": data_list[idx]["id"],
            }
    return {
        "application_id": "936929561302675456",
        "version": "1118961510123847772",
        "id": "938956540159881230",
    }


async def main():
    app_commands_data = await get_data_from_midjourney()
    description = "Create images with Midjourney"
    values = extract_initial_values(app_commands_data, description)
    payload = {
        "application_id": values.get("application_id"),
        "data": {
            "version": values.get("version"),
            "id": values.get("id"),
            "application_command": {
                "id": values.get("id"),
                "application_id": values.get("application_id"),
                "version": values.get("version"),
                "description": description,
            },
        },
    }
    print(payload)


if __name__ == "__main__":
    asyncio.run(main())
