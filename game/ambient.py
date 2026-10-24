"""The small sounds a frightened human makes while nobody is talking: sighs, sniffs, a shaky breath.

Generated once per voice with Higgs TTS 3's sound-effect tags, then played through the avatar so the face
moves with them. Impostors don't get them: they stand perfectly still and silent.
"""

import io
import logging
import os
import wave

import aiohttp

log = logging.getLogger("ambient")

SPEECH_URL = "https://api.boson.ai/v1/audio/speech"
SAMPLE_RATE = 24000
# Wordless only: anything they say must come from the conversation.
SOUNDS = {
    "sigh": "<|emotion:helplessness|><|sfx:sigh|>Hhhhh.",
    "sniff": "<|emotion:sadness|><|sfx:sniff|>",
    "breath": "<|emotion:fear|><|sfx:sigh|>Hhh... <|prosody:pause|><|sfx:sniff|>",
}

_cache: dict[str, list[bytes]] = {}


async def sounds_for(voice: str) -> list[bytes]:
    """24 kHz mono 16-bit PCM clips in this voice. Empty if generation fails: the game works without them."""
    if voice in _cache:
        return _cache[voice]
    clips = []
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {os.environ['BOSON_API_KEY']}"}) as http:
        for name, text in SOUNDS.items():
            body = {"model": "higgs-tts-3", "input": text, "voice": voice, "response_format": "wav"}
            try:
                async with http.post(SPEECH_URL, json=body) as r:
                    r.raise_for_status()
                    with wave.open(io.BytesIO(await r.read())) as w:
                        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                            raise ValueError(f"unexpected format {w.getparams()}")
                        clips.append(w.readframes(w.getnframes()))
            except Exception as e:
                log.warning("couldn't make the %s sound: %s", name, e)
    if clips:
        _cache[voice] = clips
    return clips
