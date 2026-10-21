import time

import pytest
from little_blue.life import Life
from little_blue.models import Environment, Action
from little_blue.phone import Phone, Puzzle, PhoneCommand, choose_direction, slide
from little_blue.storage import Store


@pytest.fixture
def phone(tmp_path):
    store = Store(tmp_path / 'phone.db')
    p = Phone(store, seed=7, now=100000)
    yield p
    store.close()


def cmd(p, action, life=None, now=100000, **data):
    return p.command({'action': action, 'revision': p.data.revision, **data}, life or Life(seed=3), now)


@pytest.mark.parametrize('row,result,gain', [([2, 2, 2, 2], [4, 4, 0, 0], 8),
    ([2, 2, 4, 0], [4, 4, 0, 0], 4), ([0, 4, 0, 4], [8, 0, 0, 0], 8),
    ([2, 4, 8, 16], [2, 4, 8, 16], 0)])
def test_2048_merge_once_and_score(row, result, gain):
    board = [row, [0]*4, [0]*4, [0]*4]
    new, score, changed = slide(board, 'left')
    assert new[0] == result and score == gain
    assert board[0] == row
    assert changed == (row != result)
    right, score, _ = slide(board, 'right')
    assert right[0] == slide([row[::-1], [0]*4, [0]*4, [0]*4], 'left')[0][0][::-1]
    vertical = [[row[r], 0, 0, 0] for r in range(4)]
    up, score, _ = slide(vertical, 'up')
    assert [up[r][0] for r in range(4)] == result


def test_noop_no_spawn_terminal_board_and_policy(phone):
    phone.data.puzzle = Puzzle(board=[[2, 0, 0, 0]] + [[0]*4 for _ in range(3)])
    before = phone.data.puzzle.model_dump()
    assert not phone.move('left')
    assert phone.data.puzzle.model_dump() == before
    terminal = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    assert choose_direction(terminal) is None
    terminal[3][3] = 4
    selected = choose_direction(terminal)
    assert selected in ('up', 'down', 'left', 'right') and slide(terminal, selected)[2]


def test_scores_manual_separation_and_persistence(phone):
    phone.owner = 'pet'
    phone.data.puzzle = Puzzle(board=[[2, 2, 4, 0]] + [[0]*4 for _ in range(3)])
    assert phone.move('left')
    assert phone.data.puzzle.pet_best == phone.data.puzzle.score == 4
    cmd(phone, 'take')
    phone.move('left')
    assert phone.data.puzzle.score >= 12 and phone.data.puzzle.pet_best == 4
    assert phone.data.puzzle.assisted
    phone.persist()
    restored = Phone(phone.store, seed=2, now=100002)
    assert restored.data.puzzle == phone.data.puzzle
    assert restored.owner == 'none' and restored.phase == 'idle'
    assert '4 分' in restored.summary()


def test_fishing_requires_bite_and_cannot_duplicate_catch(phone):
    cmd(phone, 'take', app='fishing')
    cmd(phone, 'cast', app='fishing')
    cmd(phone, 'reel', app='fishing')
    assert phone.phase == 'empty' and phone.data.catches == 0
    cmd(phone, 'cast', app='fishing')
    life, env = Life(seed=1), Environment()
    for _ in range(50):
        phone.tick(.25, life, env, now=100001)
        if phone.phase == 'bite':
            break
    assert phone.phase == 'bite'
    cmd(phone, 'reel', app='fishing', now=100001)
    assert phone.data.catches == 1 and sum(phone.data.collection.values()) == 2
    with pytest.raises(ValueError):
        cmd(phone, 'reel', app='fishing')
    assert phone.data.catches == 1
    cmd(phone, 'cast', app='fishing')
    for _ in range(20):
        phone.tick(1, life, env, now=100002)
    assert phone.phase == 'escaped'
    with pytest.raises(ValueError):
        cmd(phone, 'reel', app='fishing')


def test_pet_observes_float_and_catches_without_model(phone):
    phone.owner, phone.app = 'pet', 'fishing'
    life, env = Life(seed=1), Environment()
    for i in range(160):
        phone.tick(.25, life, env, now=100000 + i / 4)
    assert phone.data.catches >= 2
    assert life.needs.boredom < 25 and life.phone_active


def test_aquarium_growth_offline_feed_limits_collection_and_decor(phone):
    cmd(phone, 'take', app='aquarium')
    phone.advance_tank(100000 + 86400)
    t = phone.data.tank
    assert t.fish[0].age == 1 and t.fish[0].growth > 0
    assert t.food == 0 and t.clean == 37
    age = t.fish[0].age
    phone.advance_tank(100000)
    assert t.fish[0].age == age  # Clock rollback cannot grow fish twice.
    cmd(phone, 'feed', app='aquarium', now=186400)
    assert t.food == 40
    with pytest.raises(ValueError):
        cmd(phone, 'feed', app='aquarium', now=186401)
    cmd(phone, 'clean', app='aquarium', now=186400)
    cmd(phone, 'decorate', app='aquarium', decor='moon', now=186400)
    with pytest.raises(ValueError):
        cmd(phone, 'add_fish', app='aquarium', now=186400)
    phone.data.collection['goldfish'] = 2
    cmd(phone, 'add_fish', app='aquarium', species='goldfish', now=186400)
    cmd(phone, 'add_fish', app='aquarium', species='goldfish', now=186400)
    with pytest.raises(ValueError):
        cmd(phone, 'add_fish', app='aquarium', species='goldfish', now=186400)
    cmd(phone, 'return_fish', app='aquarium', species='goldfish', now=186400)
    assert phone.data.collection['goldfish'] == 2
    phone.persist()
    restored = Phone(phone.store, now=186400)
    assert len(restored.data.tank.fish) == 2 and restored.data.tank.decor == 'moon'
    assert restored.data.tank.fish[0].growth == t.fish[0].growth
    original_growth = t.fish[0].growth
    cmd(phone, 'return_fish', app='aquarium', species='minnow', now=186400)
    cmd(phone, 'add_fish', app='aquarium', species='minnow', now=186400)
    assert next(f for f in t.fish if f.species == 'minnow').growth == original_growth


def test_autonomy_choices_rest_chat_cooldown_and_disable(phone):
    life, env = Life(seed=1), Environment()
    life.needs.boredom = 90
    selected = set()
    for i in range(15):
        assert phone.choose(life, 100000 + i * 200)
        selected.add(phone.app)
        phone.stop(100000 + i * 200)
    assert selected == {'2048', 'fishing', 'aquarium'}
    phone.data.cooldown_until = 100300
    phone.tick(1, life, env, now=100200)
    assert phone.owner == 'none'
    phone.tick(1, life, env, now=100400)
    assert phone.owner == 'pet'
    env.interacting = True
    phone.tick(1, life, env, now=100401)
    assert phone.owner == 'none' and not life.phone_active
    env.interacting = False
    life.needs.energy = 10
    assert not phone.choose(life, 100600)
    life.needs.energy = 90
    cmd(phone, 'auto_off', now=100700)
    phone.tick(1, life, env, now=100800)
    assert phone.owner == 'none'
    cmd(phone, 'take', app='2048', now=100800)
    phone.tick(1, life, env, manual_present=False, now=100801)
    assert phone.owner == 'none'


def test_commands_validated_replay_denied_and_bad_save_preserved(phone):
    stale = {'action': 'take', 'revision': phone.data.revision}
    phone.command(stale, Life(), now=100000)
    with pytest.raises(RuntimeError):
        phone.command(stale, Life(), now=100000)
    with pytest.raises(ValueError):
        PhoneCommand.model_validate({'action': 'shell', 'revision': 0})
    with pytest.raises(ValueError):
        Puzzle(board=[[3]*4 for _ in range(4)])
    phone.store.put('phone', {'version': 77})
    restored = Phone(phone.store, now=100000)
    assert not restored.data.autonomous
    assert phone.store.get('phone_recovery') == {'version': 77}
