import random

import pytest

from game.characters import FRANK, LINEUP, MARGARET
from game.night import Night


def night_with(*impostors: bool) -> Night:
    night = Night(random.Random(0))
    for visit, is_impostor in zip(night.visits, impostors):
        visit.is_impostor = is_impostor
    return night


def play(night: Night, *choices: bool):
    for opened in choices:
        night.next_visit()
        night.decide(opened)


def test_lineup_order():
    assert [v.character for v in Night().visits] == list(LINEUP)


def test_always_human_is_never_an_impostor():
    for seed in range(50):
        night = Night(random.Random(seed))
        assert not next(v for v in night.visits if v.character is MARGARET).is_impostor


def test_roles_are_random():
    frank_roles = {next(v for v in Night(random.Random(s)).visits if v.character is FRANK).is_impostor
                   for s in range(50)}
    assert frank_roles == {True, False}


@pytest.mark.parametrize("impostors, choices, won", [
    ((False, True, False), (True, False, True), True),    # two humans in
    ((False, True, False), (True, True, False), False),   # one human, one impostor: a tie loses
    ((False, False, False), (False, False, False), False),  # nobody in: loses
    ((True, True, False), (False, False, True), True),    # one human alone
    ((True, True, False), (True, True, True), False),     # impostors outnumber
])
def test_outcome(impostors, choices, won):
    night = night_with(*impostors)
    play(night, *choices)
    assert night.finished
    assert night.won is won


def test_counts():
    night = night_with(True, False, False)
    play(night, True, True, False)
    assert (night.humans_in, night.impostors_in) == (1, 1)


def test_night_ends_after_lineup():
    night = night_with(False, False, False)
    play(night, True, True, True)
    assert night.next_visit() is None


def test_must_decide_before_next_knock():
    night = Night()
    night.next_visit()
    with pytest.raises(ValueError):
        night.next_visit()


def test_cannot_decide_twice_or_before_a_knock():
    night = Night()
    with pytest.raises(ValueError):
        night.decide(True)
    night.next_visit()
    night.decide(True)
    with pytest.raises(ValueError):
        night.decide(False)
