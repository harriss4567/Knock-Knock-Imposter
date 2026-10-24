"""Clone a voice from a recording, for use as `--voice voice_<id>` in probe.py.

Record 10-30 s of someone *performing* Frank (desperate, breathless, shaky): Higgs copies the
delivery of the reference, not just the timbre. Voice Memos / QuickTime files work; they are
converted to 24 kHz mono WAV with macOS's afconvert first.

Run:  .venv/bin/python explore/clone_voice.py recording.m4a "exact words spoken in the recording"
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from probe import load_env


def main(audio_path, ref_text):
    load_env()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "ref.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", audio_path, str(wav)], check=True)
        with wav.open("rb") as f:
            r = requests.post(
                "https://api.boson.ai/v1/audio/voices",
                headers={"Authorization": f"Bearer {os.environ['BOSON_API_KEY']}"},
                files={"ref_audio": ("ref.wav", f, "audio/wav")},
                data={"ref_text": ref_text, "title": Path(audio_path).stem},
                timeout=120,
            )
    if not r.ok:
        raise SystemExit(f"clone failed: {r.status_code} {r.text}")
    body = r.json()
    voice = body.get("voice_id") or body["voice"]  # the API returns voice_id; the docs say voice
    print(f"voice: {voice}\nnow run: .venv/bin/python explore/probe.py --role random --voice {voice}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
