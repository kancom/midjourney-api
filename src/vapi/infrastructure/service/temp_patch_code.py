import json
import requests

def get_data_from_midjourney():
    url = "https://discord.com/api/v9/channels/1008571141507534928/application-commands/search?type=1&limit=25&include_applications=true"
#    headers = {'Authorization': self._user_access_token}
    headers = {"Authorization": "MTA1Njg1MDA1NDM3MzE4MzUyOA.G7ir-4.g99a8tsRDsnEN7u_sFHb_dseD8m0luxRAxtnco"}
    response = requests.request("GET", url, headers=headers)
    response.raise_for_status()
    return json.loads(response.text)["application_commands"]

def extract_initial_values(data_list, description):
    for element in range(len(data_list)):
        if data_list[element]["description"] == description:
            return {"application_id": data_list[element]["application_id"],
                    "version": data_list[element]["version"],
                    "id": data_list[element]["id"]}
    raise ValueError("No application command found")

if __name__ == '__main__':
# Вызов при старте:
    global raw_data
    raw_data = get_data_from_midjourney()
# Указываем в каждой функции. На примере с send_prompt:
    description = "Create images with Midjourney"
    values = extract_initial_values(raw_data, description)
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
