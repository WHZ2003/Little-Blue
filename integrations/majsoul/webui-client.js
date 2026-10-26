// DaFeiYu companion panel; all values come from the running World.
export default {
  panels: {
    game: {
      mount(ctx) {
        const { root, ui, signal } = ctx;
        const el = (tag, cls, text) => ui.h(tag, cls, text);
        const panel = el('div', 'ms-companion');
        const style = el('style');
        style.textContent = `
          .ms-companion{max-width:1000px;margin:8px auto 24px;display:grid;gap:18px;font:inherit}
          .ms-companion *{box-sizing:border-box}.ms-companion [hidden]{display:none!important}
          .ms-companion .ms-card{border:1px solid var(--border,#ddd);border-radius:14px;padding:22px;background:color-mix(in srgb,currentColor 2%,transparent)}
          .ms-companion .ms-top,.ms-companion .ms-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
          .ms-companion .ms-top{justify-content:space-between}.ms-companion h2{font-size:26px;line-height:1.25;margin:0 0 8px}
          .ms-companion h3{font-size:16px;margin:0 0 12px}.ms-companion p{margin:0;line-height:1.65}
          .ms-companion .ms-muted{opacity:.68;font-size:13px}.ms-companion .ms-tag{border:1px solid var(--border,#ddd);border-radius:99px;padding:5px 11px;font-size:12px;white-space:nowrap}
          .ms-companion .ms-tag[data-on=true]{color:#158474;border-color:#43a99b;background:color-mix(in srgb,#24a58e 9%,transparent)}
          .ms-companion .ms-sources{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:22px 0 14px}
          .ms-companion .ms-source{appearance:none;text-align:left;font:inherit;color:inherit;background:transparent;border:1px solid var(--border,#ddd);border-radius:10px;padding:16px;cursor:pointer;display:grid;gap:6px}
          .ms-companion .ms-source[aria-pressed=true]{border-color:#259c8a;background:color-mix(in srgb,#24a58e 8%,transparent);box-shadow:inset 0 0 0 1px #259c8a}
          .ms-companion button:focus-visible,.ms-companion input:focus-visible{outline:2px solid #259c8a;outline-offset:3px}
          .ms-companion button:disabled{cursor:default;opacity:.5}.ms-companion .ms-source strong{font-size:15px}
          .ms-companion .ms-note{font-size:13px;line-height:1.65;opacity:.72;margin:10px 0 18px}
          .ms-companion .ms-primary{background:#167d70!important;border-color:#167d70!important;color:white!important;min-width:150px;min-height:40px;border-radius:8px}
          .ms-companion .ms-actions .btn{min-height:38px}.ms-companion .ms-spacer{flex:1}
          .ms-companion .ms-status{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:20px;padding-top:20px;border-top:1px solid var(--border,#ddd)}
          .ms-companion .ms-value{display:block;margin-top:6px;font-size:15px;font-weight:600;overflow-wrap:anywhere}
          .ms-companion .ms-status-label{font-size:12px;opacity:.65}.ms-companion .ms-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:18px}
          .ms-companion .ms-quote{font-size:17px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere;min-height:64px}
          .ms-companion .ms-foot{margin-top:12px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
          .ms-companion .ms-form-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:14px 0}
          .ms-companion .ms-number{width:85px;padding:8px;border:1px solid var(--border,#ddd);border-radius:7px;color:inherit;background:transparent;font:inherit}
          .ms-companion .ms-preset{min-height:30px!important;font-size:12px}.ms-companion .ms-error{padding:12px 14px;border:1px solid #d99a67;border-radius:9px;background:color-mix(in srgb,#d99a67 8%,transparent);line-height:1.6}
          .ms-companion .ms-browser{margin-top:16px;padding-top:16px;border-top:1px solid var(--border,#ddd)}
          .ms-companion .ms-debug{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:380px;overflow:auto}
          .ms-companion .ms-handle{width:100%;max-width:300px;margin:10px 0}.ms-companion a{color:inherit;text-decoration:underline;text-underline-offset:3px}
          @media(max-width:650px){.ms-companion .ms-grid,.ms-companion .ms-sources{grid-template-columns:1fr}.ms-companion .ms-card{padding:16px}.ms-companion .ms-status{gap:12px;grid-template-columns:1fr 1fr}.ms-companion h2{font-size:22px}}
        `;
        let last = null, selected = 'native', sourceDirty = false, settingsDirty = false;
        let working = false, polling = false, revision = 0, failed = false;
        const hero = el('section', 'ms-card');
        const top = el('div', 'ms-top'), title = el('div');
        title.append(el('h2', '', '陪我玩'), el('p', 'ms-muted', '你来打牌，大肥鱼在旁边陪你聊、帮你看。'));
        const badge = el('span', 'ms-tag', '正在读取状态'); top.append(title, badge);
        const choices = el('div', 'ms-sources'); choices.setAttribute('role', 'group'); choices.setAttribute('aria-label', '陪玩画面来源');
        function sourceButton(value, label, help) {
          const button = el('button', 'ms-source'); button.type = 'button';
          button.append(el('strong', '', label), el('span', 'ms-muted', help));
          button.addEventListener('click', () => { selected = value; sourceDirty = selected !== last?.companionSource; render(); }, { signal });
          choices.append(button); return button;
        }
        const native = sourceButton('native', '雀魂客户端', 'Steam 版 / 独立客户端 · 只看游戏窗口');
        const browser = sourceButton('browser', '雀魂网页版', '专用 Edge 窗口 · 读取公开牌局');
        const privacy = el('p', 'ms-note');
        const controls = el('div', 'ms-actions');
        const start = ui.button('开始陪我玩', { variant: 'primary', onClick: () => void change(selected === 'native' ? 'companion-native' : 'companion-browser') });
        start.classList.add('ms-primary');
        const stop = ui.button('结束陪玩', { onClick: () => void change('companion-off') });
        const refresh = ui.button('刷新状态', { onClick: () => void update() });
        controls.append(start, stop, el('span', 'ms-spacer'), refresh);
        const warning = el('div', 'ms-error'); warning.hidden = true; warning.setAttribute('role', 'status');
        const status = el('div', 'ms-status');
        const browserTools = el('div', 'ms-browser');
        const browserActions = el('div', 'ms-actions');
        const openGame = ui.button('打开专用网页', { onClick: () => void change('connect') });
        const attach = ui.button('连接已有网页', { onClick: () => void change('attach') });
        browserActions.append(openGame, attach);
        browserTools.append(browserActions, el('p', 'ms-note', '自己登录和开局。连接已有网页会保留当前页面。'));
        hero.append(top, choices, privacy, controls, status, browserTools);

        const grid = el('div', 'ms-grid');
        const talk = el('section', 'ms-card'); talk.append(el('h3', '', '她最近说'));
        const quote = el('div', 'ms-quote', '收到画面后，她会在桌宠气泡里给你建议。');
        const speechFoot = el('div', 'ms-foot'), speechTime = el('span', 'ms-muted'), voice = el('span', 'ms-muted');
        speechFoot.append(speechTime, voice);
        const voiceLink = el('a', 'ms-muted', '打开语音设置'); voiceLink.href = '#/provider/world%3Afatfish';
        talk.append(quote, speechFoot, el('p', 'ms-note', '这里显示桌宠最近的实际发言。建议以你眼前的牌局为准。'), voiceLink);

        const settings = el('section', 'ms-card'); settings.append(el('h3', '', '陪玩节奏'));
        const frequency = el('input', 'ms-number'); frequency.type = 'number'; frequency.min = '8'; frequency.max = '60'; frequency.step = '1'; frequency.value = '10'; frequency.required = true;
        const frequencyLabel = el('label', 'ms-form-row', '观察 / 建议间隔'); frequencyLabel.append(frequency, el('span', 'ms-muted', '秒'));
        frequency.setAttribute('aria-label', '观察建议间隔（秒）');
        const presets = el('div', 'ms-actions');
        const presetButtons = [[10, '多聊一点 · 10秒'], [20, '适中 · 20秒'], [40, '安静一点 · 40秒']].map(([seconds, label]) => {
          const b = ui.button(label, { size: 'sm', onClick: () => { frequency.value = String(seconds); settingsDirty = true; render(); } });
          b.classList.add('ms-preset'); presets.append(b); return b;
        });
        const save = ui.button('应用设置', { onClick: () => void saveSettings() });
        const saved = el('span', 'ms-muted'); saved.setAttribute('role', 'status');
        const saveRow = el('div', 'ms-actions'); saveRow.append(save, saved);
        settings.append(el('p', 'ms-muted', '调节看牌频率。相同画面不会重复发送，回复速度也取决于模型。'), frequencyLabel, presets, el('p', 'ms-note', '客户端最短 8 秒；退出陪玩后停止截图。'), saveRow);
        grid.append(talk, settings);

        const advanced = ui.foldSheet('companion-advanced', { title: '高级与诊断', note: '指定客户端窗口、查看网页牌局与详细状态', defaultOpen: false });
        const handle = el('input', 'ms-number ms-handle'); handle.type = 'text'; handle.placeholder = '留空，自动识别唯一的雀魂窗口'; handle.pattern = '[0-9a-fA-F]*'; handle.setAttribute('aria-label', '客户端窗口句柄');
        advanced.body.append(el('p', 'ms-muted', '多个雀魂窗口同时打开时，可以填写提示中的窗口句柄。修改后点击“应用设置”。'), handle);
        const details = el('pre', 'ms-debug'); advanced.body.append(details);
        for (const input of [frequency, handle]) input.addEventListener('input', () => { settingsDirty = true; saved.textContent = '有未保存的设置'; render(); }, { signal });
        ctx.guardLeave?.(() => settingsDirty ? '陪玩设置尚未保存，确定离开？' : null);
        panel.append(style, hero, warning, grid, advanced.el); root.append(panel);

        function metric(label, value) { const box = el('div'); box.append(el('span', 'ms-status-label', label), el('span', 'ms-value', value)); return box; }
        const time = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString() : '尚未收到';
        function render() {
          if (signal.aborted) return;
          const compatible = !!last?.companionSource && !!last?.companionUi;
          const active = !!last?.companionMode, isNative = last?.companionSource === 'native';
          const locked = working || !compatible || failed;
          native.setAttribute('aria-pressed', String(selected === 'native')); browser.setAttribute('aria-pressed', String(selected === 'browser'));
          native.disabled = browser.disabled = locked;
          privacy.textContent = selected === 'native'
            ? '只截取雀魂客户端窗口并发送给 DeepSeek。她只看牌和说话，不点击、不出牌；结束陪玩即停止截图。'
            : '读取专用网页里的本人手牌和公开信息。陪玩期间由你操作，她只聊天和给建议。';
          start.textContent = working ? '正在处理…' : active && !sourceDirty ? '正在陪你玩' : active ? '切换并陪我玩' : '开始陪我玩';
          start.disabled = locked || (active && !sourceDirty);
          stop.hidden = !active; stop.disabled = working;
          refresh.disabled = working || polling;
          for (const input of [frequency, handle, ...presetButtons]) input.disabled = locked;
          save.disabled = locked || !settingsDirty;
          frequency.min = selected === 'native' ? '8' : '5';
          browserTools.hidden = selected !== 'browser';
          openGame.disabled = attach.disabled = locked || isNative || !!last?.busy;
          if (!last) return;
          const n = last.nativeWatch || {};
          badge.textContent = failed ? '状态连接中断' : !compatible ? '请重启大肥鱼' : active ? `${isNative ? '客户端' : '网页版'} · 陪玩中` : '陪玩已停止';
          badge.dataset.on = String(active && !failed && !n.error);
          status.replaceChildren(
            metric(isNative ? '游戏窗口' : '游戏浏览器', isNative ? n.windowTitle || '等待识别雀魂窗口' : last.browserConnected ? '已连接专用 Edge' : '尚未连接'),
            metric('观察状态', !active ? '已停止' : isNative ? n.error ? '暂时看不到画面' : n.watching ? '正在看你的牌' : '正在获取窗口' : last.connected ? '已收到牌局' : '等待连接或开局'),
            metric(isNative ? '最近观察' : '牌局状态', isNative ? time(n.capturedAt) : last.observation?.reason || (last.observation?.active ? '进行中' : '等待开局')),
          );
          if (!failed) {
            const problem = !compatible ? '界面已更新，后台还需重启一次。请在局间重启大肥鱼后刷新。' : active ? isNative ? n.error : last.error : '';
            warning.textContent = problem || ''; warning.hidden = !problem;
          }
          const speech = last.companionUi?.latestSpeech;
          quote.textContent = speech?.text || (active ? '等她说第一句。收到画面后，会通过桌宠气泡和已启用的语音回应。' : '准备好后，点击“开始陪我玩”。');
          speechTime.textContent = speech ? time(speech.at) : '暂时没有发言';
          voice.textContent = last.companionUi?.voiceEnabled === true ? '桌宠朗读已开启' : last.companionUi?.voiceEnabled === false ? '桌宠朗读已关闭' : '语音状态暂不可用';
          details.textContent = JSON.stringify(last, null, 2);
        }
        function accept(state) {
          last = state; failed = false;
          if (!sourceDirty) selected = state.companionSource || 'native';
          if (!settingsDirty) {
            frequency.value = String(state.companionUi?.intervalSec ?? 10);
            handle.value = state.companionUi?.windowHandle || '';
          }
          render();
        }
        function report(error) {
          if (signal.aborted) return;
          failed = true; warning.hidden = false; warning.textContent = '未确认操作结果：' + String(error?.message || error) + '。请刷新状态后再试。'; render();
        }
        async function update() {
          if (working || polling || signal.aborted) return;
          const current = revision; polling = true; render();
          try { const state = await ctx.invoke('state'); if (!signal.aborted && current === revision) accept(state); }
          catch (error) { if (current === revision) report(error); }
          finally { polling = false; render(); }
        }
        async function change(method) {
          if (working || signal.aborted) return;
          working = true; revision++; render();
          try {
            const state = await ctx.invoke(method);
            if (signal.aborted) return;
            sourceDirty = false; accept(state); await ctx.refresh();
          } catch (error) { report(error); }
          finally { working = false; render(); }
        }
        async function saveSettings() {
          if (working || signal.aborted || !frequency.reportValidity() || !handle.reportValidity()) return;
          working = true; revision++; render(); saved.textContent = '正在保存…';
          try {
            await ctx.setConfig('world:majsoul', { 'worlds.majsoul.companionIntervalSec': Number(frequency.value), 'worlds.majsoul.nativeWindow': handle.value.trim() });
            if (signal.aborted) return;
            settingsDirty = false; accept(await ctx.invoke('state')); saved.textContent = '已保存，立即生效';
          } catch (error) { saved.textContent = '保存未确认'; report(error); }
          finally { working = false; render(); }
        }
        render(); void update(); ctx.interval(() => void update(), 2000);
      },
    },
  },
};
