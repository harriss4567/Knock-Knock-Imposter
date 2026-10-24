"""One night: the lineup knocks one at a time, the player opens the door or keeps it shut, and dawn decides.

You survive only if more humans than impostors are inside when the sun comes up.
"""

import random
from dataclasses import dataclass

from .characters import LINEUP, Character


@dataclass
class Visit:
    character: Character
    is_impostor: bool
    opened: bool | None = None  # None until the player decides


class Night:
    def __init__(self, rng: random.Random | None = None, lineup: tuple[Character, ...] = LINEUP):
        rng = rng or random.Random()
        self.visits = [Visit(c, is_impostor=not c.always_human and rng.random() < 0.5) for c in lineup]
        self.index = -1  # the visit at the door; -1 before the first knock

    @property
    def current(self) -> Visit | None:
        return self.visits[self.index] if 0 <= self.index < len(self.visits) else None

    @property
    def finished(self) -> bool:
        return all(v.opened is not None for v in self.visits)

    def next_visit(self) -> Visit | None:
        """The next visitor, or None when the night is over. The one at the door must be decided first."""
        if self.current and self.current.opened is None:
            raise ValueError("decide on the visitor at the door first")
        if self.index + 1 >= len(self.visits):
            return None
        self.index += 1
        return self.current

    def decide(self, opened: bool) -> Visit:
        visit = self.current
        if visit is None or visit.opened is not None:
            raise ValueError("nobody is waiting at the door")
        visit.opened = opened
        return visit

    @property
    def humans_in(self) -> int:
        return sum(1 for v in self.visits if v.opened and not v.is_impostor)

    @property
    def impostors_in(self) -> int:
        return sum(1 for v in self.visits if v.opened and v.is_impostor)

    @property
    def won(self) -> bool:
        return self.humans_in > self.impostors_in
