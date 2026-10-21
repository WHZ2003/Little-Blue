"""Pure, deterministic-under-a-seed artificial life; no Qt or network imports."""
import math
import random
from dataclasses import dataclass
from .models import Action, Needs, Emotion, Personality, Environment


@dataclass
class TimedAction:
    action: Action
    duration: float
    elapsed: float = 0

    def tick(self, dt):
        self.elapsed += dt
        return self.elapsed >= self.duration


class Sequence:
    """A stateful behavior-tree sequence of timed leaf nodes."""
    def __init__(self, *children):
        self.children = list(children)
        self.index = 0

    @property
    def action(self):
        return self.children[min(self.index, len(self.children) - 1)].action

    def tick(self, dt):
        if self.children[self.index].tick(dt):
            self.index += 1
        return self.index >= len(self.children)


class Life:
    def __init__(self, needs=None, emotion=None, personality=None, seed=None):
        self.needs = needs or Needs()
        self.emotion = emotion or Emotion()
        self.personality = personality or Personality()
        self.rng = random.Random(seed)
        self.elapsed = 0.0
        self.goal = "settle"
        self.tree = Sequence(TimedAction(Action.IDLE, 4))
        self.cooldowns = {}
        self.scores = {}
        self.direction = self.rng.choice([-1, 1])
        self.override = None
        self.override_until = 0
        self.last_interaction = -100.0
        self.drag_started = 0
        self.phone_active = False

    @property
    def action(self):
        if self.override is not None and self.elapsed < self.override_until:
            return self.override
        if self.phone_active:
            return Action.IDLE
        return self.tree.action

    def interact(self, kind):
        if kind == "drag":
            self.drag_started = self.elapsed
            self.override, self.override_until = Action.DRAG, float("inf")
            return
        if kind == "release":
            long_drag = self.elapsed - self.drag_started > 8
            self.override = Action.ANGRY if long_drag else Action.IDLE
            self.override_until = self.elapsed + (4 if long_drag else 1)
            if long_drag:
                self.needs.change("mood", -12)
            return
        if self.elapsed - self.last_interaction < .6:
            return
        self.last_interaction = self.elapsed
        self.needs.change("social", -12)
        self.needs.change("trust", .6 if kind == "pet" else .2)
        self.needs.change("mood", 6 if kind == "pet" else 2)
        self.needs.change("boredom", -6)
        self.override = Action.HAPPY if kind == "pet" else Action.JUMP
        self.override_until = self.elapsed + 2

    def utilities(self, env):
        n, p = self.needs, self.personality
        scores = {
            "rest": max(0, 65 - n.energy) * 2.3 + (15 if env.hour < 7 else 0),
            "explore": n.curiosity * p.curiosity + n.boredom * .3,
            "play": n.boredom * p.playfulness + n.energy * .18,
            "stroll": n.boredom * .5 + 18 * p.independence,
            "observe": 18 + (25 if env.focused else 0),
        }
        if n.energy < 20:
            return {k: (1000 if k == "rest" else 0) for k in scores}
        for k in scores:
            if self.cooldowns.get(k, 0) > self.elapsed:
                scores[k] = 0
            elif k == self.goal:
                scores[k] *= .15
        return scores

    def choose(self, env):
        self.scores = self.utilities(env)
        choices, weights = zip(*self.scores.items())
        goal = self.rng.choices(choices, weights=weights)[0] if sum(weights) else "observe"
        self.goal = goal
        self.direction = self.rng.choice([-1, 1])
        duration = self.rng.uniform(5, 12)
        plans = {
            "rest": [(Action.IDLE, 2), (Action.SLEEP, self.rng.uniform(35, 65))],
            "explore": [(Action.WALK, duration), (Action.IDLE, 3), (Action.JUMP, 1.5)],
            "play": [(Action.RUN, duration / 2), (Action.JUMP, 1.5), (Action.HAPPY, 3)],
            "stroll": [(Action.WALK, duration), (Action.IDLE, 4)],
            "observe": [(Action.IDLE, duration)],
        }
        self.tree = Sequence(*(TimedAction(a, d) for a, d in plans[goal]))

    def tick(self, dt, env):
        dt = max(0, min(dt, 1.0))  # suspend/resume never advances hours in one frame
        self.elapsed += dt
        a, n = self.action, self.needs
        n.change("energy", (1.1 if a == Action.SLEEP else -.055 if a == Action.IDLE else -.22) * dt)
        n.change("boredom", (.14 if a in (Action.IDLE, Action.SLEEP) else -.65) * dt)
        n.change("curiosity", (-.6 if self.goal == "explore" else .08) * dt)
        n.change("social", .035 * dt)
        n.change("mood", (65 - n.mood) * dt / 600)
        target_v = (n.mood - 50) / 50
        target_a = -.8 if a == Action.SLEEP else (n.energy + n.curiosity - 100) / 100
        alpha = 1 - math.exp(-dt / 8)
        self.emotion.valence += (target_v - self.emotion.valence) * alpha
        self.emotion.arousal += (target_a - self.emotion.arousal) * alpha
        if self.override is not None and self.elapsed < self.override_until:
            return
        self.override = None
        if self.phone_active and n.energy >= 10:
            return
        if n.energy < 10 and self.goal != "rest":
            self.choose(env)
        elif self.tree.tick(dt):
            self.cooldowns[self.goal] = self.elapsed + (90 if self.goal == "rest" else 18)
            self.choose(env)


@dataclass
class Body:
    x: float = 0
    y: float = 0
    vy: float = 0
    direction: int = 1
    previous: Action = Action.IDLE

    def tick(self, dt, action, bounds, size, direction, target_x=None):
        left, top, right, bottom = bounds
        right, bottom = max(left, right - size), max(top, bottom - size)
        dt = max(0, min(dt, .05))
        grounded = self.y >= bottom - .1
        if action == Action.JUMP and self.previous != action and grounded:
            self.vy = -440
        self.previous = action
        speed = 130 if action == Action.RUN else 48 if action == Action.WALK else 0
        self.direction = direction
        if target_x is not None and action in (Action.IDLE, Action.WALK) and abs(target_x - self.x) > size:
            self.direction = 1 if target_x > self.x else -1
            speed = 48
        self.x += self.direction * speed * dt
        if self.x <= left:
            self.x, self.direction = left, 1
        elif self.x >= right:
            self.x, self.direction = right, -1
        self.vy += 1100 * dt
        self.y = max(top, self.y + self.vy * dt)
        if self.y >= bottom:
            self.y, self.vy = bottom, 0
        return self.direction
