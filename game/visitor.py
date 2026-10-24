"""One visitor at the door: a Higgs avatar in a LiveKit room, voiced by a Higgs Realtime conversation.

Audio path: player mic -> room -> this agent -> Higgs Realtime -> this agent -> avatar -> room -> player.
The avatar only lip-syncs what we send it, so controlling the audio controls the face.
"""

import asyncio
import base64
import json
import logging
import os
import time
from datetime import timedelta

import aiohttp
import websockets
from livekit import api, rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput

from .characters import FAREWELL, OPENING, Character, voice_for

log = logging.getLogger("visitor")

BOSON_BASE_URL = "https://api.boson.ai/v1"
BOSON_REALTIME_URL = "wss://api.boson.ai/v1/realtime"
AGENT_IDENTITY = "visitor-agent"
AVATAR_IDENTITY = "visitor-avatar"
PLAYER_IDENTITY = "player"
SAMPLE_RATE = 24000
MAX_DURATION_SECONDS = 6 * 60
# Boson sometimes refuses a new avatar session right after the last one was released.
AVATAR_ATTEMPTS = 3
AVATAR_RETRY_SECONDS = 2
# Silence that ends the player's turn. Higgs defaults to 500 ms; shorter replies faster but may cut
# the player off mid-pause.
SILENCE_MS = 300
# Higher varies the wording more; 0.6 looped on the same sentence.
TEMPERATURE = 0.8
# The visitor's recent lines, fed back into its instructions so it stops repeating itself.
RECENT_LINES = 6

# Pressure rises with interruptions: level 2 at the 2nd interruption, level 3 at the 3rd.
PRESSURE_AT_INTERRUPTIONS = {2: 2, 3: 3}
# Impostor tell at pressure 3: after a barge-in it says what it was saying again, from the first word,
# before answering. Only replayed if it had spoken at least MIN_REPLAY_MS.
REPLAY_PAUSE_MS = 700
MIN_REPLAY_MS = 800
# Once the door is decided: how long to wait for a cancelled reply to end, then for the last words to play.
FINISH_REPLY_TIMEOUT = 5
# A reply the server may have just triggered from the player's last words, before we hear about it.
LATE_REPLY_MS = 300
FAREWELL_TIMEOUT = 25


def pressure_for(interruptions: int) -> int:
    return max([1] + [level for count, level in PRESSURE_AT_INTERRUPTIONS.items() if interruptions >= count])


def livekit_token(room: str, identity: str, *, publish: bool, subscribe: bool, publish_on_behalf: str | None = None):
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=publish,
                can_subscribe=subscribe,
                can_publish_data=True,
                can_publish_sources=["camera", "microphone"] if publish else None,
            )
        )
        .with_ttl(timedelta(seconds=MAX_DURATION_SECONDS + 300))
    )
    if publish_on_behalf:
        token = token.with_kind("agent").with_attributes({"lk.publish_on_behalf": publish_on_behalf})
    return token.to_jwt()


def pcm_ms(n_bytes: int) -> float:
    return n_bytes / 2 / SAMPLE_RATE * 1000


def ms_bytes(ms: float) -> int:
    return int(ms) * SAMPLE_RATE // 1000 * 2


class Visitor:
    def __init__(self, character: Character, is_impostor: bool, room_name: str):
        self.character = character
        self.is_impostor = is_impostor
        self.room_name = room_name
        self.done = asyncio.Event()
        self._boson = {"Authorization": f"Bearer {os.environ['BOSON_API_KEY']}"}
        self._session_id = None
        self._room = rtc.Room()
        self._ws = None
        self._to_avatar = None
        # Speech state for the item currently being played by the avatar.
        self._speaking = False
        self._item_id = None
        self._item_pcm = bytearray()
        self._item_started = 0.0
        self._replay = None
        self._interruptions = 0
        self._voice = voice_for(character, 1)
        self._greeted = False
        self._player_stopped = None  # when the player last stopped talking, to measure reply latency
        self._said: list[str] = []
        self._listening = True  # False once the door is decided: the player's mic stops reaching the model
        self._active_response = None  # id of the response being generated
        self._idle = asyncio.Event()  # no response being generated
        self._idle.set()
        self._decided = False  # from then on, only the farewell response is played
        self._farewell_id = None
        self._farewell_generated = False  # all its audio is with the avatar
        self._farewell_heard = asyncio.Event()

    def player_token(self) -> str:
        return livekit_token(self.room_name, PLAYER_IDENTITY, publish=True, subscribe=True)

    @property
    def pressure(self) -> int:
        return pressure_for(self._interruptions)

    async def run(self):
        try:
            # Join first, so a failure can still be reported to the player's page.
            await self._room.connect(
                os.environ["LIVEKIT_URL"], livekit_token(self.room_name, AGENT_IDENTITY, publish=True, subscribe=True)
            )
            await self._start_avatar()
            self._to_avatar = DataStreamAudioOutput(
                self._room, destination_identity=AVATAR_IDENTITY, sample_rate=SAMPLE_RATE,
                wait_remote_track=rtc.TrackKind.KIND_AUDIO,
            )
            self._to_avatar.on("playback_finished", self._on_playback_finished)
            self._room.on("participant_disconnected", self._on_participant_left)
            async with websockets.connect(BOSON_REALTIME_URL, additional_headers=self._boson, max_size=None) as ws:
                self._ws = ws
                await self._send({
                    "type": "session.update",
                    "session": {
                        "model": "higgs-realtime",
                        "instructions": self.character.instructions,
                        "temperature": TEMPERATURE,
                        "audio": {
                            "input": {
                                "turn_detection": {"type": "server_vad", "threshold": 0.85, "silence_duration_ms": SILENCE_MS},
                                "transcription": {"model": "higgs-stt-3.1"},
                            },
                            "output": {"voice": self._voice},
                        },
                    },
                })
                # Only now: forwarding the mic needs the WebSocket.
                self._room.on("track_subscribed", self._on_track)
                for participant in self._room.remote_participants.values():
                    for publication in participant.track_publications.values():
                        if publication.track is not None:
                            self._on_track(publication.track, publication, participant)
                events = asyncio.create_task(self._handle_events())
                await asyncio.wait([events, asyncio.create_task(self.done.wait())], return_when=asyncio.FIRST_COMPLETED)
                events.cancel()
        except Exception:
            log.exception("visitor session failed")
            await self._publish({"type": "error", "message": "The visitor is gone. (Session failed; see server log.)"})
        finally:
            await self.close()

    async def close(self):
        self.done.set()
        if self._session_id:
            session_id, self._session_id = self._session_id, None
            async with aiohttp.ClientSession(headers=self._boson) as http:
                async with http.delete(f"{BOSON_BASE_URL}/avatar/sessions/{session_id}") as r:
                    log.info("released avatar %s (%s)", session_id, r.status)
        await self._room.disconnect()

    async def _start_avatar(self):
        body = {
            "avatar_id": self.character.avatar_id,
            "transport": {
                "type": "livekit",
                "url": os.environ["LIVEKIT_URL"],
                "room_name": self.room_name,
                "participant_token": livekit_token(
                    self.room_name, AVATAR_IDENTITY, publish=True, subscribe=False, publish_on_behalf=AGENT_IDENTITY
                ),
                "participant_identity": AVATAR_IDENTITY,
                "publisher_identity": AGENT_IDENTITY,
            },
            "max_duration_seconds": MAX_DURATION_SECONDS,
        }
        async with aiohttp.ClientSession(headers=self._boson) as http:
            for attempt in range(1, AVATAR_ATTEMPTS + 1):
                async with http.post(f"{BOSON_BASE_URL}/avatar/sessions", json=body) as r:
                    if r.status < 400:
                        self._session_id = (await r.json())["id"]
                        break
                    error = f"avatar session failed: {r.status} {await r.text()}"
                if attempt == AVATAR_ATTEMPTS:
                    raise RuntimeError(error)
                log.warning("%s; retrying", error)
                await asyncio.sleep(AVATAR_RETRY_SECONDS)
        log.info("avatar %s in room %s (%s, %s)", self._session_id, self.room_name, self.character.key,
                 "IMPOSTOR" if self.is_impostor else "human")

    def _on_track(self, track, _publication, participant):
        if participant.identity == PLAYER_IDENTITY and track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(self._forward_player_audio(track))
            if not self._greeted:
                self._greeted = True
                # The visitor knocked; they speak first. Per-response instructions replace the session's,
                # so the character has to be included.
                asyncio.create_task(self._send({"type": "response.create", "response": {
                    "instructions": f"{self.character.instructions}\n\n{OPENING}"}}))

    def _on_participant_left(self, participant):
        if participant.identity == PLAYER_IDENTITY:
            log.info("player left")
            self.done.set()

    async def _forward_player_audio(self, track):
        async for event in rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1):
            if not self._listening:
                continue
            try:
                await self._send({"type": "input_audio_buffer.append",
                                  "audio": base64.b64encode(bytes(event.frame.data)).decode()})
            except websockets.ConnectionClosed:
                return

    def _on_playback_finished(self, ev):
        self._speaking = False
        if self._farewell_generated:
            self._farewell_heard.set()
        if ev.interrupted and self._item_id:
            # Make the stored conversation match what the player actually heard.
            asyncio.create_task(self._send({
                "type": "conversation.item.truncate", "item_id": self._item_id,
                "content_index": 0, "audio_end_ms": int(ev.playback_position * 1000),
            }))

    async def _on_barge_in(self):
        now = time.monotonic()
        self._interruptions += 1
        if self.is_impostor and self.pressure >= 3:
            # Decided now (not on playback_finished) so the replay always precedes the answer.
            played_ms = min(pcm_ms(len(self._item_pcm)), (now - self._item_started) * 1000)
            if played_ms >= MIN_REPLAY_MS:
                self._replay = bytes(self._item_pcm[: ms_bytes(played_ms)])
        self._to_avatar.clear_buffer()
        self._speaking = False
        await self._publish({"type": "pressure", "level": self.pressure})

    async def _play(self, pcm: bytes):
        await self._to_avatar.capture_frame(rtc.AudioFrame(pcm, SAMPLE_RATE, 1, len(pcm) // 2))

    async def _handle_events(self):
        async for message in self._ws:
            event = json.loads(message)
            kind = event["type"]
            if kind == "response.created":
                self._active_response = event["response"]["id"]
                self._idle.clear()
                if (event["response"].get("metadata") or {}).get("farewell"):
                    self._farewell_id = event["response"]["id"]
            elif kind == "response.output_audio.delta":
                if self._decided and event.get("response_id") != self._farewell_id:
                    continue
                pcm = base64.b64decode(event["delta"])
                if self._item_id != event["item_id"]:
                    if self._player_stopped:
                        log.info("reply audio %.0f ms after player stopped (+%d ms silence detection)",
                                 (time.monotonic() - self._player_stopped) * 1000, SILENCE_MS)
                        self._player_stopped = None
                    self._item_id = event["item_id"]
                    self._item_pcm = bytearray()
                    self._item_started = time.monotonic()
                    if self._replay:
                        replay, self._replay = self._replay, None
                        await self._play(bytes(ms_bytes(REPLAY_PAUSE_MS)) + replay)
                self._item_pcm += pcm
                await self._play(pcm)
                self._speaking = True
            elif kind == "response.done":
                self._active_response = None
                self._idle.set()
                if self._decided:
                    if event["response"]["id"] == self._farewell_id:
                        self._to_avatar.flush()
                        self._farewell_generated = True
                    continue
                self._to_avatar.flush()
                await self._after_response()
            elif kind == "input_audio_buffer.speech_started":
                if self._speaking:
                    await self._on_barge_in()
            elif kind == "input_audio_buffer.speech_stopped":
                self._player_stopped = time.monotonic()
            elif kind == "conversation.item.input_audio_transcription.completed":
                log.info("PLAYER: %s", event.get("transcript"))
            elif kind == "response.output_audio_transcript.done":
                log.info("VISITOR: %s", event["transcript"])
                self._said.append(event["transcript"])
            elif kind == "error":
                log.error("realtime error: %s", event)

    async def _after_response(self):
        # Only between responses: changing config mid-response can break the session.
        session = {"instructions": self.character.instructions + "\n\nWHAT YOU HAVE ALREADY SAID. Don't reuse "
                   "these lines or their phrases on your own; say something new. But if they ask you to repeat "
                   "something or ask again (your name, what happened to you tonight), answer it: the facts stay "
                   "exactly the same, the words can differ.\n"
                   + "\n".join(f"- {line}" for line in self._recent_lines())}
        voice = voice_for(self.character, self.pressure)
        if voice != self._voice:
            self._voice = voice
            session["audio"] = {"output": {"voice": voice}}
            log.info("voice -> pressure %d", self.pressure)
        await self._send({"type": "session.update", "session": session})

    def _recent_lines(self) -> list[str]:
        # The opening plea too: it's the line they most like to fall back on.
        return self._said[:1] + self._said[1:][-RECENT_LINES:]

    async def decide(self, opened: bool):
        """The player opened the door or kept it shut. The visitor stops listening and says its last words;
        returns once they have played (or timed out)."""
        if self.done.is_set() or self._ws is None:
            return
        self._listening = False
        self._decided = True
        self._replay = None
        # Silence whatever they were saying, and end that audio segment: the avatar discards the rest of a
        # cleared segment, so the last words have to start a new one.
        self._to_avatar.clear_buffer()
        self._to_avatar.flush()
        try:
            await self._send({"type": "input_audio_buffer.clear"})
            await asyncio.sleep(LATE_REPLY_MS / 1000)
            if self._active_response:
                # Config can't change mid-response, and the thankful voice needs a session.update.
                await self._send({"type": "response.cancel", "response_id": self._active_response})
                await asyncio.wait_for(self._idle.wait(), FINISH_REPLY_TIMEOUT)
            if opened and self.character.thankful_voice:
                await self._send({"type": "session.update",
                                  "session": {"audio": {"output": {"voice": self.character.thankful_voice}}}})
            farewell = FAREWELL[(opened, self.is_impostor)]
            # Stated before and after the character: per-response instructions are easy for it to ignore.
            await self._send({"type": "response.create", "response": {
                "metadata": {"farewell": "true"},
                "instructions": f"{farewell}\n\n{self.character.instructions}\n\nNOW: {farewell}"}})
            await asyncio.wait_for(self._farewell_heard.wait(), FAREWELL_TIMEOUT)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            log.warning("visitor's last words cut short")

    async def _send(self, event: dict):
        await self._ws.send(json.dumps(event))

    async def _publish(self, event: dict):
        """Game events to the player's page over the LiveKit data channel."""
        if self._room.isconnected():
            await self._room.local_participant.publish_data(json.dumps(event).encode(), reliable=True, topic="game")
