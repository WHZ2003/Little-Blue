"""Offline virtual phone: real game rules, bounded local policy, durable saves.

No network, model, host filesystem tools, or background execution on restore.
"""
import math
import random
import time
from typing import Literal

from pydantic import Field, field_validator
from .models import Model, Action

DIRECTIONS = ('left', 'down', 'right', 'up')
APPS = {'2048': '2048', 'fishing': '浮漂钓鱼', 'aquarium': '小小水族箱'}
SPECIES = {'minnow': ('银色小鱼', '#b8dce8', 45), 'goldfish': ('金鱼', '#ffba66', 28),
           'guppy': ('孔雀鱼', '#9bd4bb', 16), 'angelfish': ('神仙鱼', '#b8b1ee', 7),
           'moonfish': ('月光鱼', '#a2e5f7', 3), 'whale': ('迷你鲸鱼', '#668ccd', 1)}
DECORS = {'garden': '水草庭院', 'coral': '珊瑚小窝', 'moon': '月光海湾'}


class GameError(ValueError):
    """A bounded, user-facing game rule explanation, never an external exception."""


def slide(board, direction):
    """Pure move; equal tiles merge once, spawn handled only after a changed move."""
    if direction not in DIRECTIONS:
        raise ValueError('无效方向')
    result = [row[:] for row in board]
    score = 0
    for line in range(4):
        coords = ([(line, j) for j in range(4)] if direction in ('left', 'right') else
                  [(j, line) for j in range(4)])
        if direction in ('right', 'down'):
            coords.reverse()
        values = [board[r][c] for r, c in coords if board[r][c]]
        merged, i = [], 0
        while i < len(values):
            if i + 1 < len(values) and values[i] == values[i + 1]:
                value = values[i] * 2
                merged.append(value)
                score += value
                i += 2
            else:
                merged.append(values[i])
                i += 1
        merged += [0] * (4 - len(merged))
        for (r, c), value in zip(coords, merged):
            result[r][c] = value
    return result, score, result != board


def board_value(board):
    logs = [[math.log2(v) if v else 0 for v in row] for row in board]
    empty = sum(v == 0 for row in board for v in row)
    smooth = sum(abs(logs[r][c] - logs[nr][nc])
                 for r in range(4) for c in range(4)
                 for nr, nc in ((r + 1, c), (r, c + 1))
                 if nr < 4 and nc < 4 and logs[r][c] and logs[nr][nc])
    maximum = max(map(max, logs))
    corner = maximum if maximum in (logs[0][0], logs[0][3], logs[3][0], logs[3][3]) else 0
    return empty * 12 + corner * 4 - smooth * .7


def choose_direction(board):
    """One move plus expected spawn and a next-move evaluation; no random moves."""
    best = None
    best_value = float('-inf')
    for direction in DIRECTIONS:
        moved, gain, changed = slide(board, direction)
        if not changed:
            continue
        empty = [(r, c) for r in range(4) for c in range(4) if not moved[r][c]]
        expected = 0
        for r, c in empty:
            for value, probability in ((2, .9), (4, .1)):
                moved[r][c] = value
                options = [(board_value(b) + g * .08) for d in DIRECTIONS
                           for b, g, ok in [slide(moved, d)] if ok]
                expected += probability * (max(options) if options else -1000) / len(empty)
            moved[r][c] = 0
        utility = gain * .15 + (expected if empty else board_value(moved))
        if utility > best_value:
            best, best_value = direction, utility
    return best


class Puzzle(Model):
    board: list[list[int]] = Field(default_factory=lambda: [[0] * 4 for _ in range(4)])
    score: int = Field(0, ge=0)
    best: int = Field(0, ge=0)
    pet_best: int = Field(0, ge=0)
    moves: int = Field(0, ge=0)
    assisted: bool = False
    over: bool = False
    last_move: str = ''

    @field_validator('board')
    @classmethod
    def valid_board(cls, board):
        if len(board) != 4 or any(len(row) != 4 for row in board):
            raise ValueError('Board must be 4x4')
        if any(v < 0 or v == 1 or v > 2**30 or v and v & (v - 1) for row in board for v in row):
            raise ValueError('Tiles must be powers of two')
        return board


class Fish(Model):
    species: Literal['minnow', 'goldfish', 'guppy', 'angelfish', 'moonfish', 'whale']
    age: float = Field(0, ge=0)
    growth: float = Field(0, ge=0, le=100)


class Tank(Model):
    fish: list[Fish] = Field(default_factory=lambda: [Fish(species='minnow')], max_length=8)
    food: float = Field(75, ge=0, le=100)
    clean: float = Field(85, ge=0, le=100)
    decor: Literal['garden', 'coral', 'moon'] = 'garden'
    updated: float = 0
    last_feed: float = 0
    last_clean: float = 0
    day: str = ''


class PhoneSave(Model):
    version: Literal[1] = 1
    revision: int = Field(0, ge=0)
    autonomous: bool = True
    puzzle: Puzzle = Field(default_factory=Puzzle)
    tank: Tank = Field(default_factory=Tank)
    collection: dict[str, int] = Field(default_factory=lambda: {'minnow': 1})
    catches: int = Field(0, ge=0)
    journal: list[str] = Field(default_factory=list, max_length=30)
    cooldown_until: float = 0
    last_app: str = ''
    recent_apps: list[Literal['2048', 'fishing', 'aquarium']] = Field(default_factory=list, max_length=2)
    resting_fish: list[Fish] = Field(default_factory=list, max_length=64)

    @field_validator('collection')
    @classmethod
    def valid_collection(cls, value):
        if any(k not in SPECIES or n < 0 or n > 10**9 for k, n in value.items()):
            raise ValueError('Invalid collection')
        return value


class PhoneCommand(Model):
    action: Literal['take', 'release', 'choose', 'auto_on', 'auto_off', 'move', 'new',
                    'cast', 'reel', 'feed', 'clean', 'decorate', 'add_fish', 'return_fish']
    app: Literal['2048', 'fishing', 'aquarium'] = '2048'
    direction: Literal['left', 'right', 'up', 'down'] = 'left'
    decor: Literal['garden', 'coral', 'moon'] = 'garden'
    species: Literal['minnow', 'goldfish', 'guppy', 'angelfish', 'moonfish', 'whale'] = 'minnow'
    revision: int = Field(ge=0)


class Phone:
    def __init__(self, store, seed=None, now=None):
        self.store, self.rng = store, random.Random(seed)
        now = time.time() if now is None else now
        raw = store.get('phone')
        try:
            self.data = PhoneSave.model_validate(raw or {})
        except ValueError:
            # Preserve the invalid save for recovery rather than silently overwriting it.
            store.put('phone_recovery', raw)
            self.data = PhoneSave(autonomous=False)
            store.audit('phone', 'invalid_save_preserved')
        self.owner = 'none'
        self.app = None
        self.reason = '手机放在身边，等想玩的时候再拿起来。'
        self.elapsed = self.step_elapsed = 0.0
        self.next_check = now + 30
        self.session_best = self.data.puzzle.pet_best
        self.phase = 'idle'
        self.fishing_time = self.bite_at = self.nibble_at = 0.0
        self.last_catch = ''
        self.save_elapsed = 0.0
        self.data.tank.updated = self.data.tank.updated or now
        self.data.tank.day = self.data.tank.day or time.strftime('%Y-%m-%d', time.localtime(now))
        if not any(map(any, self.data.puzzle.board)):
            self.spawn()
            self.spawn()
        self.advance_tank(now)
        self.persist()

    def log(self, text, memory=False):
        self.data.journal = (self.data.journal + [text])[-30:]
        if memory:
            self.store.remember('虚拟手机游戏：' + text)

    def summary(self):
        d = self.data
        return (f"正在：{APPS.get(self.app, '休息')}；{self.reason}\n"
                f"2048 本局 {d.puzzle.score} 分，自主最高 {d.puzzle.pet_best} 分，全部最高 {d.puzzle.best} 分。\n"
                f"钓到 {d.catches} 条鱼；收藏 " + '、'.join(f'{SPECIES[k][0]}×{v}' for k, v in d.collection.items() if v) +
                f"。鱼缸 {len(d.tank.fish)}/8 条，饱食 {d.tank.food:.0f}，清洁 {d.tank.clean:.0f}，{DECORS[d.tank.decor]}。")

    def persist(self):
        self.data.revision += 1
        self.store.put('phone', self.data.model_dump())
        self.store.put('phone_summary', self.summary())
        self.save_elapsed = 0

    def snapshot(self):
        return self.data.model_dump() | {'owner': self.owner, 'app': self.app, 'reason': self.reason,
            'fishing': {'phase': self.phase, 'last_catch': self.last_catch}, 'summary': self.summary()}

    def spawn(self):
        board = self.data.puzzle.board
        empty = [(r, c) for r in range(4) for c in range(4) if not board[r][c]]
        if empty:
            r, c = self.rng.choice(empty)
            board[r][c] = 2 if self.rng.random() < .9 else 4

    def move(self, direction):
        p = self.data.puzzle
        board, gained, changed = slide(p.board, direction)
        if not changed:
            return False
        p.board, p.score, p.moves = board, p.score + gained, p.moves + 1
        p.last_move = direction
        p.assisted |= self.owner == 'user'
        self.spawn()
        p.best = max(p.best, p.score)
        if not p.assisted:
            p.pet_best = max(p.pet_best, p.score)
        p.over = not any(slide(p.board, d)[2] for d in DIRECTIONS)
        return True

    def new_puzzle(self):
        p = self.data.puzzle
        self.record_score()
        self.data.puzzle = Puzzle(best=p.best, pet_best=p.pet_best, assisted=self.owner == 'user')
        self.spawn()
        self.spawn()

    def record_score(self):
        if self.data.puzzle.pet_best > self.session_best:
            self.log(f"我自己玩 2048 的最高纪录提高到 {self.data.puzzle.pet_best} 分。", True)
            self.session_best = self.data.puzzle.pet_best

    def cast(self):
        if self.phase in ('waiting', 'nibble', 'bite'):
            raise GameError('鱼线已经在水里了')
        self.phase, self.fishing_time = 'waiting', 0
        self.nibble_at = self.rng.uniform(3, 7)
        self.bite_at = self.nibble_at + self.rng.uniform(2, 4)
        self.last_catch = ''

    def reel(self):
        if self.phase not in ('waiting', 'nibble', 'bite'):
            raise GameError('先抛竿，再观察浮漂')
        if self.phase != 'bite':
            self.phase, self.last_catch = 'empty', '收竿太早，鱼还没咬钩。'
            return False
        species = self.rng.choices(list(SPECIES), weights=[s[2] for s in SPECIES.values()])[0]
        first = not self.data.collection.get(species, 0)
        self.data.collection[species] = self.data.collection.get(species, 0) + 1
        self.data.catches += 1
        self.phase, self.last_catch = 'caught', '钓到了' + SPECIES[species][0] + '！'
        who = '我' if self.owner == 'pet' else '用户'
        self.log(f"{who}钓到了{SPECIES[species][0]}，已收入收藏。", first)
        return True

    def advance_tank(self, now):
        t = self.data.tank
        # Bounded catch-up; no offline catches, scores, or replay of game actions.
        hours = min(168, max(0, now - t.updated) / 3600)
        if hours <= 0:
            return
        healthy_hours = min(hours, max(0, (t.food - 20) / 5), max(0, (t.clean - 25) / 2))
        for fish in t.fish:
            fish.age += hours / 24
            fish.growth = min(100, fish.growth + healthy_hours * .7)
        t.food = max(0, t.food - hours * 5)
        t.clean = max(0, t.clean - hours * 2)
        t.updated = now
        day = time.strftime('%Y-%m-%d', time.localtime(now))
        if day != t.day:
            t.day = day
            self.log(f"{day}：鱼缸的小鱼又长大了一点。" if healthy_hours > 0 and t.fish else
                     f"{day}：看看鱼缸，给小鱼们补充食物和清水吧。")

    def care(self, action, now, species='minnow', decor='garden'):
        t = self.data.tank
        if action == 'feed':
            if now - t.last_feed < 1800 or t.food > 80:
                raise GameError('小鱼还不饿，晚点再喂吧')
            t.food, t.last_feed = min(100, t.food + 40), now
            self.log('给小鱼喂了食。')
        elif action == 'clean':
            if now - t.last_clean < 1800 or t.clean > 80:
                raise GameError('水还很干净，不用急着换水')
            t.clean, t.last_clean = 100, now
            self.log('清理了鱼缸，水又透亮了。')
        elif action == 'decorate':
            if t.decor != decor:
                t.decor = decor
                self.log('把小窝布置成了' + DECORS[decor] + '。')
        elif action == 'add_fish':
            in_tank = sum(f.species == species for f in t.fish)
            if len(t.fish) >= 8 or in_tank >= self.data.collection.get(species, 0):
                raise GameError('鱼缸已满，或收藏里没有多余的这条鱼')
            grown = next((f for f in self.data.resting_fish if f.species == species), None)
            if grown is not None:
                self.data.resting_fish.remove(grown)
            t.fish.append(grown if grown is not None else Fish(species=species))
            self.log(SPECIES[species][0] + '搬进了鱼缸。')
        elif action == 'return_fish':
            fish = next((f for f in t.fish if f.species == species), None)
            if fish is None:
                raise GameError('鱼缸里没有这种鱼')
            if len(self.data.resting_fish) >= 64:
                raise GameError('暂养区已满，先让收藏里的鱼回到鱼缸吧')
            t.fish.remove(fish)
            self.data.resting_fish.append(fish)
        else:
            raise ValueError('无效的水族箱操作')

    def stop(self, now, reason='放下手机，活动一下。'):
        self.record_score()
        self.owner, self.app, self.phase = 'none', None, 'idle'
        self.reason = reason
        self.data.cooldown_until = now + 120
        self.persist()

    def choose(self, life, now):
        if not self.data.autonomous or life.needs.energy < 30 or life.action in (Action.SLEEP, Action.DRAG):
            return False
        n, t = life.needs, self.data.tank
        scores = {'2048': n.boredom + n.energy * .2,
                  'fishing': n.curiosity * .7 + n.boredom * .5,
                  'aquarium': (100 - t.food) + (100 - t.clean) * .7 + 10}
        # Variation and a penalty for the previous game prevent a fixed rotation.
        scores = {k: v * (.25 if k in self.data.recent_apps else 1) + self.rng.uniform(0, 12)
                  for k, v in scores.items()}
        self.app = max(scores, key=scores.get)
        self.owner = 'pet'
        self.elapsed = self.step_elapsed = 0
        self.session_best = self.data.puzzle.pet_best
        self.data.last_app = self.app
        self.data.recent_apps = (self.data.recent_apps + [self.app])[-2:]
        self.reason = {'2048': '有点无聊，想看看棋盘还能合出多大的数。',
                       'fishing': '想观察浮漂，看看今天会遇到什么鱼。',
                       'aquarium': '去看看小鱼饿不饿，顺便整理小窝。'}[self.app]
        if self.app == '2048' and self.data.puzzle.over:
            self.new_puzzle()
        self.log('拿起手机，打开' + APPS[self.app] + '。')
        self.persist()
        return True

    def command(self, raw, life, now=None):
        cmd = PhoneCommand.model_validate(raw)
        now = time.time() if now is None else now
        if cmd.revision != self.data.revision:
            raise RuntimeError('手机状态刚刚更新，请看清最新状态再操作。')
        self.advance_tank(now)
        if cmd.action in ('auto_on', 'auto_off'):
            self.data.autonomous = cmd.action == 'auto_on'
            if self.owner == 'pet' and not self.data.autonomous:
                self.stop(now, '自主玩手机已关闭。')
        elif cmd.action == 'take':
            self.record_score()
            self.owner, self.app, self.phase = 'user', cmd.app, 'idle'
            self.reason = '你接过手机了；玩完可以交还给她。'
            self.elapsed = 0
        elif cmd.action == 'release':
            self.stop(now)
        elif cmd.action == 'choose':
            if self.owner == 'user':
                self.stop(now)
            if self.owner == 'pet':
                return self.snapshot()
            if not self.choose(life, now):
                raise GameError('她现在需要休息，或自主玩手机已关闭。')
        else:
            if self.owner != 'user' or self.app != cmd.app:
                raise PermissionError('请先接过这个游戏，再操作')
            if cmd.app == '2048' and cmd.action in ('move', 'new'):
                self.move(cmd.direction) if cmd.action == 'move' else self.new_puzzle()
            elif cmd.app == 'fishing' and cmd.action in ('cast', 'reel'):
                self.cast() if cmd.action == 'cast' else self.reel()
            elif cmd.app == 'aquarium':
                self.care(cmd.action, now, cmd.species, cmd.decor)
            else:
                raise ValueError('此游戏不支持这个操作')
        self.persist()
        return self.snapshot()

    def tick(self, dt, life, env, busy=False, manual_present=True, now=None):
        now = time.time() if now is None else now
        dt = max(0, min(1, dt))
        self.advance_tank(now)
        self.save_elapsed += dt
        if self.owner == 'user' and not manual_present:
            self.stop(now, '手机收起来了，游戏进度已经保存。')
        if self.owner == 'pet' and (busy or env.interacting or life.needs.energy < 20 or
                                    life.action in (Action.SLEEP, Action.DRAG)):
            self.stop(now, '先放下手机，休息或陪你说话。')
        if self.owner == 'none' and now >= max(self.next_check, self.data.cooldown_until):
            self.next_check = now + 30
            t = self.data.tank
            curious = life.needs.curiosity >= 60 and self.rng.random() < .22
            if not busy and not env.interacting and (life.needs.boredom >= 45 or curious or
                                                    life.goal == 'play' or t.food < 40 or t.clean < 40):
                self.choose(life, now)
        life.phone_active = self.owner == 'pet'
        if self.owner != 'none':
            self.elapsed += dt
            self.step_elapsed += dt
            if self.app == 'fishing' and self.phase in ('waiting', 'nibble', 'bite'):
                self.fishing_time += dt
                old = self.phase
                if self.fishing_time > self.bite_at + 2:
                    self.phase, self.last_catch = 'escaped', '错过咬钩，小鱼游走了。'
                elif self.fishing_time >= self.bite_at:
                    self.phase = 'bite'
                elif self.fishing_time >= self.nibble_at:
                    self.phase = 'nibble'
                if old != self.phase:
                    self.persist()
            if self.owner == 'pet':
                life.needs.change('boredom', -.6 * dt)
                life.needs.change('energy', -.06 * dt)
                life.needs.change('mood', .025 * dt)
                if self.elapsed >= 90:
                    self.stop(now)
                    life.phone_active = False
                elif self.step_elapsed >= 2:
                    self.step_elapsed = 0
                    self.pet_step(now)
            elif self.elapsed > 900:
                self.stop(now, '这次试玩先收起来了，下次可以继续。')
        if self.save_elapsed >= 15:
            self.persist()

    def pet_step(self, now):
        if self.app == '2048':
            direction = choose_direction(self.data.puzzle.board)
            if direction is None:
                self.data.puzzle.over = True
                self.log(f'这局 2048 结束，得到 {self.data.puzzle.score} 分。')
                self.stop(now)
            else:
                self.move(direction)
        elif self.app == 'fishing':
            if self.phase == 'bite':
                self.reel()
            elif self.phase in ('idle', 'caught', 'empty', 'escaped'):
                self.cast()
        elif self.app == 'aquarium':
            t = self.data.tank
            if t.food < 60 and now - t.last_feed >= 1800:
                self.care('feed', now)
            elif t.clean < 60 and now - t.last_clean >= 1800:
                self.care('clean', now)
            else:
                available = [k for k, n in self.data.collection.items()
                             if n > sum(f.species == k for f in t.fish)]
                if available and len(t.fish) < 5:
                    self.care('add_fish', now, self.rng.choice(available))
                elif self.elapsed >= 8:
                    # One arrangement per day, not a constantly flickering aquarium.
                    if self.store.get('phone_decor_day') != t.day:
                        self.care('decorate', now, decor=self.rng.choice(list(DECORS)))
                        self.store.put('phone_decor_day', t.day)
                    self.stop(now, '小窝整理好了，看看小鱼游一会儿。')
        self.persist()
