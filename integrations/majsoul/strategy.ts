/** Deterministic public-information baseline. Scores are heuristics, not win probabilities. */
import { createRequire } from 'node:module';
import { analyzeHand } from './hand.ts';
import { commandFor, type Observation, type Player } from './state.ts';

const { Shoupai, Util } = createRequire(import.meta.url)('@kobalab/majiang-core');
const base = (t: string) => t.replace(/^0/, '5');
const compact = (tiles: string[]) => ['m', 'p', 's', 'z'].map(s => s + tiles.filter(t => t[1] === s)
  .sort((a, b) => Number(base(a)[0]) - Number(base(b)[0])).map(t => t[0]).join('')).filter(s => s.length > 1).join('');
const own = (v: Observation) => v.players[v.seat!];
const native = (tiles: string[], melds: Player['melds']) => Shoupai.fromString([compact(tiles),
  ...melds.map(m => compact(m.tiles) + (m.kind === 'ankan' ? '' : '-'))].join(','));
const count = (tiles: string[], t: string) => tiles.filter(p => base(p) === base(t)).length;
const remove = (tiles: string[], t: string) => { const next = [...tiles]; const at = next.indexOf(t); if (at < 0) throw Error('Missing call tile'); next.splice(at, 1); return next; };

export interface Decision {
  args: { version: string; action: string; tile?: string; index?: number; tsumogiri?: boolean };
  mode: 'attack' | 'guard' | 'fold' | 'win';
  reason: string;
  candidates?: Array<{ tile: string; shanten: number; ukeire: number; risk: number; score: number }>;
}

export function doraFromIndicator(t: string, players: number): string {
  t = base(t);
  const n = Number(t[0]), s = t[1];
  if (players === 3 && s === 'm') return n === 1 ? '9m' : '1m';
  if (s !== 'z') return `${n % 9 + 1}${s}`;
  return `${n <= 4 ? n % 4 + 1 : (n - 4) % 3 + 5}z`;
}

function visible(v: Observation): Map<string, number> {
  const counts = new Map<string, number>();
  const add = (t: string, n = 1) => counts.set(base(t), (counts.get(base(t)) ?? 0) + n);
  v.hand.forEach(t => add(t)); v.doras.forEach(t => add(t));
  v.players.forEach(p => {
    p.discards.filter(d => !d.called).forEach(d => add(d.tile));
    p.melds.forEach(m => m.kind === 'ankan' && m.tiles.length === 1 ? add(m.tiles[0], 4) : m.tiles.forEach(t => add(t)));
    add('4z', p.norths);
  });
  return counts;
}

export function threats(v: Observation): Array<{ seat: number; weight: number }> {
  return v.players.flatMap((p, seat) => seat === v.seat ? [] : p.riichi ? [{ seat, weight: 1 }]
    : p.melds.filter(m => m.kind !== 'ankan').length >= 3 ? [{ seat, weight: .75 }] : []);
}

/** Zero only for current threats' own discards. Suji and walls merely reduce risk. */
export function tileRisk(v: Observation, tile: string): number {
  const t = base(tile), n = Number(t[0]), s = t[1], seen = visible(v);
  return threats(v).reduce((total, threat) => {
    const river = v.players[threat.seat].discards.map(d => base(d.tile));
    if (river.includes(t)) return total;
    let risk = s === 'z' ? ((seen.get(t) ?? 0) >= 3 ? 1 : (seen.get(t) ?? 0) === 2 ? 3 : 5)
      : [1, 9].includes(n) ? 5 : [2, 8].includes(n) ? 7 : 10;
    if (s !== 'z') {
      const suji = n <= 3 ? river.includes(`${n + 3}${s}`) : n >= 7 ? river.includes(`${n - 3}${s}`)
        : river.includes(`${n - 3}${s}`) && river.includes(`${n + 3}${s}`);
      if (suji) risk *= .65;
      if ([n - 1, n + 1].some(k => k >= 1 && k <= 9 && (seen.get(`${k}${s}`) ?? 0) >= 4)) risk *= .75;
      if (v.playerCount === 3 && s === 'm') risk *= .5;
    }
    if (v.doras.some(d => doraFromIndicator(d, v.playerCount ?? v.players.length) === t)) risk *= 1.25;
    return total + risk * threat.weight;
  }, 0);
}

function valueHonors(v: Observation): string[] {
  const wind = v.round ? `${(v.seat! - v.round.dealer + v.players.length) % v.players.length + 1}z` : '';
  return ['5z', '6z', '7z', wind, v.round ? `${v.round.wind + 1}z` : ''].filter(Boolean);
}

/** Only certain structural routes; unknown room-specific open tanyao is not assumed. */
function openYaku(v: Observation, hand: string[], melds: Player['melds']): boolean {
  const all = hand.concat(melds.flatMap(m => m.tiles));
  if (valueHonors(v).some(t => count(hand, t) >= 3 || melds.some(m => m.kind !== 'chi' && base(m.tiles[0]) === t))) return true;
  if (new Set(all.filter(t => t[1] !== 'z').map(t => t[1])).size <= 1) return true;
  // Four established triplets, including the three open ones, guarantee toitoi.
  return melds.every(m => m.kind !== 'chi') && melds.length >= 3 &&
    new Set(hand.map(base)).size <= 2 && hand.some(t => count(hand, t) >= 3);
}

/** Coarse retained value for push/fold and tie breaks, never a promised score. */
function handValue(v: Observation, hand: string[], melds = own(v).melds): number {
  const all = hand.concat(melds.flatMap(m => m.tiles));
  let value = melds.every(m => m.kind === 'ankan') ? 1 : 0;
  for (const t of valueHonors(v)) if (count(hand, t) >= 3 || melds.some(m => m.kind !== 'chi' && base(m.tiles[0]) === t)) value++;
  value += all.filter(t => t[0] === '0').length;
  for (const d of v.doras) value += count(all, doraFromIndicator(d, v.playerCount ?? v.players.length));
  if (v.playerCount === 3) value += own(v).norths;
  const suits = new Set(all.filter(t => t[1] !== 'z').map(t => t[1]));
  if (suits.size === 1 && all.length >= 10) value += all.some(t => t[1] === 'z') ? 2 : 4;
  return value;
}

function shape(tiles: string[]): number {
  let score = 0;
  for (const t of new Set(tiles.map(base))) {
    const n = Number(t[0]), s = t[1];
    if (s === 'z') continue;
    if (n <= 8 && tiles.some(p => base(p) === `${n + 1}${s}`)) score += n >= 2 && n <= 7 ? 3 : 1;
    if (n <= 7 && tiles.some(p => base(p) === `${n + 2}${s}`)) score++;
  }
  return score;
}

export function chooseAction(v: Observation): Decision | null {
  if (!v.active || !v.complete || !v.ready || v.seat === null || !v.operations.length) return null;
  const a = analyzeHand(v);
  if (!a) return null;
  const has = (name: string) => v.operations.some(o => o.name === name);
  const finish = (action: string, mode: Decision['mode'], reason: string, extra: Record<string, any> = {}): Decision => {
    const args = { version: v.version, action, ...extra };
    commandFor(v, args); // Actual server options are the final legality gate.
    return { args, mode, reason };
  };
  if (has('tsumo')) return finish('tsumo', 'win', '服务器允许自摸，直接和牌');
  if (has('ron')) return finish('ron', 'win', '服务器允许荣和，直接和牌');
  const candidates = a.discardCandidates;
  const best = candidates[0];
  const enemies = threats(v);
  const bestValue = best ? handValue(v, remove(v.hand, best.tile)) : handValue(v, v.hand);
  const shanten = best?.shanten ?? a.shanten;
  let mode: Decision['mode'] = 'attack';
  if (enemies.length) {
    const canPush = shanten === 0 && (best?.ukeire ?? 0) >= 4 && !best?.discardFuriten &&
      (enemies.length === 1 || bestValue >= 4);
    const canGuard = shanten <= 1 && (best?.ukeire ?? 0) >= 16 && bestValue >= 3 &&
      enemies.length === 1 && own(v).discards.length < 12;
    mode = canPush ? 'attack' : canGuard ? 'guard' : 'fold';
  }
  if (own(v).riichi && best && v.drawn) return finish('discard', 'attack', '立直后按合法摸切处理', { tile: v.drawn, tsumogiri: true });
  if (has('babei') && v.playerCount === 3 && mode !== 'fold') {
    const kokushi = a.kokushiShanten !== null && a.kokushiShanten <= a.regularShanten && a.kokushiShanten <= 3;
    const sevenPairs = a.sevenPairsShanten !== null && a.sevenPairsShanten <= 1 && count(v.hand, '4z') >= 2;
    if (!kokushi && !sevenPairs && count(v.hand, '4z') < 3) return finish('babei', mode, '北不承担国士或接近听牌的对子路线，拔北补牌');
  }
  if (!enemies.length && best && a.regularShanten <= (a.sevenPairsShanten ?? 99) && a.regularShanten <= (a.kokushiShanten ?? 99)) {
    for (const op of v.operations.filter(o => ['ankan', 'kakan'].includes(o.name))) {
      for (let index = 0; index < op.combinations.length; index++) {
        const t = op.combinations[index].split('|')[0];
        const next = structuredClone(v), melds = own(next).melds;
        if (op.name === 'ankan') {
          const four = next.hand.filter(p => base(p) === base(t));
          if (four.length !== 4) continue;
          four.forEach(p => { next.hand = remove(next.hand, p); });
          melds.push({ kind: 'ankan', tiles: four, froms: [] });
        } else {
          const meld = melds.find(m => m.kind === 'pon' && base(m.tiles[0]) === base(t));
          if (!meld || !next.hand.includes(t)) continue;
          next.hand = remove(next.hand, t); meld.kind = 'kakan'; meld.tiles.push(t);
        }
        if (Util.xiangting(native(next.hand, melds)) <= best.shanten) return finish(op.name, mode, '无明显听牌威胁，杠后不增加向听', { index });
      }
    }
  }
  if (candidates.length) {
    const ranked = candidates.map(c => {
      const hand = remove(v.hand, c.tile), risk = tileRisk(v, c.tile);
      const score = -c.shanten * 1000 + c.ukeire * 12 + shape(hand) * 2 + handValue(v, hand) * 15
        - (c.discardFuriten ? 160 : 0) - risk * (mode === 'guard' ? 65 : 10);
      return { ...c, risk, score };
    }).sort((x, y) => mode === 'fold' ? x.risk - y.risk || y.score - x.score : y.score - x.score);
    const chosen = ranked[0];
    const riichi = chosen.riichiAllowed && chosen.shanten === 0 && !chosen.discardFuriten && chosen.ukeire >= 2 &&
      mode !== 'fold' && (v.round?.leftTiles ?? 0) >= 8;
    const decision = finish(riichi ? 'riichi' : 'discard', mode,
      mode === 'fold' ? '面对听牌威胁，优先现物及较低风险牌' : riichi ? '合法听牌且有剩余进张，选择立直'
      : mode === 'guard' ? '有价值的一向听，兼顾进张与安全' : '综合向听、有效进张、搭子质量与保留价值', { tile: chosen.tile });
    decision.candidates = ranked.slice(0, 5).map(({ tile, shanten, ukeire, risk, score }) => ({ tile, shanten, ukeire, risk, score }));
    return decision;
  }
  // Calls are evaluated on the resulting hand, including the compulsory discard.
  const called = v.lastAction?.name === 'ActionDiscardTile' ? v.lastAction.tile : undefined;
  if (called && !enemies.length) {
    let bestCall: { name: string; index: number; shanten: number; ukeire: number } | undefined;
    for (const op of v.operations.filter(o => ['chi', 'pon'].includes(o.name))) {
      for (let index = 0; index < op.combinations.length; index++) {
        const held = op.combinations[index].split('|');
        if (held.length !== 2) continue;
        const next = structuredClone(v);
        try { held.forEach(t => { next.hand = remove(next.hand, t); }); } catch { continue; }
        own(next).melds.push({ kind: op.name, tiles: [...held, called], froms: [v.seat, v.seat, v.lastAction!.seat!] });
        if (!openYaku(next, next.hand, own(next).melds)) continue;
        // Conservative kuikae guard: exclude called tile and both alternative sequence ends.
        const forbidden = [base(called)];
        if (op.name === 'chi') for (const n of [Number(base(called)[0]) - 3, Number(base(called)[0]) + 3])
          if (n >= 1 && n <= 9) forbidden.push(`${n}${called[1]}`);
        next.operations = [{ type: 1, name: 'discard', combinations: [forbidden.join('|')] }]; next.drawn = null;
        const after = analyzeHand(next)?.discardCandidates[0];
        if (!after || after.shanten >= a.shanten || after.shanten > 1 || after.ukeire < 2) continue;
        if (!bestCall || after.shanten < bestCall.shanten || after.shanten === bestCall.shanten && after.ukeire > bestCall.ukeire)
          bestCall = { name: op.name, index, shanten: after.shanten, ukeire: after.ukeire };
      }
    }
    if (bestCall) return finish(bestCall.name, 'attack', '有明确副露役基础，鸣牌后降低向听并保留有效进张', { index: bestCall.index });
  }
  return finish('pass', mode, enemies.length ? '不为鸣牌破坏防守' : '鸣牌收益或役种基础不足，保留原手牌');
}
