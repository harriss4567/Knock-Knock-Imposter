"""Exploration probe: talk to the bus driver through a live Higgs avatar.

Answers the open questions in the plan before we write production code:
  1. How "impostor-like" is the raw avatar? (watch the viewer)
  2. Does it move while silent? (--idle none vs --idle silence)
  3. How fast does it stop after a barge-in? (logged: speech_started -> playback_finished)
  4. Without truncate, does the model keep the whole sentence and repeat it from the start?
     (--no-truncate, then ask "what did you just say at the end?"; the stored item is printed)

Run:  .venv/bin/python explore/probe.py --role impostor --no-truncate
Then open the printed viewer URL in Chrome and click Join.
"""

import argparse
import asyncio
import base64
import json
import os
import threading
import time
import webbrowser
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import certifi
import requests
import websockets
from livekit import api, rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput

ROOT = Path(__file__).resolve().parent.parent
ROOM = "probe"
AGENT_IDENTITY = "agent"
AVATAR_IDENTITY = "boson-avatar"
USER_IDENTITY = "player"
MAX_DURATION_SECONDS = 900
SAMPLE_RATE = 24000
VIEWER_PORT = 8765

BUS_DRIVER = (
    "You are Frank, a night-shift bus driver in your fifties. It is past midnight. "
    "The sun has made the days deadly; you are standing outside a stranger's door, "
    "knocking, asking to be let in until morning. Your bus broke down on Route 9 near "
    "the old water tower and you walked here. You are tired, sweaty and scared. "
    "Speak in short, natural spoken sentences, two or three at a time. Never mention being an AI."
)
ROLES = {
    "plain": "You are a friendly assistant. Keep answers short.",
    "human": BUS_DRIVER
    + " You are a real human. When the person inside interrupts you, answer their question "
    "directly, hesitate naturally ('uh, wait...'), and get more nervous the more they push.",
    "impostor": BUS_DRIVER
    + " Secretly you are not human; you are an imitation. You sound perfectly human, except for "
    "one habit: whenever you are interrupted, you pause, then calmly repeat the sentence you were "
    "saying from its very first word, exactly as before, before you answer anything.",
}


def load_env():
    # python.org's macOS Python ships without root certificates; use certifi's.
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))
    missing = [k for k in ("BOSON_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")


def livekit_token(identity, *, publish, subscribe, publish_on_behalf=None):
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=ROOM,
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


T0 = time.monotonic()


def log(*args):
    print(f"[{time.monotonic() - T0:7.2f}s]", *args, flush=True)


async def main(args):
    load_env()
    boson = {"Authorization": f"Bearer {os.environ['BOSON_API_KEY']}"}
    base = "https://api.boson.ai/v1"

    r = requests.post(
        f"{base}/avatar/sessions",
        headers=boson,
        json={
            "avatar_id": args.avatar,
            "transport": {
                "type": "livekit",
                "url": os.environ["LIVEKIT_URL"],
                "room_name": ROOM,
                "participant_token": livekit_token(
                    AVATAR_IDENTITY, publish=True, subscribe=False, publish_on_behalf=AGENT_IDENTITY
                ),
                "participant_identity": AVATAR_IDENTITY,
                "publisher_identity": AGENT_IDENTITY,
            },
            "max_duration_seconds": MAX_DURATION_SECONDS,
        },
        timeout=60,
    )
    if not r.ok:
        raise SystemExit(f"avatar session failed: {r.status_code} {r.text}")
    session = r.json()
    log("avatar session", session["id"], session["status"])

    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], livekit_token(AGENT_IDENTITY, publish=True, subscribe=True))
    to_avatar = DataStreamAudioOutput(
        room, destination_identity=AVATAR_IDENTITY, sample_rate=SAMPLE_RATE, wait_remote_track=rtc.TrackKind.KIND_AUDIO
    )

    # Serve the viewer over http://localhost: browsers block the mic on file:// pages.
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "explore"))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", VIEWER_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    viewer = f"http://localhost:{VIEWER_PORT}/viewer.html?" + urlencode(
        {"url": os.environ["LIVEKIT_URL"], "token": livekit_token(USER_IDENTITY, publish=True, subscribe=True)}
    )
    log("viewer:", viewer)
    webbrowser.open(viewer)

    try:
        async with websockets.connect("wss://api.boson.ai/v1/realtime", additional_headers=boson, max_size=None) as ws:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "model": "higgs-realtime",
                    "instructions": ROLES[args.role],
                    "audio": {
                        "input": {
                            "turn_detection": {"type": "server_vad", "threshold": args.threshold},
                            "transcription": {"model": "higgs-stt-3.1"},
                        },
                        "output": {"voice": args.voice},
                    },
                },
            }))

            async def forward_user_audio(track):
                async for event in rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1):
                    try:
                        await ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(bytes(event.frame.data)).decode(),
                        }))
                    except websockets.ConnectionClosed:
                        return

            @room.on("track_subscribed")
            def on_track(track, _publication, participant):
                if participant.identity != AVATAR_IDENTITY and track.kind == rtc.TrackKind.KIND_AUDIO:
                    log("forwarding mic from", participant.identity)
                    asyncio.create_task(forward_user_audio(track))

            for participant in room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.track is not None:
                        on_track(publication.track, publication, participant)

            state = {"speaking": False, "item_id": None, "sent_ms": 0.0, "barge_at": None}

            @to_avatar.on("playback_finished")
            def on_played(ev):
                state["speaking"] = False
                pos_ms = int(ev.playback_position * 1000)
                if state["barge_at"] is not None:
                    log(f"playback_finished after barge-in: {(time.monotonic() - state['barge_at']) * 1000:.0f} ms, "
                        f"played {pos_ms} of {state['sent_ms']:.0f} ms sent (interrupted={ev.interrupted})")
                    state["barge_at"] = None
                    if state["item_id"]:
                        if not args.no_truncate:
                            asyncio.create_task(ws.send(json.dumps({
                                "type": "conversation.item.truncate",
                                "item_id": state["item_id"], "content_index": 0, "audio_end_ms": pos_ms,
                            })))
                        asyncio.create_task(ws.send(json.dumps({
                            "type": "conversation.item.retrieve", "item_id": state["item_id"],
                        })))
                else:
                    log(f"playback_finished: {pos_ms} ms")

            async def idle_silence():
                # 20 ms of zeros, continuously, while the avatar is not speaking.
                frame = bytes(SAMPLE_RATE // 50 * 2)
                while True:
                    if not state["speaking"]:
                        await to_avatar.capture_frame(rtc.AudioFrame(frame, SAMPLE_RATE, 1, len(frame) // 2))
                    await asyncio.sleep(0.02)

            if args.idle == "silence":
                asyncio.create_task(idle_silence())

            async for message in ws:
                event = json.loads(message)
                kind = event["type"]
                if kind == "response.output_audio.delta":
                    pcm = base64.b64decode(event["delta"])
                    if state["item_id"] != event["item_id"]:
                        state["item_id"] = event["item_id"]
                        state["sent_ms"] = 0.0
                    state["sent_ms"] += len(pcm) / 2 / SAMPLE_RATE * 1000
                    await to_avatar.capture_frame(rtc.AudioFrame(pcm, SAMPLE_RATE, 1, len(pcm) // 2))
                    state["speaking"] = True
                elif kind == "response.done":
                    to_avatar.flush()
                    log("response.done", event.get("response", {}).get("status"))
                elif kind == "input_audio_buffer.speech_started":
                    log("player started speaking")
                    if state["speaking"]:
                        state["barge_at"] = time.monotonic()
                        to_avatar.clear_buffer()
                        state["speaking"] = False
                elif kind == "conversation.item.input_audio_transcription.completed":
                    log("PLAYER:", event.get("transcript"))
                elif kind == "response.output_audio_transcript.done":
                    log("VISITOR (generated):", event["transcript"])
                elif kind == "conversation.item.retrieved":
                    content = event.get("item", {}).get("content", [])
                    stored = " | ".join(c.get("transcript") or c.get("text") or "" for c in content)
                    log("STORED after barge-in:", stored)
                elif kind in ("conversation.item.truncated", "session.updated", "error"):
                    log(kind, json.dumps(event)[:400])
    finally:
        requests.delete(f"{base}/avatar/sessions/{session['id']}", headers=boson, timeout=30)
        await room.disconnect()
        log("released avatar session")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--role", choices=ROLES, default="human")
    p.add_argument("--avatar", default="builtin_james_v1")
    p.add_argument("--voice", default="jake")
    p.add_argument("--no-truncate", action="store_true", help="skip conversation.item.truncate after barge-in")
    p.add_argument("--idle", choices=["none", "silence"], default="none", help="what to send the avatar while not speaking")
    p.add_argument("--threshold", type=float, default=0.85, help="server VAD threshold")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        pass
