"""How a visitor's line sounds.

Higgs Realtime can't do emotion: told to sound terrified, it still reads "I'm scared" evenly. Higgs TTS 3 can,
through inline tags like <|emotion:fear|>. So Realtime writes each line with tags (TAG_RULES), this module
keeps the emotion moving like a person's (Mood), checks the tags and applies the game's rules (deliver), and
TTS 3 speaks the result in one voice. The speaker
never changes; only the emotion does. Tags: https://docs.boson.ai/models/higgs-tts/tags
"""

import re
from difflib import SequenceMatcher

TAG_RULES = """\
DELIVERY TAGS (the one exception to "only the words you say"): your words are performed by a voice engine \
that reads tags written as <|kind:value|>. Tags are never spoken; they set how the words sound. Start every \
reply with exactly one emotion tag for how you feel at this moment, and add at most two more where they fit.
- emotion (always, first): fear, anger, helplessness, sadness, relief, confusion, determination, bitterness
- style (optional, right after the emotion): shouting when you are desperate or furious, whispering when \
you are afraid of something nearby
- sound (optional, at the start of a sentence, immediately followed by the written sound): \
<|sfx:sniff|>Sff..., <|sfx:sigh|>Ahh..., <|sfx:cough|>Ahem, <|sfx:crying|> followed by broken words like "I... I"
- prosody (optional): <|prosody:pause|> where your voice breaks, <|prosody:speed_fast|> when you panic
HOW YOUR EMOTION MOVES, like a real person's, building up slowly, not the same one every time:
- Asked for proof, doubted or refused the first time: scared, hurt or confused, never angry. Ask why they \
need it, or offer what you can.
- Asked the same thing again, or refused again: frustrated (bitterness) and short with them, still not angry.
- Only after they have refused or doubted you again and again, or when time is almost up: anger, sometimes \
shouting. Once angry, you stay angry for your next few replies, longer if they keep pushing; you calm down \
only if they soften.
- They ask you something you can't answer, or you think of the people you love: you break down crying.
- Fear when the danger closes in; helplessness only when you have truly run out of words.
How replies open (the shape only: the words that follow are always your own, never these):
<|emotion:fear|><|style:whispering|>...
<|emotion:bitterness|>...
<|emotion:sadness|><|sfx:crying|>I... I...
<|emotion:anger|><|style:shouting|>...
No other brackets, asterisks or stage directions, ever."""

ALLOWED = {
    "emotion": {"fear", "anger", "helplessness", "sadness", "relief", "confusion", "determination", "bitterness"},
    "style": {"shouting", "whispering"},
    "sfx": {"sniff", "sigh", "cough", "crying"},
    "prosody": {"pause", "long_pause", "speed_fast", "expressive_high", "expressive_low"},
}
# Loose on purpose (spaces, capitals), so a slightly malformed tag is fixed or dropped, never read aloud.
TAG = re.compile(r"<\|[^<>|]*\|>")
STAGE_DIRECTION = re.compile(r"\*[^*]*\*|\[[^\]]*\]")
LEADING_TAGS = re.compile(r"^(?:\s*<\|[^<>|]*\|>)+\s*")
DEFAULT_EMOTION = "<|emotion:fear|>"
INTENSE = "<|prosody:expressive_high|>"
FLAT = "<|prosody:expressive_low|>"
# Humans at this pressure level or above get more intense delivery.
INTENSE_FROM = 3
# The impostor tell "the voice doesn't match the words": from this pressure level on, an impostor's lines lose
# all emotion. Below it, an impostor sounds exactly like a human.
IMPOSTOR_FLAT_FROM = 2

# Mood: how the visitor's emotion carries from one line to the next, whatever the model tagged.
# Anger builds up: only after this many provocations (refused, doubted, asked the same thing again), or late in
# the visit, may the visitor get angry. Before that, anger comes out as frustration.
ANGRY_AFTER = 3
# Lines after an angry one that stay angry; while the player keeps provoking, the count starts over.
ANGER_LASTS = 2
FRUSTRATED = "<|emotion:bitterness|>"
ANGRY = "<|emotion:anger|><|style:shouting|>"
IMPATIENT = "<|emotion:anger|><|prosody:speed_fast|>"
CRYING = "<|emotion:sadness|><|sfx:crying|>"
NOT_KNOWING = re.compile(
    r"\b(?:i don'?t know|i'?m not sure|no idea|i don'?t remember|i can'?t (?:answer|remember|say))\b", re.I)
PROVOKING = re.compile(
    r"^\W*no\b|\b(?:won'?t|will not|not (?:letting|let|open|opening|going to)|never|go away|leave|get lost"
    r"|impostor|imposter|not (?:a )?(?:real|human)|liar|lying|fake|don'?t (?:trust|believe))\b", re.I)
# Share of the shorter question's words, in order, that make the player's line count as asked before.
SAME_QUESTION = 0.8


def _canonical(match: re.Match) -> str:
    kind, _, value = match.group(0)[2:-2].replace(" ", "").lower().partition(":")
    return f"<|{kind}:{value}|>" if value in ALLOWED.get(kind, ()) else ""


def _tidy(text: str) -> str:
    return re.sub(r"\s+([.,!?])", r"\1", " ".join(text.split()))


def spoken(text: str) -> str:
    """The words alone, as the player hears them (subtitles, memory)."""
    return _tidy(STAGE_DIRECTION.sub(" ", TAG.sub(" ", text)))


def leading_tags(text: str) -> str:
    m = LEADING_TAGS.match(text)
    return "".join(m.group(0).split()) if m else ""


def deliver(text: str, *, is_impostor: bool, pressure: int) -> str:
    """The model's line -> what TTS 3 should say: only known tags, always an emotion, plus the game's rules."""
    if is_impostor and pressure >= IMPOSTOR_FLAT_FROM:
        return FLAT + spoken(text)
    line = _tidy(STAGE_DIRECTION.sub(" ", TAG.sub(_canonical, text)))
    m = LEADING_TAGS.match(line)
    lead, rest = (leading_tags(line), line[m.end():]) if m else ("", line)
    if "<|emotion:" not in lead:
        lead = DEFAULT_EMOTION + lead
    if not is_impostor and pressure >= INTENSE_FROM and INTENSE not in lead:
        lead += INTENSE
    return lead + rest


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def asked_before(line: str, earlier: list[str]) -> bool:
    """The player asks again what they already asked, give or take a few words."""
    words = _words(line)
    for before in map(_words, earlier):
        shorter = min(len(words), len(before))
        if shorter >= 3:
            same = sum(block.size for block in SequenceMatcher(None, words, before).get_matching_blocks())
            if same >= SAME_QUESTION * shorter:
                return True
    return False


class Mood:
    """Keeps the emotion moving like a person's, where the model's own tags don't: anger builds up instead of
    flaring at the first hard question, and doesn't vanish right after a shout; being asked the same thing again
    is irritating, and having no answer is upsetting.
    Applied to the model's line before deliver(), so the game's rules (an impostor going flat) still win."""

    def __init__(self):
        self._provoked = 0
        self._angry_for = 0

    def steer(self, text: str, *, player: str = "", repeated: bool = False, late: bool = False) -> str:
        """text: the model's line. player: what the player just said; repeated: whether they asked it before.
        late: time is nearly up."""
        provoking = repeated or bool(PROVOKING.search(player))
        self._provoked += provoking
        may_be_angry = late or self._provoked >= ANGRY_AFTER
        if repeated:
            if not may_be_angry:
                return _retag(text, FRUSTRATED)
            self._angry_for = ANGER_LASTS
            return _retag(text, IMPATIENT)
        if "<|emotion:anger|>" in leading_tags(TAG.sub(_canonical, text)):
            if not may_be_angry:
                return _retag(text, FRUSTRATED)  # too soon
            self._angry_for = ANGER_LASTS
            return text
        if self._angry_for:
            self._angry_for = ANGER_LASTS if provoking else self._angry_for - 1
            return _retag(text, ANGRY)
        if NOT_KNOWING.search(spoken(text)):
            return _retag(text, CRYING, sob=True)
        return text


def _retag(text: str, tags: str, sob: bool = False) -> str:
    """The line with its opening tags replaced. sob: crying needs broken words right after the tag."""
    m = LEADING_TAGS.match(text)
    rest = text[m.end():] if m else text.lstrip()
    if sob and not rest.startswith(("I...", "I…")):
        rest = "I... " + rest
    return tags + rest
