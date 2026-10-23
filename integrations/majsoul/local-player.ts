import type { Observation } from './state.ts';
import { chooseAction, type Decision } from './strategy.ts';

export interface DecisionRecord {
  at: string;
  decision?: Decision;
  calculationMs: number;
  outcome: 'confirmed' | 'unconfirmed' | 'failed';
  result?: unknown;
  error?: string;
}
interface Game {
  connected: boolean;
  busy: boolean;
  snapshot(): Observation;
  execute(args: Decision['args'], timeout: number, signal?: AbortSignal): Promise<unknown>;
}

/** One executor per World; a rejected/uncertain operation window is never retried. */
export class LocalPlayer {
  private running = false;
  private inflight = false;
  private attempted = new Set<string>();
  private timer?: ReturnType<typeof setInterval>;
  private abort?: AbortController;
  last?: DecisionRecord;
  constructor(private game: Game, private allowed: () => boolean, private timeout: () => number,
    private record: (record: DecisionRecord) => void = () => {}) {}

  start() {
    if (this.running) return;
    this.running = true;
    // Also notices a permission change when no new game frame arrives.
    this.timer = setInterval(() => this.tick(), 100);
    this.timer.unref?.();
    this.tick();
  }
  stop() {
    this.running = false;
    clearInterval(this.timer);
    this.abort?.abort();
  }
  tick() {
    if (!this.running || !this.allowed()) { this.abort?.abort(); return; }
    if (this.inflight || !this.game.connected || this.game.busy) return;
    const view = this.game.snapshot();
    if (!view.active || !view.complete || !view.ready) return;
    // A server rejection can change version while restoring the same operation.
    const key = JSON.stringify([view.operationReceivedAt, view.round, view.seat, view.hand, view.operations, view.lastAction]);
    if (this.attempted.has(key)) return;
    this.attempted.add(key);
    if (this.attempted.size > 512) this.attempted.delete(this.attempted.values().next().value!);
    this.inflight = true;
    this.abort = new AbortController();
    void this.play(view, this.abort.signal);
  }
  private async play(view: Observation, signal: AbortSignal) {
    const started = performance.now();
    let decision: Decision | undefined;
    let calculationMs = 0;
    let record: DecisionRecord | undefined;
    try {
      decision = chooseAction(view) ?? undefined;
      calculationMs = performance.now() - started;
      if (!decision || !this.running || !this.allowed() || signal.aborted) return;
      const result = await this.game.execute(decision.args, this.timeout(), signal) as { outcome?: string };
      const confirmed = result?.outcome === 'state_changed' || result?.outcome === 'server_accepted';
      record = { at: new Date().toISOString(), decision, calculationMs, outcome: confirmed ? 'confirmed' : 'unconfirmed', result };
    } catch (error) {
      record = { at: new Date().toISOString(), decision, calculationMs: calculationMs || performance.now() - started,
        outcome: 'failed', error: error instanceof Error ? error.message : String(error) };
    } finally {
      this.inflight = false;
      if (record) {
        this.last = record;
        // A logging or commentary failure must never cause another game action.
        try { this.record(record); } catch { /* last remains available in status */ }
      }
      // State callbacks may have arrived while execute was resolving.
      if (this.running) queueMicrotask(() => this.tick());
    }
  }
}
