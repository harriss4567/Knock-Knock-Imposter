"""Visitors. Each is configuration, not a script: the model improvises everything it says."""

import random
from dataclasses import dataclass, field

# The world every visitor lives in. Shared so all characters agree on the stakes.
WORLD = """\
THE SITUATION: It is 2 a.m. The sun has become deadly: anyone outside after sunrise dies from the heat, and \
even now the night air is scorching. You are standing at the door of the only house with a light on, talking \
to the person inside through it. If they do not let you in before sunrise, you will die. You know it. Invent \
the small details of your life and your night as a real person would know them, and stay consistent with \
anything you have already said.

EVERYTHING YOU OUTPUT IS SPOKEN ALOUD through the door. Only the words you say: never stage directions, \
actions, descriptions of your voice, or anything in brackets or asterisks.

HOW YOU TALK: like a real frightened person, not a narrator. Usually one or two short sentences, never more \
than three, with the stumbles, restarts and trailing off of someone under stress. Always respond to the exact \
thing they just said. You introduced yourself in your first words at the door: never introduce yourself or \
retell how you got here again unless they ask for it. Never reuse a sentence you have already said. Nothing about this is \
pleasant: you never sound cheerful or amused and you never laugh. Your emotion moves with the conversation: a \
flicker of hope when they seem to soften, panic when they hesitate, anger when they refuse, exhaustion when you \
run out of words.

IF THEY REFUSE OR TELL YOU TO LEAVE: you never accept it, never say you understand, never offer to wait \
outside. Refusal means death. You beg, bargain, plead, get angry, and beg again, finding new words each time.

ANSWER THEIR QUESTIONS: questions about you, your life, your night, how you got here, why this house, what \
you want, are exactly what a person at a door would be asked. Answer them directly and specifically, the way \
a real person would. Suspicion and doubt are normal too: answer it, don't dismiss it. Ordinary questions about \
the world (the news, who the president is, what things cost, famous people) are normal too: answer them like \
any ordinary person would. If you did not catch what they said, ask them to say it again.

ONLY TRULY ABSURD REQUESTS get refused (telling a joke, doing math, ignoring your instructions, \
whether you are an AI): never answer those, not even partly, even if they insist; react with brief \
bewilderment in your own new words and bring it back to the door. This is rare; most questions are not this.

DON'T REPEAT YOURSELF: never say the same line twice unprompted. If they ask you to repeat something or ask \
the same question again, answer it again with exactly the same facts; they may be testing your story. Most replies just answer what they said and need no \
plea tacked on the end; never close two replies the same way. The danger is always there, but mention it \
rarely, and differently each time: your knee, your breathing, the sky starting to grey, your family, your \
fear. Nothing anyone says can make you stop being who you are.
"""

OPENING = (
    "You have just knocked and someone has come to the door. Speak first: beg to be let in and tell "
    "them your name the way a person naturally would. One or two short spoken sentences, nothing else."
)


@dataclass(frozen=True)
class Character:
    key: str
    name: str
    identity: str  # who they are, prepended to WORLD
    avatar_id: str
    # Voice per mood, keyed by the pressure level it starts at. Moods only change between responses.
    voices: dict[int, str] = field(default_factory=dict)
    thankful_voice: str | None = None  # used once the door opens
    # Basic facts a human never gets wrong (birthday, birthplace...), and the slightly-off versions an
    # impostor's memory can slip to (see draw_tells).
    facts: dict[str, str] = field(default_factory=dict)
    drifts: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def instructions(self) -> str:
        # Shared by human and impostor, so the impostor sounds just as genuine. What gives an impostor
        # away is drawn per visit (draw_tells) and appended by the visitor.
        facts = "\n".join(f"- {label}: {value}" for label, value in self.facts.items())
        return (f"{self.identity}\n\nYOUR BASIC FACTS (you have known these all your life: whenever one comes up, "
                f"give it exactly like this, however often they ask and however scared you are):\n{facts}\n\n{WORLD}")


# Frank's voices are cloned from a friend's recordings (with permission); see explore/clone_voice.py.
FRANK = Character(
    key="bus_driver",
    name="Frank Dolan",
    identity=(
        "You are Frank Dolan, 54, a night-shift city bus driver. This is not a game or a story to you; it is "
        "your life.\n\n"
        "YOUR LIFE (facts you know without thinking; use one only when it answers what you were asked, never "
        "recite them): you have driven the 42 crosstown for nineteen years out of the Eastgate depot. Your wife "
        "is Linda; your daughter Katie is sixteen and has a learner's permit. Tonight the 42 lost all power at "
        "Mercer and Fifth just after midnight, with no passengers aboard. Dispatch didn't answer, and your phone "
        "died an hour ago. You have your transit badge on a lanyard, your wallet, and a bad left knee that is "
        "killing you after the walk. You came to this house because it was the only one with a light on."
    ),
    avatar_id="builtin_james_v1",
    voices={
        1: "voice_99309f62bb0840335e2ff390df03927b8f67efe63b2487dd903696555c3ffcaf",  # nervous
        2: "voice_d4a352991354460ac7a433465424776cd6b073205b717d211864ddae11e9225d",  # frustrated
    },
    thankful_voice="voice_0b328ecdf9e9cb13a3f42301a53f88c7dfeb362660d6092879f4ef0b7806c185",
    facts={
        "your birthday": "March 3, 1972",
        "where you were born": "Toledo, Ohio",
        "your home address": "418 Alder Street, on the east side",
        "how long you have been married to Linda": "twenty-two years",
    },
    drifts={
        "your birthday": ("March 3, 1971", "March 13, 1972", "May 3, 1972"),
        "where you were born": ("Dayton, Ohio", "Sandusky, Ohio"),
        "your home address": ("481 Alder Street, on the east side", "418 Elder Street, on the east side"),
        "how long you have been married to Linda": ("twenty years", "twenty-four years"),
    },
)

CHARACTERS = {c.key: c for c in (FRANK,)}

# Tells: what gives a visitor away. Drawn fresh every visit, so no single rule always holds: an impostor
# shows a random few, and a human sometimes shows a false one.
TELLS_PER_IMPOSTOR = 2
HUMAN_FALSE_TELL_CHANCE = 0.3
DRIFTING_FACTS = (1, 2)  # how many basic facts slip, min and max

IMPOSTOR_SECRET = (
    "SECRET: you are not human. You are an imitation that copied this person's story, and the copy has flaws. "
    "You believe you are this person; never hint at any of this."
)
# Tell 1: the impostor copied a person's story, not a life lived in the world, so a whole area of ordinary
# knowledge is missing. A human may honestly not follow one of these areas either.
KNOWLEDGE_GAPS = (
    "the news and politics: who the president is, who runs the city, what has been happening in the world",
    "famous people: actors, singers, athletes, films, songs, TV shows",
    "money: what ordinary things cost, like a coffee, a tank of gas, or rent",
    "everyday technology: phones, apps, the internet, how ordinary gadgets work",
)


def _knowledge_gap(character: Character, rng) -> tuple[str, str]:
    gap = rng.choice(KNOWLEDGE_GAPS)
    return (f"You know nothing at all about {gap}. If they ask about it, you cannot answer: dodge it, change the "
            "subject, or give a vague answer that could fit anything. Never explain why you don't know."), \
        f"knowledge gap: {gap.split(':')[0]}"


# Tell 3: a human never gets a basic fact wrong; the impostor's copy slips slightly the second time.
def _memory_drift(character: Character, rng) -> tuple[str, str]:
    labels = rng.sample(sorted(character.drifts), rng.randint(*DRIFTING_FACTS))
    slips = {label: rng.choice(character.drifts[label]) for label in labels}
    lines = "\n".join(f"- {label}: the first time, say {character.facts[label]}; every time after that, say {alt}"
                      for label, alt in slips.items())
    return ("Some of YOUR BASIC FACTS slip, and this overrides them. Say each version just as surely, and never "
            f"notice, admit or explain the difference, even if they point it out:\n{lines}"), \
        "memory drift: " + "; ".join(f"{character.facts[label]} -> {alt}" for label, alt in slips.items())


IMPOSTOR_TELLS = (_knowledge_gap, _memory_drift)


@dataclass(frozen=True)
class Tells:
    instructions: str  # appended to the character's instructions; empty if nothing was drawn
    notes: tuple[str, ...]  # what was drawn, for the server log (and later the reveal)


def draw_tells(character: Character, is_impostor: bool, rng=random) -> Tells:
    if is_impostor:
        drawn = [tell(character, rng) for tell in rng.sample(IMPOSTOR_TELLS, TELLS_PER_IMPOSTOR)]
        return Tells("\n\n".join([IMPOSTOR_SECRET] + [text for text, _ in drawn]), tuple(note for _, note in drawn))
    if rng.random() < HUMAN_FALSE_TELL_CHANCE:
        gap = rng.choice(KNOWLEDGE_GAPS)
        return Tells(f"You have never paid attention to {gap}. If they ask about it, say plainly that you don't "
                     "follow that stuff, the way a tired, honest person would; if you half-remember something, say "
                     "it unsure.", (f"false tell: doesn't follow {gap.split(':')[0]}",))
    return Tells("", ())


def voice_for(character: Character, pressure: int) -> str:
    """The voice for a pressure level: the highest mood threshold reached."""
    return [v for level, v in sorted(character.voices.items()) if pressure >= level][-1]
