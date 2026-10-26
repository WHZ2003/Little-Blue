import type { Page } from 'puppeteer-core';

/** A live object reference is the authority. URLs and restored sessions never confer ownership. */
export class OwnedVideoPage {
  private generation = 0;
  private released = false;
  closing = false;
  constructor(readonly page: Page) {}
  get eligible() { return !this.released && !this.page.isClosed(); }
  revoke() { this.released = true; this.generation++; }
  navigated(byWorld: boolean) { this.generation++; if (!byWorld) this.revoke(); }
  ticket() { return { owner: this, generation: this.generation, url: this.page.url() }; }
  matches(ticket: ReturnType<OwnedVideoPage['ticket']> | null) {
    return ticket?.owner === this && ticket.generation === this.generation && ticket.url === this.page.url() && !this.page.isClosed();
  }
  async observeUser() {
    // A page can only revoke its own lease, never grant one or close another page.
    await this.page.exposeFunction('__dafeiyuReleaseVideoPage', () => this.revoke());
    // String source avoids transpiler-injected helper names leaking into the page context.
    const install = `(() => {
      const root = window;
      root.__dafeiyuVideoTouched = false;
      const release = (event) => {
        if (!event.isTrusted) return;
        root.__dafeiyuVideoTouched = true;
        void root.__dafeiyuReleaseVideoPage();
      };
      for (const event of ['pointerdown', 'keydown', 'wheel', 'touchstart', 'input'])
        window.addEventListener(event, release, { capture: true, passive: true });
    })()`;
    await this.page.evaluateOnNewDocument(install);
    await this.page.evaluate(install);
  }
  async close(ticket = this.ticket()): Promise<'closed' | 'retained' | 'failed'> {
    if (!this.eligible || this.closing || !this.matches(ticket)) return 'retained';
    // Check in the renderer too: the binding notification may still be in flight.
    const untouched = await this.page.evaluate(() => (window as any).__dafeiyuVideoTouched === false).catch(() => false);
    if (!untouched || !this.eligible || this.closing || !this.matches(ticket)) return 'retained';
    this.closing = true;
    try {
      await this.page.close();
      return this.page.isClosed() ? 'closed' : 'failed';
    } catch { return this.page.isClosed() ? 'closed' : 'failed'; }
    finally { this.closing = false; }
  }
}

export const autoCloseVideoEnabled = () =>
  (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]?.values?.auto_close_video_tabs === true;
