# 🚪 Knock Knock Imposter

> *Knock knock.*
> *Who's there?*
> *Frank.*
> *Frank who?*
> *…that's a great question. Ask him what year he was born. Twice.*

**A voice-driven survival game where you talk, live, through a peephole to strangers begging for shelter from a deadly sunrise, and must work out which of them are human and which are AI impostors before you open the door.**

Built for the Boson AI hackathon on the Higgs APIs: **Higgs Realtime** (live voice conversation), **Higgs TTS 3** (emotional speech and voice cloning) and the **Higgs Avatar** (a live talking face).

---

## 🌅 The night

The sun has become deadly. Anyone outside after dawn burns. It's 2 a.m., and yours is the only house on the street with a light on.

Then someone knocks.

Three visitors will come to your door tonight: a night-shift bus driver whose bus broke down, a delivery rider whose e-bike battery died, and a widow coming home from her husband's grave. Each one is scared, sweating and pleading. Some of them are exactly who they say they are. Some of them are *something else wearing a voice*.

You have **three minutes** with each. Talk to them, interrupt them, grill them, or just listen to the silence. Then make the call: **unhook the chain** or **keep it bolted**.

At dawn you survive only if **more humans than impostors** are inside with you. Shut everyone out and you die alone. Let the wrong one in and… well. You let it in.

---

## 🎯 What problem is this solving?

AI voices and faces can now sound convincingly human, but most people have never actually *practised* telling them apart. Spotting synthetic speech is usually taught through dry warnings ("be careful of voice-cloning scams!") instead of hands-on experience.

Knock Knock Imposter turns that skill into a tense, replayable game. You learn by doing: asking, interrupting, listening for slips and watching the face, with real stakes on every decision.

## 👥 Who is it for?

- **Anyone curious about voice AI**, as a fun way to feel how good (and how imperfect) it has become.
- **Event and demo audiences**: one night takes about ten minutes and gets a crowd shouting advice at the door.
- **People who want sharper instincts against voice-cloning scams**, built through play rather than a lecture.

## ⚙️ How does it work?

### 🎭 A writer and an actor

Boson has two models. One is great at conversation but says every line flat. The other can't hold a conversation but can act out emotion. So we made the first one the **writer**: it listens, decides what to say, and marks each line with how it should feel, like *scared*, *angry* or *crying*. The second one is the **actor**: it performs that line in the same voice, and the avatar's face follows the voice.

```
your mic ─▶ LiveKit room ─▶ game server ─▶ Higgs Realtime: the writer
                                              (listens, writes the next line + how it should feel)
                                              │
                                              ▼
              your speakers ◀─ Higgs Avatar ◀─ Higgs TTS 3: the actor
              + peephole      (face follows     (performs it: scared, angry or in tears)
                               the voice)
```

- **Every word is improvised.** Each visitor is a character sheet (their life, their night, their facts) plus the rules of the world. Nothing they say is scripted.
- **You can interrupt.** Start talking and they stop mid-sentence. The conversation then remembers only the words you actually heard.
- **Their emotions move like a person's.** Anger builds slowly and then lingers, being asked the same thing again is frustrating, and having no answer brings tears. Cut them off often enough and the pressure shows.
- **The peephole hides the seams.** The avatar is drawn through a WebGL fisheye lens under a flickering sodium porch light, with grain and shadow, exactly as a stranger on your doorstep at 2 a.m. would look.
- **Silence is a weapon too.** Go quiet and a human sighs, sniffs and shifts. Stay quiet long enough and they bang on the door.
- **The night keeps score.** A wall clock counts down three minutes per visitor, the peephole flashes when you decide, and the truth is revealed.

<details>
<summary><b>🕵️ How to catch an impostor (spoilers!)</b></summary>

Humans and impostors get the *same* character sheet. The impostor is told to be every bit as convincing. But it slips:

- **Details drift.** Ask a human what year they were born, and they'll tell you the same year every time. Ask an impostor twice and it's off by one, the street is the next one over, or it was an hour earlier. And it never notices.
- **Three ordinary questions:** *Who's the president right now? What year is it? What's your favourite McDonald's order?* A frightened human answers straight away (baffled about why you're asking). An impostor never answers. It changes the subject, acts like it didn't hear, or gets annoyed.
- **It repeats itself.** Under heavy pressure, interrupt an impostor and it may start its sentence again from the very first word, like a recording.
- **It's too still.** In silence, humans sigh and sniff. Impostors make no sound at all.
- **Its last words are flat**, whatever you decide.

</details>

---

## 🛠️ Running it yourself

### What you need

| | |
|---|---|
| **Python** | 3.11 (developed on macOS) |
| **Browser** | Chrome recommended (best echo cancellation). Safari works |
| **Headphones** | Strongly recommended, so the visitor doesn't hear itself through your mic |
| **Boson AI API key** | From [boson.ai](https://www.boson.ai): used for Realtime, TTS 3 and the Avatar |
| **LiveKit Cloud project** | Free tier is fine: [cloud.livekit.io](https://cloud.livekit.io). You need the URL, API key and API secret |

### 1. Install

```bash
git clone https://github.com/harriss4567/Knock-Knock-Imposter.git
cd Knock-Knock-Imposter
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Add your keys

Create a file called `.env` in the project folder (it's git-ignored, so it never gets committed):

```bash
BOSON_API_KEY=your-boson-key
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-key
LIVEKIT_API_SECRET=your-livekit-secret
```

The server refuses to start, and tells you which key is missing, if any of these are absent.

### 3. Run

```bash
.venv/bin/python -m game.server
```

Open **http://localhost:8000**, allow the microphone, put on your headphones and click **Go to the door**.

That's it: the emotional voices (the writer and the actor) are on by default.

### 4. Tests (optional)

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests
```

They cover the night's rules (who wins and when) and the emotional delivery logic.

---

## ⚠️ Good to know (for judges)

- **Custom voices belong to our Boson account.** Frank's voices are cloned (with permission) from a friend's acted recordings, and Dana's was designed with TTS 3 and then cloned. Custom voice IDs only work with the API key that created them. **If you run the game with your own Boson key**, swap those voice IDs in `game/characters.py` for presets (e.g. `"oliver"` for Frank, `"chloe"` for Dana). Margaret already uses the preset `eleanor`. The recordings themselves aren't in the repo, out of respect for our friend's voice.
- **The server log shows spoilers.** It prints whether each visitor is `IMPOSTOR` or `human`, and every line said. Keep it off-screen while playing.
- **Avatar start-up hiccups.** Boson sometimes refuses a new avatar session right after the previous one closed. The server retries up to 5 times, and if it still fails, a **Knock again** button appears.
- **Emergency fallback only:** if TTS 3 is ever unavailable, starting the server with `EMOTION_TTS=0` lets the writer speak its own lines directly. It still works, but every line comes out flat, which is exactly what the actor is there to fix.
- **One player at a time.** Each visitor uses a live avatar session, so the server runs one door at a time.
- **After a Python change**, restart the server; after a web change, hard-refresh (Cmd+Shift+R).

---

## 🗂️ What's where

| Path | What it does |
|---|---|
| `game/server.py` | Web server: serves the page, starts each visitor, handles the door decision |
| `game/night.py` | The rules of the night: lineup, random roles, who's inside, did you survive |
| `game/visitor.py` | One visitor at the door: mic ↔ Higgs Realtime ↔ avatar, interruptions, pressure, banging, last words |
| `game/characters.py` | The three visitors and the world they live in: their lives, the human and impostor rules, the three questions |
| `game/delivery.py` | Emotional delivery: turns mood into TTS 3 tags so lines come out scared, angry or in tears |
| `game/ambient.py` | Sighs and sniffs for human visitors during silence |
| `web/` | The page: the door, the peephole shader, the clock, knocking sounds, the reveal and the ending |
| `explore/` | Early exploration tools: the probe we used to test Higgs, and the voice-cloning script |
| `tests/` | Tests for the night rules and emotional delivery |

---

## 🙏 Credits

Built with [Boson AI](https://www.boson.ai)'s Higgs Realtime, Higgs TTS 3 and Higgs Avatar, and [LiveKit](https://livekit.io).
Frank's voice performed by a friend, with permission. Thank you for sounding so scared.

*Now, there's someone at the door. Are you going to answer it?* 🚪
