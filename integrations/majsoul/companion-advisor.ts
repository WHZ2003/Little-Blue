import type { Observation } from './state.ts';
import { chooseAction, threats, type Decision } from './strategy.ts';

export interface Advice {
  version: string;
  suggestion: string;
  reason: string;
  mode: Decision['mode'];
  candidates?: Decision['candidates'];
}
const actions: Record<string, string> = { discard: '打出', riichi: '立直并打出', chi: '吃', pon: '碰',
  ankan: '暗杠', minkan: '大明杠', kakan: '加杠', tsumo: '自摸和牌', ron: '荣和',
  kyushu: '九种九牌流局', babei: '拔北', pass: '跳过这次鸣牌机会' };
const tileText = (tile: string) => tile[1] === 'z' ? ['东', '南', '西', '北', '白', '发', '中'][Number(tile[0]) - 1]
  : `${tile[0] === '0' ? '红五' : tile[0]}${({ m: '万', p: '筒', s: '索' } as Record<string, string>)[tile[1]]}`;

/** Only accepts observations: this class has no browser or execution capability. */
export class CompanionAdvisor {
  private version = '';
  private advice: Advice | null = null;
  private spokenKey = '';
  private lastSpoke = -Infinity;
  private lastThreats = '';
  reset() {
    this.version = ''; this.advice = null; this.spokenKey = ''; this.lastSpoke = -Infinity; this.lastThreats = '';
  }
  current(view: Observation): Advice | null {
    if (!view.active || !view.complete || !view.ready) return null;
    if (view.version === this.version) return this.advice;
    this.version = view.version;
    this.advice = null;
    try {
      const decision = chooseAction(view);
      if (decision) {
        const { action, tile, index } = decision.args;
        const combination = index === undefined ? '' : view.operations.find(op => op.name === action)?.combinations[index];
        this.advice = { version: view.version,
          suggestion: `${actions[action] ?? action}${tile ? ' ' + tileText(tile) : combination ? ' ' + combination.split('|').map(tileText).join('、') : ''}`,
          reason: decision.reason, mode: decision.mode, candidates: decision.candidates?.slice(0, 3) };
      }
    } catch { /* Incomplete/unsupported observations must not break the bridge. */ }
    return this.advice;
  }
  /** Rechecks the latest view after a cooldown, never queues an old tile suggestion. */
  shouldSpeak(view: Observation, intervalSec: number, now = Date.now()): boolean {
    const ended = ['ActionHule', 'ActionNoTile', 'ActionLiuJu'].includes(view.lastAction?.name ?? '');
    if (!view.complete || (!view.active && !ended)) return false;
    const threatKey = JSON.stringify(threats(view));
    const changedThreat = threatKey !== this.lastThreats && threatKey !== '[]';
    const advice = this.current(view);
    if (!advice && !changedThreat && !ended && view.lastAction?.name !== 'ActionNewRound') return false;
    const key = JSON.stringify([view.round, view.operationReceivedAt, view.hand, view.operations, view.lastAction, threatKey]);
    if (key === this.spokenKey) return false;
    const urgent = changedThreat || ended || advice?.mode === 'win';
    const interval = Number.isFinite(intervalSec) ? Math.max(5, Math.min(60, intervalSec)) : 10;
    if (now - this.lastSpoke < (urgent ? 3000 : interval * 1000)) return false;
    this.spokenKey = key; this.lastSpoke = now; this.lastThreats = threatKey;
    return true;
  }
}
