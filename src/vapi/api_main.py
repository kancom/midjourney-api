import sys
from typing import List, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vapi.api import endpoint as api
from vapi.wiring import Container

origins = [
    "http://localhost:3000",
]

desc = """Unofficial MidJourney API
For enquiries/По вопросам: 
"""


tags_metadata = [
    {
        "name": "Auth",
        "description": "Authentication",
    },
    {
        "name": "ImageSet",
        "description": "MidJorney-related endpoints",
    },
    {
        "name": "Profile",
        "description": "Profile manipulation",
    },
    {
        "name": "Package",
        "description": "Package manipulation",
    },
]


def main(argv: Optional[List[str]]):
    container = Container()
    # settings = Settings(**container.settings.provided())
    # api_url = settings.api_v1_str

    app = FastAPI(
        # title=settings.project_name,
        # openapi_tags=tags_metadata,
        # description=desc,
        # contact={"url": settings.contact_url, "name": "Telegram contact"},
        # openapi_url=f"{api_url}/openapi.json",
        # docs_url=f"{api_url}/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api.router)
    uvicorn.run(app, host="0.0.0.0", port=8123)


if __name__ == "__main__":
    main(sys.argv[1:])
