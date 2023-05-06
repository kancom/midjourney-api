This project contains two apps:

1. discord bot workers
2. API to interact with the bots

Prerequisites:

* redis
* .env file, with
  REDIS_DSN="redis://127.0.0.1:5002/0"
* discord_ids.csv file (obsolete content)
  id,bot_access_token,user_access_token,channel_id,server_id,high_priority,human_name,proxy,pool
  8,MTEwMDUwMDczNTk5MDQ0ODI5OQ.GCojrG.II3xh2ZfCpHApW7cF66amriEA8_U5S94zlLtj0,MTA1Njg1MDA1NDM3MzE4MzUyOA.G7ir-4.g99a8tsRDsnEN7u_sFHb_dseD8m0luxRAxtnco,1100500115719999571,1100500115719999568,false,oktaviyak871605135@autorambler.ru,192.241.68.95:8000:EAbmLe:gedSYj,common
* pipenv
* install deps: `pipenv install`

Launch

bots:

* `pipenv run python src/vapi/bot_main[__dev].py`

api:

* `pipenv run python src/vapi/api_main.py`. then you can open http://127.0.0.1:8000/docs to check openapi specs
