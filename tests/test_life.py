import math
import pytest
from little_blue.life import Body, Life, Sequence, TimedAction
from little_blue.models import Action, Environment, Needs


def test_exhausted_pet_prioritizes_sleep():
    pet = Life(Needs(energy=5), seed=4)
    pet.tick(.5, Environment())
    assert pet.goal == 'rest'
    for _ in range(10):
        pet.tick(.5, Environment())
    assert pet.action == Action.SLEEP
    before = pet.needs.energy
    pet.tick(1, Environment())
    assert pet.needs.energy > before


def test_long_offline_simulation_is_bounded_and_varied():
    pet = Life(seed=8)
    actions = set()
    for _ in range(36000):
        pet.tick(.5, Environment())
        actions.add(pet.action)
        assert all(0 <= value <= 100 for value in pet.needs.model_dump().values())
        assert -1 <= pet.emotion.valence <= 1
    assert {Action.SLEEP, Action.WALK, Action.JUMP, Action.HAPPY} <= actions


def test_behaviors_persist_and_cooldowns_prevent_repetition():
    pet = Life(seed=2)
    pet.choose(Environment())
    first = pet.goal
    pet.tick(.1, Environment())
    assert pet.goal == first
    pet.cooldowns[first] = pet.elapsed + 100
    assert pet.utilities(Environment())[first] == 0


def test_emotion_changes_smoothly():
    pet = Life(seed=3)
    pet.needs.mood = 0
    before = pet.emotion.valence
    pet.tick(.25, Environment())
    assert 0 < before - pet.emotion.valence < .1


def test_drag_holds_and_release_resumes():
    pet = Life(seed=3)
    pet.interact('drag')
    for _ in range(100):
        pet.tick(1, Environment())
        assert pet.action == Action.DRAG
    pet.interact('release')
    for _ in range(5):
        pet.tick(1, Environment())
    assert pet.action != Action.DRAG


def test_pet_interaction_changes_trust_and_social():
    pet = Life()
    before = pet.needs.model_copy()
    pet.interact('pet')
    assert pet.needs.trust > before.trust
    assert pet.needs.social < before.social
    assert pet.action == Action.HAPPY


def test_physics_respects_negative_monitor_origin_and_lands():
    body = Body(x=-1910, y=300)
    for _ in range(500):
        direction = body.tick(.016, Action.WALK, (-1920, 0, 0, 1080), 180, -1)
        assert -1920 <= body.x <= -180
        assert 0 <= body.y <= 900
    assert body.y == 900


def test_jump_is_edge_triggered():
    body = Body(x=500, y=900)
    body.tick(.016, Action.JUMP, (0,0,1920,1080),180,1)
    assert body.y < 900
    for _ in range(200):
        body.tick(.016, Action.JUMP, (0,0,1920,1080),180,1)
    assert body.y == 900


def test_sequence_finishes_in_order():
    tree = Sequence(TimedAction(Action.WALK, 1), TimedAction(Action.HAPPY, 1))
    assert not tree.tick(1)
    assert tree.action == Action.HAPPY
    assert tree.tick(1)


def test_nan_state_rejected():
    with pytest.raises(ValueError):
        Needs(energy=math.nan)
