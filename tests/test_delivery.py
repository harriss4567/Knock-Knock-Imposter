import pytest

from game.delivery import ANGRY as ANGRY_TAGS
from game.delivery import (ANGRY_AFTER, CRYING, FRUSTRATED, IMPATIENT, IMPOSTOR_FLAT_FROM, Mood, asked_before,
                           deliver, leading_tags, spoken)

ANGRY = "<|emotion:anger|><|style:shouting|>You're going to let me die out here?"


def human(text: str, pressure: int = 1) -> str:
    return deliver(text, is_impostor=False, pressure=pressure)


def test_known_tags_are_kept():
    assert human(ANGRY) == ANGRY
    assert human("<|emotion:fear|>Did you hear that? <|prosody:pause|>Please.") == \
        "<|emotion:fear|>Did you hear that? <|prosody:pause|>Please."


def test_unknown_tags_are_dropped():
    assert human("<|emotion:fear|><|style:singing|>Please.") == "<|emotion:fear|>Please."
    assert human("<|emotion:joy|>Hello?") == "<|emotion:fear|>Hello?"


def test_capitals_and_spaces_are_fixed():
    assert human("<| Emotion : Anger |>Open it!") == "<|emotion:anger|>Open it!"


def test_stage_directions_are_dropped():
    assert human("*sobs* Please [crying] let me in.") == "<|emotion:fear|>Please let me in."


def test_missing_emotion_becomes_fear():
    assert human("<|style:whispering|>Hello?") == "<|emotion:fear|><|style:whispering|>Hello?"
    assert human("Hello?") == "<|emotion:fear|>Hello?"


def test_human_under_pressure_sounds_more_intense():
    assert human(ANGRY, pressure=2) == ANGRY
    assert human(ANGRY, pressure=3) == \
        "<|emotion:anger|><|style:shouting|><|prosody:expressive_high|>You're going to let me die out here?"


@pytest.mark.parametrize("text", [ANGRY, "<|emotion:fear|><|sfx:crying|>I... I can't.", "*sobs* Please."])
def test_impostor_sounds_human_until_pressure(text):
    assert deliver(text, is_impostor=True, pressure=1) == human(text)


@pytest.mark.parametrize("pressure", [IMPOSTOR_FLAT_FROM, 3])
def test_impostor_goes_flat_under_pressure(pressure):
    assert deliver("<|emotion:fear|><|sfx:crying|>I... I can't.", is_impostor=True, pressure=pressure) == \
        "<|prosody:expressive_low|>I... I can't."


def test_spoken_keeps_words_apart():
    assert spoken(ANGRY) == "You're going to let me die out here?"
    assert spoken("Wait.<|prosody:pause|>Please<|sfx:sigh|>open.") == "Wait. Please open."
    assert spoken("Hi <|sfx:sigh|>, there") == "Hi, there"


def test_leading_tags():
    assert leading_tags("<|emotion:fear|> <|style:whispering|> Hi <|prosody:pause|>there") == \
        "<|emotion:fear|><|style:whispering|>"
    assert leading_tags("Hi") == ""


CALM = "<|emotion:helplessness|>I just need to come in."
REFUSAL = "No, I will not let you in."


def provoked_mood() -> Mood:
    """A visitor refused often enough to be allowed to get angry."""
    mood = Mood()
    for _ in range(ANGRY_AFTER):
        mood.steer(CALM, player=REFUSAL)
    return mood


def test_anger_builds_up():
    mood = Mood()
    shout = ANGRY_TAGS + "You'd let me die?"
    for _ in range(ANGRY_AFTER - 1):  # refused, but not often enough yet: frustrated
        assert mood.steer(shout, player=REFUSAL) == FRUSTRATED + "You'd let me die?"
    assert mood.steer(shout, player=REFUSAL) == shout


def test_asked_for_proof_is_not_angry():
    assert Mood().steer("<|emotion:anger|>I don't have my ID on me!", player="Can I see your ID?") == \
        FRUSTRATED + "I don't have my ID on me!"


def test_late_in_the_visit_anger_comes_sooner():
    shout = ANGRY_TAGS + "Open the door!"
    assert Mood().steer(shout, late=True) == shout


def test_anger_lasts_two_more_lines():
    mood = provoked_mood()
    mood.steer(ANGRY_TAGS + "You'd let me die?")
    assert mood.steer(CALM) == ANGRY_TAGS + "I just need to come in."
    assert mood.steer(CALM) == ANGRY_TAGS + "I just need to come in."
    assert mood.steer(CALM) == CALM


def test_anger_stays_while_provoked():
    mood = provoked_mood()
    mood.steer("<|emotion:anger|>Open it!")
    for player in ["No, I will not.", "Because you are an impostor.", "No.", "Go away."]:
        assert mood.steer(CALM, player=player).startswith(ANGRY_TAGS)
    assert mood.steer(CALM, player="What's your name?").startswith(ANGRY_TAGS)
    assert mood.steer(CALM, player="Where were you born?").startswith(ANGRY_TAGS)
    assert mood.steer(CALM, player="Okay.") == CALM


def test_asked_again_is_frustrated_then_impatient():
    mood = Mood()
    assert mood.steer("<|emotion:fear|>Frank Dolan.", repeated=True) == FRUSTRATED + "Frank Dolan."
    assert mood.steer(CALM) == CALM  # frustration doesn't linger like anger
    assert provoked_mood().steer("<|emotion:fear|>Frank Dolan.", repeated=True) == IMPATIENT + "Frank Dolan."


def test_no_answer_makes_them_cry():
    mood = Mood()
    assert mood.steer("<|emotion:confusion|> I don't know, I work nights.") == \
        CRYING + "I... I don't know, I work nights."
    assert mood.steer("<|emotion:sadness|><|sfx:crying|>I... I'm not sure.") == CRYING + "I... I'm not sure."
    assert mood.steer(CALM) == CALM


def test_anger_beats_crying():
    mood = provoked_mood()
    mood.steer("<|emotion:anger|>Open it!")
    assert mood.steer("<|emotion:fear|>I don't know!").startswith(ANGRY_TAGS)


def test_mood_then_impostor_still_goes_flat():
    line = provoked_mood().steer("<|emotion:fear|>I don't know.", repeated=True)
    assert deliver(line, is_impostor=True, pressure=IMPOSTOR_FLAT_FROM) == "<|prosody:expressive_low|>I don't know."


def test_asked_before():
    earlier = ["Who are you?", "When is your birthday?", "Who is the president of the United States?"]
    assert asked_before("Okay, when is your birthday?", earlier)
    assert asked_before("Can you tell me when is your birthday and where you were born?", earlier)
    assert asked_before("Who's the president?", earlier) is False  # too few words in common
    assert asked_before("Who is the president?", earlier)
    assert asked_before("Where were you born?", earlier) is False
    assert asked_before("No.", ["No."]) is False  # too short to count
