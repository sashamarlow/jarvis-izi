// Only the rendered Telegram Web K DOM is read. No Telegram runtime/session APIs.
(() => {
  window.__concertWeb?.stop?.();
  const STABLE_MS = 40;
  const visible = e => !!e && e.isConnected && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const inputs = () => [...document.querySelectorAll('.chat-input-main [contenteditable="true"][data-peer-id]')].filter(visible);
  const current = () => {
    const found = inputs();
    if(found.length !== 1) throw Error('Не найдено единственное поле сообщения. Открой обычную беседу в Telegram Web K.');
    const input = found[0], root = input.closest('.chat');
    const peer = Number(input.dataset.peerId);
    if(!root || !Number.isSafeInteger(peer) || peer >= 0) throw Error('Нужна группа с доступным полем сообщения, не личный чат.');
    return {input, root, peer};
  };
  const text = element => {
    if(!element) return '';
    const copy = element.cloneNode(true);
    copy.querySelectorAll('.time, .clearfix, .reply, .reply-wrapper').forEach(e => e.remove());
    const walk = n => {
      if(n.nodeType === Node.TEXT_NODE) return n.textContent;
      if(n.nodeName === 'BR') return '\n';
      const body = [...n.childNodes].map(walk).join('');
      return ['DIV', 'P', 'LI'].includes(n.nodeName) ? body + '\n' : body;
    };
    return walk(copy).trim();
  };
  const bubbles = root => [...root.querySelectorAll('.bubbles-inner .bubble[data-mid][data-peer-id][data-timestamp]')]
    .filter(e => !e.closest('.bubbles-remover'));
  // Group avatars represent the sender. Forward/reply titles can represent someone else.
  // See Telegram Web K bubbleGroups.ts#getAvatarOptions and bubbles.ts#peerIdForColor.
  const senderLabels = b => {
    const group = b.closest('.bubbles-group');
    return [...(group || b).querySelectorAll('.name.colored-name[data-peer-id]')].filter(e => {
      const owner = e.closest('.bubble');
      return owner && (group ? owner.closest('.bubbles-group') === group : owner === b) &&
        owner.dataset.peerId === b.dataset.peerId &&
        !e.closest('.reply, .reply-wrapper, .message, .is-forward, .forwarded, .is-via');
    });
  };
  const sender = b => {
    const candidates = [];
    const group = b.closest('.bubbles-group');
    if(group) {
      const avatars = [...group.querySelectorAll('.bubbles-group-avatar[data-peer-id]')]
        .filter(e => e.closest('.bubbles-group') === group && !e.closest('.bubble'));
      if(avatars.length > 1) return null;
      candidates.push(...avatars.map(e => e.dataset.peerId));
    }
    // Only the actual colored sender label, never arbitrary .peer-title descendants.
    const labels = senderLabels(b);
    candidates.push(...labels.map(e => e.dataset.peerId));
    if(!candidates.length || candidates.some(v => !/^[1-9][0-9]*$/.test(v))) return null;
    const ids = [...new Set(candidates.map(Number))];
    return ids.length === 1 && Number.isSafeInteger(ids[0]) && ids[0] > 0 ? ids[0] : null;
  };
  const guard = (expected, draft) => {
    const c = current();
    if(c.peer !== expected.peer || c.root !== expected.root || c.input !== expected.input)
      throw Error('Беседа/поле сообщения изменились. Остановлено.');
    if(c.root.classList.contains('is-helper-active')) throw Error('Включён ответ, пересылка или редактирование. Сначала отмените их.');
    const avatar = c.root.querySelector('.new-message-send-as-container .new-message-send-as-avatar[data-peer-id]');
    if(avatar && Number(avatar.dataset.peerId) <= 0) throw Error('Выбрана отправка от имени канала/группы. Выберите личный аккаунт.');
    if(text(c.input) !== draft) throw Error('Поле сообщения занято/изменено. Черновик не трогаю.');
    if([...document.querySelectorAll('.popup')].some(visible)) throw Error('Открыто всплывающее окно Telegram.');
    return c;
  };
  let armed;
  let selection;
  const oneBubble = (root, peer, mid) => [...root.querySelectorAll(`.bubbles-inner .bubble[data-mid="${mid}"][data-peer-id="${peer}"]`)]
    .filter(b => !b.closest('.bubbles-remover'));
  const source = (mid, authorId) => {
    if(!Number.isSafeInteger(mid) || !Number.isSafeInteger(authorId) || authorId !== armed.authorId)
      throw Error('Не задан подтверждённый автор объявления. Плюс не отправлен.');
    const observed = armed.observed.get(mid);
    const found = oneBubble(armed.root, armed.peer, mid);
    if(!observed || observed.sender_id !== authorId || found.length !== 1)
      throw Error('Объявление или его автор не подтверждены. Плюс не отправлен.');
    const b = found[0];
    if(b.classList.contains('is-out') || b.classList.contains('is-outgoing') || sender(b) !== authorId ||
        Number(b.dataset.timestamp) !== observed.date || text(b.querySelector('.message')) !== observed.text)
      throw Error('Объявление/автор изменились до отправки. Плюс не отправлен.');
    return observed;
  };
  const flush = () => {
    if(!armed?.waiter) return;
    const {resolve, reject, timer} = armed.waiter;
    if(!armed.error && !armed.queue.length) return;
    clearTimeout(timer); armed.waiter = null;
    if(armed.error) reject(Error(armed.error));
    else {
      try { guard(armed, ''); resolve(armed.queue.splice(0)); }
      catch(e) { reject(e); }
    }
  };
  const fail = message => {
    if(!armed || armed.error) return;
    armed.error = message;
    armed.observer?.disconnect();
    for(const value of armed.pending.values()) clearTimeout(value.timer);
    armed.pending.clear(); armed.queue.length = 0;
    flush();
  };
  const alive = () => {
    if(!armed) throw Error('Наблюдение не запущено.');
    if(Date.now() - armed.lastWake > 60000) fail('Длительная пауза/сон компьютера. Перезапусти программу.');
    if(armed.error) throw Error(armed.error);
    if(!armed.list.isConnected || armed.root.querySelector('.bubbles-inner') !== armed.list)
      throw Error('Список сообщений заменён. Перезапусти наблюдение.');
  };
  const emit = event => {
    // Bound the queue if Python is busy waiting for a previous send acknowledgment.
    if(armed.queue.length >= 500) { fail('Слишком много событий. Перезапусти наблюдение.'); return; }
    armed.queue.push(event); flush();
  };
  const inspectBubble = b => {
    alive();
    if(!b.isConnected || !armed.list.contains(b) || b.closest('.bubbles-remover')) return;
    const mid = Number(b.dataset.mid), date = Number(b.dataset.timestamp);
    if(Number(b.dataset.peerId) !== armed.peer || !Number.isSafeInteger(mid) || mid <= armed.baseline || armed.seen.has(mid)) return;
    if(b.classList.contains('is-out') || b.classList.contains('is-outgoing') || (Number.isFinite(date) && date < armed.since)) {
      armed.seen.add(mid); return;
    }
    if(!Number.isFinite(date) || !b.hasAttribute('data-timestamp')) return;
    const sender_id = sender(b); // Reject other senders before cloning/parsing text.
    const body = sender_id === armed.authorId ? text(b.querySelector('.message')) : '';
    const now = performance.now(), previous = armed.pending.get(mid);
    if(previous) clearTimeout(previous.timer);
    const changed = !previous || previous.text !== body || previous.sender_id !== sender_id || previous.date !== date;
    const state = {b, text: body, sender_id, date, firstSeen: previous?.firstSeen ?? now,
      stableSince: changed ? now : previous.stableSince, warned: previous?.warned ?? false};
    armed.pending.set(mid, state);
    if(sender_id === armed.authorId && !body) return;
    if(!changed && now - state.stableSince >= STABLE_MS) {
      if(sender_id === null) {
        if(!state.warned) {
          state.warned = true;
          emit({mid, date, text: '', sender_id: null});
        }
        // Missing metadata can arrive later. Keep listening; never infer a sender.
        return;
      }
      armed.pending.delete(mid); armed.seen.add(mid);
      const event = {mid, date, text: body, sender_id,
        detected_at: state.firstSeen, ready_at: now, detection_ms: now - state.firstSeen};
      if(sender_id === armed.authorId) armed.observed.set(mid, event);
      emit(event);
      return;
    }
    state.timer = setTimeout(() => {
      try { inspectBubble(b); } catch(e) { fail(e.message); }
    }, Math.max(1, STABLE_MS - (now - state.stableSince)));
  };
  const onMutations = records => {
    try {
      alive();
      const changed = new Set();
      const collect = node => {
        const e = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
        if(!e || !armed.list.contains(e)) return;
        const own = e.closest('.bubble');
        if(own) changed.add(own);
        e.querySelectorAll?.('.bubble').forEach(b => changed.add(b));
        // A shared group avatar/name can arrive after several bubbles are rendered.
        if(e.closest('.name.colored-name, .bubbles-group-avatar') ||
           e.querySelector?.('.name.colored-name, .bubbles-group-avatar')) {
          e.closest('.bubbles-group')?.querySelectorAll('.bubble').forEach(b => changed.add(b));
        }
      };
      for(const record of records) {
        // Appending a bubble must not scan every sibling in the message list.
        if(record.type === 'childList') {
          if(record.target !== armed.list) {
            const own = record.target.closest?.('.bubble');
            if(own) changed.add(own);
            if(record.removedNodes.length) collect(record.target);
          }
          record.addedNodes.forEach(collect);
        } else collect(record.target);
      }
      for(const b of changed) inspectBubble(b);
    } catch(e) { fail(e.message); }
  };
  const waitForChange = (check, timeout) => new Promise((resolve, reject) => {
    let timer;
    const observer = new MutationObserver(attempt);
    function finish(error, value) {
      observer.disconnect(); clearTimeout(timer);
      if(error) reject(error); else resolve(value);
    }
    function attempt() {
      try { const result = check(); if(result !== undefined) finish(null, result); }
      catch(e) { finish(e); }
    }
    observer.observe(document.body, {childList: true, subtree: true, attributes: true, characterData: true});
    timer = setTimeout(() => finish(Error('Истекло время ожидания интерфейса Telegram. Автоповтора нет.')), timeout);
    attempt();
  });
  window.__concertWeb = {
    inspect() {
      const c = current();
      guard(c, '');
      const title = c.root.querySelector('.topbar .user-title')?.textContent?.trim();
      if(!title) throw Error('Не найден заголовок беседы. Дождитесь её загрузки.');
      return {peer: c.peer, title};
    },
    authorMessages(peer) {
      const c = current(); guard(c, '');
      if(c.peer !== peer) throw Error('Беседа изменилась. Вернись в выбранную группу.');
      const choices = [];
      for(const b of bubbles(c.root).reverse()) {
        if(Number(b.dataset.peerId) !== peer || b.classList.contains('is-out') || b.classList.contains('is-outgoing')) continue;
        const mid = Number(b.dataset.mid), authorId = sender(b), body = text(b.querySelector('.message'));
        if(!Number.isSafeInteger(mid) || authorId === null || !body || oneBubble(c.root, peer, mid).length !== 1) continue;
        const name = senderLabels(b).find(e => Number(e.dataset.peerId) === authorId)?.textContent?.trim() || 'Автор без подписи';
        choices.push({mid, author_id: authorId, name, preview: body.replace(/\s+/g, ' ').slice(0, 160)});
        if(choices.length === 15) break;
      }
      selection = {...c, choices};
      return choices;
    },
    selectAuthor(peer, mid, authorId) {
      if(!selection || selection.peer !== peer) throw Error('Обнови список сообщений.');
      guard(selection, '');
      const choice = selection.choices.find(e => e.mid === mid && e.author_id === authorId);
      const found = oneBubble(selection.root, peer, mid);
      if(!choice || found.length !== 1 || sender(found[0]) !== authorId ||
         text(found[0].querySelector('.message')).replace(/\s+/g, ' ').slice(0, 160) !== choice.preview)
        throw Error('Сообщение или автор изменились. Обнови список и выбери снова.');
      return {author_id: authorId, name: choice.name};
    },
    stop() {
      if(!armed) return;
      fail('Наблюдение остановлено.');
      clearInterval(armed.watchdog);
    },
    arm(peer, authorId) {
      this.stop();
      const c = current();
      if(c.peer !== peer) throw Error('Беседа изменилась до начала проверки.');
      guard(c, '');
      if(!Number.isSafeInteger(authorId) || authorId <= 0) throw Error('Сначала укажи Telegram ID автора.');
      if(!c.root.querySelector('.bubbles.scrolled-down'))
        throw Error('Перейдите к последним сообщениям беседы перед запуском.');
      const mids = bubbles(c.root).map(b => Number(b.dataset.mid)).filter(Number.isSafeInteger);
      if(!mids.length) throw Error('Сообщения ещё не загружены. Откройте конец беседы и повторите запуск.');
      armed = {...c, baseline: Math.max(...mids), since: Math.floor(Date.now()/1000),
        authorId, seen: new Set(mids), pending: new Map(), observed: new Map(),
        list: c.root.querySelector('.bubbles-inner'), queue: [], waiter: null, lastWake: Date.now()};
      armed.observer = new MutationObserver(onMutations);
      armed.observer.observe(armed.list, {childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ['class', 'data-mid', 'data-peer-id', 'data-timestamp']});
      // A health check, not message polling: no list scan when the chat is idle.
      armed.watchdog = setInterval(() => {
        try {
          alive(); armed.lastWake = Date.now();
          if(armed.waiter) guard(armed, '');
          for(const [mid, event] of armed.observed) if(Date.now()/1000 - event.date > 60) armed.observed.delete(mid);
          for(const [mid, state] of armed.pending) if(!state.b.isConnected || Date.now()/1000 - state.date > 60) {
            clearTimeout(state.timer); armed.pending.delete(mid);
          }
        } catch(e) { fail(e.message); }
      }, 1000);
      return {baseline: armed.baseline, since: armed.since};
    },
    poll() {
      // Diagnostic queue drain used by offline tests; production awaits nextEvents().
      alive();
      guard(armed, '');
      return armed.queue.splice(0);
    },
    nextEvents(timeout = 1000) {
      alive(); guard(armed, '');
      if(armed.waiter) throw Error('Уже запущено ожидание сообщений.');
      if(armed.queue.length) return Promise.resolve(armed.queue.splice(0));
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { armed.waiter = null; resolve([]); }, timeout);
        armed.waiter = {resolve, reject, timer};
      });
    },
    preflight(mid, authorId) { alive(); guard(armed, ''); source(mid, authorId); return armed.peer; },
    async sendPrepared(date, mid, authorId) {
      const observed = source(mid, authorId);
      const before = await waitForChange(() => {
        alive(); const c = guard(armed, '+'); source(mid, authorId);
        const button = c.root.querySelector('.chat-input-main .btn-send.send');
        if(!button) return undefined;
        return this.commit(date, 60, mid, authorId);
      }, 3000);
      const clicked = performance.now();
      await waitForChange(() => {
        alive();
        if(this.acknowledged(before)) return true;
        return undefined;
      }, 10000);
      return {detection_ms: observed.detection_ms,
        ready_to_click_ms: clicked - observed.ready_at,
        first_seen_to_click_ms: clicked - observed.detected_at,
        click_to_ack_ms: performance.now() - clicked};
    },
    commit(date = null, maxAge = 60, mid = null, authorId = null) {
      // Guard and click happen in one JS task, so a chat switch cannot interleave.
      alive();
      const c = guard(armed, '+');
      const observed = source(mid, authorId);
      if(date !== null && date !== observed.date) throw Error('Дата объявления изменилась. Плюс не отправлен.');
      if(Date.now()/1000 - observed.date > maxAge)
        throw Error('Объявление устарело до отправки. Плюс не отправлен.');
      const buttons = [...c.root.querySelectorAll('.chat-input-main .btn-send.send')].filter(visible);
      if(buttons.length !== 1 || buttons[0].disabled || buttons[0].classList.contains('btn-disabled') ||
        buttons[0].getAttribute('aria-disabled') === 'true')
        throw Error('Обычная отправка недоступна. Плюс не отправлен.');
      const before = bubbles(c.root).map(b => b.dataset.mid);
      armed.observed.delete(mid); // Consume before click; even a thrown click is not retried.
      buttons[0].click();
      return before;
    },
    acknowledged(before) {
      const c = current();
      if(c.peer !== armed.peer || c.root !== armed.root) throw Error('Беседа изменилась после клика.');
      return bubbles(c.root).some(b => Number(b.dataset.peerId) === armed.peer &&
        !before.includes(b.dataset.mid) && Number.isSafeInteger(Number(b.dataset.mid)) &&
        b.classList.contains('is-out') && !b.classList.contains('is-outgoing') &&
        !b.classList.contains('is-error') && text(b.querySelector('.message')) === '+');
    }
  };
})();
