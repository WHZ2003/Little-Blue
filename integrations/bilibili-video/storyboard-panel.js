/** Extend the installed playback panel; retain upstream sign-in and browser controls. */
export function augmentVideoPanel(bundle) {
  const original = bundle.panels.player;
  return { ...bundle, panels: { ...bundle.panels, player: { mount(ctx) {
    const { ui, root, signal } = ctx;
    const card = ui.sheet({ title: '省流分镜', desc: '把一段画面合成一张图，附上时间与字幕。需要看清细节时，大肥鱼仍可单独查看。' });
    const row = ui.h('div'); row.style.cssText = 'display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:12px 0';
    const toggleLabel = ui.h('label'); toggleLabel.style.cssText = 'display:flex;align-items:center;gap:8px';
    const enabled = ui.h('input'); enabled.type = 'checkbox'; enabled.setAttribute('aria-label', '启用省流分镜');
    toggleLabel.append(enabled, ui.h('span', null, '启用省流分镜'));
    const intervalLabel = ui.h('label'); intervalLabel.style.cssText = 'display:flex;align-items:center;gap:8px';
    const interval = ui.h('input'); interval.type = 'number'; interval.min = '5'; interval.max = '120'; interval.step = '1'; interval.required = true;
    interval.style.cssText = 'width:80px;padding:7px;border:1px solid var(--border,#ccc);border-radius:6px;background:transparent;color:inherit';
    interval.setAttribute('aria-label', '分镜间隔（秒）'); intervalLabel.append(ui.h('span', null, '每'), interval, ui.h('span', null, '秒观察一次'));
    const save = ui.button('应用分镜设置', { variant: 'primary', onClick: () => void saveSettings() });
    row.append(toggleLabel, intervalLabel, save);
    const status = ui.h('p'); status.setAttribute('role', 'status'); status.style.lineHeight = '1.7';
    const message = ui.msgline(); message.setAttribute('role', 'status');
    const note = ui.h('p', null, '首次约 2 秒出图，之后按间隔观察。最近分镜显示在下方，字幕随观察结果发送给大肥鱼。关闭可恢复原观察方式。');
    note.style.cssText = 'font-size:13px;opacity:.7;line-height:1.7';
    card.body.append(row, status, message, note); root.append(card.el);
    let state = null, dirty = false, busy = false, polling = false, revision = 0, failed = false;
    function render() {
      const ready = !!state?.storyboard;
      enabled.disabled = busy || !ready || failed;
      interval.disabled = busy || !ready || failed || !enabled.checked;
      save.disabled = busy || !ready || failed || !dirty;
      if (!ready) { status.textContent = '等待省流状态；若界面刚更新，请重启大肥鱼后刷新。'; return; }
      const s = state.storyboard;
      status.textContent = `${s.enabled ? '省流分镜已开启 · 整图最多 64 万像素' : '使用原观察方式'} · 实际观察间隔至少 ${s.effectiveIntervalSec} 秒 · 已生成 ${state.scenes || 0} 张${state.lastMontageCells ? ` · 最近 ${state.lastMontageCells} 格` : ''}`;
    }
    function accept(value) {
      if (failed) message.textContent = '';
      state = value; failed = false;
      if (!dirty && state.storyboard) { enabled.checked = state.storyboard.enabled; interval.value = String(state.storyboard.intervalSec); }
      render();
    }
    function error(e) { failed = true; message.textContent = '设置或状态未确认：' + String(e?.message || e); render(); }
    async function load() {
      if (busy || polling || signal.aborted) return;
      polling = true; const version = revision;
      try { const value = await ctx.invoke('state'); if (!signal.aborted && version === revision) accept(value); }
      catch (e) { if (!signal.aborted && version === revision) error(e); }
      finally { polling = false; }
    }
    async function saveSettings() {
      if (busy || signal.aborted || !state?.storyboard || (enabled.checked && !interval.reportValidity())) return;
      busy = true; revision++; render(); message.textContent = '正在保存…';
      try {
        const patch = { 'worlds.bilibili-video.observe.storyboard': enabled.checked };
        if (enabled.checked) patch['worlds.bilibili-video.observe.storyboardIntervalSec'] = Number(interval.value);
        await ctx.setConfig('world:bilibili-video', patch);
        const value = await ctx.invoke('state');
        if (signal.aborted) return;
        dirty = false; accept(value); message.textContent = '已保存，下一次观察使用新设置。';
      } catch (e) { if (!signal.aborted) error(e); }
      finally { busy = false; if (!signal.aborted) render(); }
    }
    for (const input of [enabled, interval]) input.addEventListener('input', () => { dirty = true; message.textContent = '有未保存的设置'; render(); }, { signal });
    ctx.guardLeave?.(() => dirty ? '分镜设置尚未保存，确定离开？' : null);
    render(); void load(); ctx.interval(() => void load(), 2000);
    return original.mount(ctx);
  } } } };
}
