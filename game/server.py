"""Game server: serves the page and puts a visitor at the door on request.

Run:  .venv/bin/python -m game.server   then open http://localhost:8000
"""

import asyncio
import logging
import os
import random
import uuid

from aiohttp import web

from .characters import FRANK
from .env import ROOT, load_env
from .visitor import Visitor

log = logging.getLogger("server")
PORT = 8000
WEB = ROOT / "web"


class Game:
    def __init__(self):
        self.visitor: Visitor | None = None

    async def end_visit(self):
        if self.visitor:
            visitor, self.visitor = self.visitor, None
            await visitor.close()

    async def start_visit(self, request):
        await self.end_visit()  # one visitor at a time; avatar slots cost money
        visitor = Visitor(FRANK, is_impostor=random.random() < 0.5, room_name=f"door-{uuid.uuid4().hex[:8]}")
        self.visitor = visitor
        asyncio.create_task(visitor.run())
        return web.json_response({"url": os.environ["LIVEKIT_URL"], "token": visitor.player_token()})

    async def stop_visit(self, request):
        await self.end_visit()
        return web.json_response({"ok": True})


async def index(request):
    return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("livekit").setLevel(logging.WARNING)
    load_env()
    game = Game()
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.post("/api/visit", game.start_visit),
        web.post("/api/visit/end", game.stop_visit),
        web.static("/static", WEB),
    ])

    async def on_shutdown(_app):
        await game.end_visit()  # release the avatar slot on Ctrl-C

    app.on_shutdown.append(on_shutdown)
    print(f"Knock Knock Imposter: http://localhost:{PORT}", flush=True)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
