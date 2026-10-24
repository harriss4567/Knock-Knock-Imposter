"""Game server: serves the page and puts a visitor at the door on request.

Run:  .venv/bin/python -m game.server   then open http://localhost:8000
"""

import asyncio
import logging
import os
import uuid

from aiohttp import web

from .env import ROOT, load_env
from .night import Night
from .visitor import Visitor

log = logging.getLogger("server")
PORT = 8000
WEB = ROOT / "web"


class Game:
    def __init__(self):
        self.night = Night()
        self.visitor: Visitor | None = None

    async def end_visit(self):
        if self.visitor:
            visitor, self.visitor = self.visitor, None
            await visitor.close()

    async def start_night(self, request):
        await self.end_visit()
        self.night = Night()
        return web.json_response({"visitors": len(self.night.visits)})

    async def start_visit(self, request):
        await self.end_visit()  # one visitor at a time; avatar slots cost money
        try:
            visit = self.night.next_visit()
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)
        if visit is None:
            return web.json_response({"error": "the night is over"}, status=409)
        return self._open_visit(visit)

    async def retry_visit(self, request):
        """The same visitor again, after their session failed to start."""
        await self.end_visit()
        visit = self.night.current
        if visit is None or visit.opened is not None:
            return web.json_response({"error": "nobody is waiting at the door"}, status=409)
        return self._open_visit(visit)

    def _open_visit(self, visit):
        visitor = Visitor(visit.character, visit.is_impostor, room_name=f"door-{uuid.uuid4().hex[:8]}")
        self.visitor = visitor
        asyncio.create_task(visitor.run())
        return web.json_response({
            "url": os.environ["LIVEKIT_URL"], "token": visitor.player_token(),
            "number": self.night.index + 1, "visitors": len(self.night.visits),
            "mouth_shadow": visit.character.mouth_shadow,
        })

    async def decide(self, request):
        opened = bool((await request.json())["opened"])
        try:
            visit = self.night.decide(opened)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)
        if self.visitor:
            await self.visitor.decide(opened)  # their last words play before this returns
        await self.end_visit()
        night = self.night
        return web.json_response({
            "name": visit.character.name, "impostor": visit.is_impostor, "opened": opened,
            "finished": night.finished, "humans_in": night.humans_in, "impostors_in": night.impostors_in,
            "won": night.won,
        })

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
        web.post("/api/night", game.start_night),
        web.post("/api/visit", game.start_visit),
        web.post("/api/visit/retry", game.retry_visit),
        web.post("/api/visit/decide", game.decide),
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
