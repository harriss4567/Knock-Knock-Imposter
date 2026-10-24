"""One visitor at the door: a Higgs avatar in a LiveKit room, voiced by a Higgs Realtime conversation.

Audio path: player mic -> room -> this agent -> Higgs Realtime -> this agent -> avatar -> room -> player.
The avatar only lip-syncs what we send it, so controlling the audio controls the face.
"""

import asyncio
import base64
import json
import logging
import os
import random
import time
from datetime import timedelta

import aiohttp
import websockets
from livekit import api, rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput

from . import ambient
from .characters import FAREWELL, NUDGE, OPENING, Character, voice_for
from .delivery import IMPOSTOR_FLAT_FROM, TAG_RULES, Mood, asked_before, deliver, leading_tags, spoken

log = logging.getLogger("visitor")

# A line's length until TTS 3 has finished it and the real length is known.
EST_MS_PER_CHAR = 60
# The page gives each visit 3 minutes; in the last one the visitor may lose its temper sooner (Mood).
LATE_SECONDS = 120

BOSON_BASE_URL = "https://api.boson.ai/v1"
BOSON_REALTIME_URL = "wss://api.boson.ai/v1/realtime"
AGENT_IDENTITY = "visitor-agent"
AVATAR_IDENTITY = "visitor-avatar"
PLAYER_IDENTITY = "player"
SAMPLE_RATE = 24000
MAX_DURATION_SECONDS = 6 * 60
# Boson sometimes refuses a new avatar session right after the last one was released.
AVATAR_ATTEMPTS = 5
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
# Nobody has spoken for this long: the visitor bangs on the door and calls out.
QUIET_NUDGE_SECONDS = 12
# A human sighs or sniffs this many seconds into a silence, and again after another such gap.
AMBIENT_GAP_SECONDS = (4, 8)


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
        # On (default): Realtime only writes each line, with delivery tags, and Higgs TTS 3 speaks it in one voice,
        # so the visitor can sound scared, angry or in tears (see delivery.py). EMOTION_TTS=0: Realtime speaks.
        # Read here, not at import: the server loads .env after importing this module.
        self._emotion_tts = os.environ.get("EMOTION_TTS", "1") != "0"
        self._instructions = character.instructions(is_impostor) + (f"\n\n{TAG_RULES}" if self._emotion_tts else "")
        self._session_instructions = self._instructions  # plus what they've already said; see _after_response
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
        self._player_talking = False
        self._player_spoke = False  # ever, this visit
        self._quiet_since = None  # when both sides last fell silent
        self._sounds: list[bytes] = []  # sighs and sniffs; humans only
        self._ambient = False  # one is playing: it isn't speech, so it isn't interrupted or truncated
        self._next_sound_at = 0.0
        # TTS mode: the text of each reply being written, the TTS 3 task speaking a line, and the line's words,
        # timed across its audio to work out how much of it the player heard.
        self._http = None
        self._replies: dict[str, tuple[str, str]] = {}  # response id -> (item id, text so far)
        self._tts_task = None
        self._line_tags = ""
        self._line_words: list[str] = []
        self._line_ms = 0.0
        self._answer_ms = 0.0  # where the line starts in the item's audio (after a replay)
        # TTS mode: the visitor's mood, and the player's words it reacts to (their latest, not yet answered).
        self._mood = Mood()
        self._first_words = None  # when the visitor started speaking: the page's clock starts about then
        self._player_said: list[str] = []
        self._player_line = ""
        self._player_repeated = False

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
            if self._emotion_tts:
                self._http = aiohttp.ClientSession(headers=self._boson)  # one connection for every line
            async with websockets.connect(BOSON_REALTIME_URL, additional_headers=self._boson, max_size=None) as ws:
                self._ws = ws
                session = {
                    "model": "higgs-realtime",
                    "instructions": self._instructions,
                    "temperature": TEMPERATURE,
                    "audio": {
                        "input": {
                            "turn_detection": {"type": "server_vad", "threshold": 0.85, "silence_duration_ms": SILENCE_MS},
                            "transcription": {"model": "higgs-stt-3.1"},
                        },
                        "output": {"voice": self._voice},
                    },
                }
                if self._emotion_tts:
                    session["output_modalities"] = ["text"]  # either text or audio, never both
                await self._send({"type": "session.update", "session": session})
                # Only now: forwarding the mic needs the WebSocket.
                self._room.on("track_subscribed", self._on_track)
                for participant in self._room.remote_participants.values():
                    for publication in participant.track_publications.values():
                        if publication.track is not None:
                            self._on_track(publication.track, publication, participant)
                events = asyncio.create_task(self._handle_events())
                if not self.is_impostor:
                    asyncio.create_task(self._load_sounds())
                quiet = asyncio.create_task(self._watch_quiet())
                await asyncio.wait([events, asyncio.create_task(self.done.wait())], return_when=asyncio.FIRST_COMPLETED)
                events.cancel()
                quiet.cancel()
        except Exception:
            log.exception("visitor session failed")
            await self._publish({"type": "error", "message": "The visitor is gone. (Session failed; see server log.)"})
        finally:
            await self.close()

    async def close(self):
        self.done.set()
        self._stop_tts()
        if self._http:
            http, self._http = self._http, None
            await http.close()
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
                    "instructions": f"{self._instructions}\n\n{OPENING}"}}))

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
        if self._ambient:
            self._ambient = False
            return
        self._speaking = False
        if not self._player_talking:
            self._quiet_since = time.monotonic()
        if self._farewell_generated:
            self._farewell_heard.set()
        if ev.interrupted and self._item_id and self._emotion_tts:
            item_id, self._item_id = self._item_id, None  # only once per line
            heard = self._heard(ev.playback_position * 1000)
            # Once the door is decided, only the farewell matters; leave the conversation alone.
            if heard is not None and not self._decided:
                asyncio.create_task(self._keep_heard(item_id, heard))
        elif ev.interrupted and self._item_id:
            # Make the stored conversation match what the player actually heard.
            asyncio.create_task(self._send({
                "type": "conversation.item.truncate", "item_id": self._item_id,
                "content_index": 0, "audio_end_ms": int(ev.playback_position * 1000),
            }))

    async def _on_barge_in(self):
        now = time.monotonic()
        self._interruptions += 1
        log.info("interrupted #%d (pressure %d)", self._interruptions, self.pressure)
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

    async def _start_item(self, item_id: str):
        """A reply's first audio: reset the item's state, and play a pending replay ahead of it."""
        if self._player_stopped:
            log.info("reply audio %.0f ms after player stopped (+%d ms silence detection)",
                     (time.monotonic() - self._player_stopped) * 1000, SILENCE_MS)
            self._player_stopped = None
        self._item_id = item_id
        self._item_pcm = bytearray()
        self._item_started = time.monotonic()
        self._answer_ms = 0.0
        if self._replay:
            replay, self._replay = self._replay, None
            log.info("replay: %.0f ms of the cut-off line", pcm_ms(len(replay)))
            await self._play(bytes(ms_bytes(REPLAY_PAUSE_MS)) + replay)
            self._answer_ms = REPLAY_PAUSE_MS + pcm_ms(len(replay))

    def _on_reply_done(self, response: dict):
        """TTS mode: Realtime has finished writing a reply. Speak it, unless the player can't hear it now."""
        item_id, text = self._replies.pop(response["id"], (None, ""))
        farewell = response["id"] == self._farewell_id
        if self._decided and not farewell:
            return
        if response.get("status") != "completed" or not spoken(text) or (self._player_talking and not farewell):
            # The server cancels a reply once the player speaks; one finished while they were already talking
            # is dropped too. Never heard, so it shouldn't stay in the conversation either.
            log.info("reply not spoken (%s): %s", response.get("status"), text)
            if item_id:
                asyncio.create_task(self._delete_item(item_id))
            if farewell:
                self._farewell_heard.set()  # nothing to wait for
            return
        if not farewell:
            late = self._first_words is not None and time.monotonic() - self._first_words >= LATE_SECONDS
            steered = self._mood.steer(text, player=self._player_line, repeated=self._player_repeated, late=late)
            if leading_tags(steered) != leading_tags(text):
                log.info("mood: %s -> %s", leading_tags(text) or "no tags", leading_tags(steered))
            text = steered
            self._player_line, self._player_repeated = "", False  # answered
        # An impostor's last words are always flat: FAREWELL makes them too calm, or cold.
        pressure = max(self.pressure, IMPOSTOR_FLAT_FROM) if farewell and self.is_impostor else self.pressure
        line = deliver(text, is_impostor=self.is_impostor, pressure=pressure)
        log.info("VISITOR: %s", line)
        self._said.append(spoken(line))
        self._stop_tts()
        self._tts_task = asyncio.create_task(self._speak(item_id, line, farewell))

    async def _speak(self, item_id: str, line: str, farewell: bool):
        """TTS mode: TTS 3 says the line, streamed to the avatar as it arrives, in the visitor's one voice."""
        body = {"model": "higgs-tts-3", "input": line, "voice": self._voice, "response_format": "pcm", "stream": True}
        asked = time.monotonic()
        started = False
        try:
            async with self._http.post(f"{BOSON_BASE_URL}/audio/speech", json=body) as r:
                if r.status >= 400:
                    raise RuntimeError(f"{r.status} {await r.text()}")
                odd = b""  # PCM16 comes in whole samples: a split byte waits for the next chunk
                async for chunk in r.content.iter_any():
                    chunk, odd = odd + chunk, b""
                    if len(chunk) % 2:
                        chunk, odd = chunk[:-1], chunk[-1:]
                    if not chunk:
                        continue
                    if not started:
                        started = True
                        log.info("TTS first audio after %.0f ms", (time.monotonic() - asked) * 1000)
                        if self._first_words is None:
                            self._first_words = time.monotonic()
                        await self._start_item(item_id)
                        self._start_line(line)
                    self._item_pcm += chunk
                    await self._play(chunk)
                    self._speaking = True
                    self._quiet_since = None
        except asyncio.CancelledError:
            if not started:
                await self._delete_item(item_id)
            elif not self._decided:  # decide() ends the segment itself
                # Cut off mid-stream: end the segment, or the avatar drops the next line with the rest of this one.
                self._to_avatar.flush()
            raise
        except Exception:
            log.exception("TTS failed; the rest of the line is skipped")
            if not started:
                await self._delete_item(item_id)
        if started:
            self._to_avatar.flush()
            self._line_ms = pcm_ms(len(self._item_pcm))  # the line's real length is known now
        if farewell:
            if started:
                self._farewell_generated = True
            else:
                self._farewell_heard.set()

    def _speaking_soon(self) -> bool:
        """TTS mode: a line's text is done but its audio hasn't reached the avatar yet."""
        return self._tts_task is not None and not self._tts_task.done() and not self._speaking

    def _stop_tts(self):
        if self._tts_task:
            self._tts_task.cancel()
            self._tts_task = None

    async def _delete_item(self, item_id: str):
        try:
            await self._send({"type": "conversation.item.delete", "item_id": item_id})
        except websockets.ConnectionClosed:
            pass

    def _start_line(self, line: str):
        """TTS mode: the line's words, timed at an estimated pace until TTS 3 has finished it."""
        words = spoken(line)
        self._line_tags = leading_tags(line)
        self._line_words = words.split()
        self._line_ms = len(words) * EST_MS_PER_CHAR

    def _word_times(self) -> list[float]:
        """When each word of the line starts, in ms into the item's audio: spread by length across the line."""
        chars = sum(len(word) + 1 for word in self._line_words) or 1
        times, at = [], 0
        for word in self._line_words:
            times.append(self._answer_ms + self._line_ms * at / chars)
            at += len(word) + 1
        return times

    def _heard(self, position_ms: float) -> str | None:
        """TTS mode: what the conversation should keep of the line the player cut off position_ms into the item's
        audio: its tags and the words the avatar had started saying ("" if none), or None if it got them all."""
        heard = [word for word, at in zip(self._line_words, self._word_times()) if at < position_ms]
        if len(heard) == len(self._line_words):
            return None
        return f"{self._line_tags}{' '.join(heard)}—" if heard else ""

    async def _keep_heard(self, item_id: str, heard: str):
        """TTS mode: text can't be truncated, so the cut-off line is replaced with the part the player heard."""
        log.info("player heard: %s", heard or "nothing")
        await self._delete_item(item_id)
        if heard:
            try:
                await self._send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "assistant", "content": [{"type": "text", "text": heard}]}})
            except websockets.ConnectionClosed:
                pass

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
                    await self._start_item(event["item_id"])
                self._item_pcm += pcm
                await self._play(pcm)
                self._speaking = True
                self._quiet_since = None
            elif kind == "response.output_text.delta":
                # TTS mode. The item id only comes with the deltas, not with output_text.done.
                if self._decided and event.get("response_id") != self._farewell_id:
                    continue
                item_id, text = self._replies.get(event["response_id"], (event["item_id"], ""))
                self._replies[event["response_id"]] = (item_id, text + event.get("delta", ""))
            elif kind == "response.done":
                self._active_response = None
                self._idle.set()
                if self._emotion_tts:
                    # Flushing and marking the farewell as generated wait until TTS 3 has spoken the line.
                    self._on_reply_done(event["response"])
                    if not self._decided:
                        await self._after_response()
                    continue
                if self._decided:
                    if event["response"]["id"] == self._farewell_id:
                        self._to_avatar.flush()
                        self._farewell_generated = True
                    continue
                self._to_avatar.flush()
                await self._after_response()
            elif kind == "input_audio_buffer.speech_started":
                self._player_talking = self._player_spoke = True
                self._quiet_since = None
                if self._emotion_tts:
                    self._stop_tts()  # first: nothing more reaches the avatar once the barge-in clears it
                if self._speaking:
                    await self._on_barge_in()
            elif kind == "input_audio_buffer.speech_stopped":
                self._player_talking = False
                self._player_stopped = self._quiet_since = time.monotonic()
            elif kind == "conversation.item.input_audio_transcription.completed":
                log.info("PLAYER: %s", event.get("transcript"))
                if self._emotion_tts:
                    self._player_line = event.get("transcript") or ""
                    self._player_repeated = asked_before(self._player_line, self._player_said)
                    self._player_said.append(self._player_line)
            elif kind == "response.output_audio_transcript.done":
                log.info("VISITOR: %s", event["transcript"])
                self._said.append(event["transcript"])
            elif kind == "error":
                log.error("realtime error: %s", event)

    async def _after_response(self):
        # Only between responses: changing config mid-response can break the session.
        self._session_instructions = (
            self._instructions + "\n\nWHAT YOU HAVE ALREADY SAID. Don't reuse these lines or their phrases on your "
            "own; say something new. But if they ask you to repeat something or ask again (your name, what happened "
            "to you tonight), answer it again in different words.\n"
            + "\n".join(f"- {line}" for line in self._recent_lines()))
        session = {"instructions": self._session_instructions}
        voice = voice_for(self.character, self.pressure)
        # TTS mode keeps one voice all visit: the emotion is in the delivery tags instead.
        if voice != self._voice and not self._emotion_tts:
            self._voice = voice
            session["audio"] = {"output": {"voice": voice}}
            log.info("voice -> pressure %d", self.pressure)
        await self._send({"type": "session.update", "session": session})

    async def _load_sounds(self):
        self._sounds = await ambient.sounds_for(voice_for(self.character, 1))
        log.info("%d ambient sounds ready", len(self._sounds))

    async def _watch_quiet(self):
        """While nobody talks, a human sighs and sniffs now and then. If the player stays quiet, the visitor
        bangs on the door and calls out, again after each silence."""
        while True:
            await asyncio.sleep(0.5)
            if not (self._quiet_since and not self._decided and self._idle.is_set() and not self._speaking
                    and not self._ambient and not self._speaking_soon()):
                continue
            now = time.monotonic()
            if self._next_sound_at < self._quiet_since:  # a new silence
                self._next_sound_at = self._quiet_since + random.uniform(*AMBIENT_GAP_SECONDS)
            if now - self._quiet_since < QUIET_NUDGE_SECONDS:
                if self._sounds and now >= self._next_sound_at:
                    self._next_sound_at = now + random.uniform(*AMBIENT_GAP_SECONDS)
                    self._ambient = True
                    await self._play(random.choice(self._sounds))
                    self._to_avatar.flush()
            else:
                self._quiet_since = None
                log.info("player quiet: banging on the door")
                await self._publish({"type": "bang"})
                await self._send({"type": "response.create", "response": {
                    "instructions": f"{self._session_instructions}\n\n{NUDGE[self._player_spoke]}"}})

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
        self._player_talking = False
        self._stop_tts()  # TTS mode: the line being spoken stops reaching the avatar
        # Silence whatever they were saying, and end that audio segment: the avatar discards the rest of a
        # cleared segment, so the last words have to start a new one.
        self._to_avatar.clear_buffer()
        self._to_avatar.flush()
        try:
            await self._send({"type": "input_audio_buffer.clear"})
            await asyncio.sleep(LATE_REPLY_MS / 1000)
            ended = True
            if self._active_response:
                # Config can't change mid-response, and the thankful voice needs a session.update.
                await self._send({"type": "response.cancel", "response_id": self._active_response})
                try:
                    await asyncio.wait_for(self._idle.wait(), FINISH_REPLY_TIMEOUT)
                except asyncio.TimeoutError:
                    # Say them anyway: a new response interrupts one still in flight.
                    log.warning("cancelled reply didn't end; sending last words anyway")
                    ended = False
            # TTS mode keeps the voice; the relief comes through the delivery tags.
            if opened and self.character.thankful_voice and ended and not self._emotion_tts:
                await self._send({"type": "session.update",
                                  "session": {"audio": {"output": {"voice": self.character.thankful_voice}}}})
            farewell = FAREWELL[(opened, self.is_impostor)]
            # Stated before and after the character: per-response instructions are easy for it to ignore.
            await self._send({"type": "response.create", "response": {
                "metadata": {"farewell": "true"},
                "instructions": f"{farewell}\n\n{self._instructions}\n\nNOW: {farewell}"}})
            await asyncio.wait_for(self._farewell_heard.wait(), FAREWELL_TIMEOUT)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            log.warning("visitor's last words cut short")

    async def _send(self, event: dict):
        await self._ws.send(json.dumps(event))

    async def _publish(self, event: dict):
        """Game events to the player's page over the LiveKit data channel."""
        if self._room.isconnected():
            await self._room.local_participant.publish_data(json.dumps(event).encode(), reliable=True, topic="game")
