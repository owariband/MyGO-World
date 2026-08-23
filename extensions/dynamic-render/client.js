const ROOT_ID = 'dynamic-render-host';

export async function startDynamicRenderHost() {
  const initial = await loadInitialState();
  if (!initial) return null;
  const view = mountHost();
  let snapshot = initial;
  let socket = null;
  let reconnectTimer = null;

  const render = () => renderSnapshot(view, snapshot, (eventId) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'timeline.select-event', eventId }));
  });
  const connect = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(protocol + '//' + window.location.host + '/api/dynamic-render');
    socket.addEventListener('message', (event) => {
      const message = parseJson(event.data);
      if (message?.type === 'timeline.snapshot') {
        snapshot = message;
        render();
      } else if (message?.type === 'timeline.error') {
        view.status.textContent = message.message || 'Timeline update failed';
      }
    });
    socket.addEventListener('close', () => {
      snapshot = { ...snapshot, playback: { ...snapshot.playback, mode: 'black', rendererConnected: false } };
      render();
      reconnectTimer = window.setTimeout(connect, 1000);
    });
  };

  render();
  connect();
  return {
    destroy() {
      window.clearTimeout(reconnectTimer);
      socket?.close();
      view.root.remove();
    },
  };
}

async function loadInitialState() {
  try {
    const response = await fetch('/api/dynamic-render/state', { cache: 'no-store' });
    return response.ok ? response.json() : null;
  } catch {
    return null;
  }
}

function mountHost() {
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = '/extensions/dynamic-render/client.css';
  document.head.appendChild(style);

  const root = document.createElement('section');
  root.id = ROOT_ID;
  root.dataset.mode = 'black';
  root.setAttribute('aria-label', 'Dynamic story timeline');
  const blackout = document.createElement('div');
  blackout.className = 'dynamic-blackout';
  blackout.tabIndex = 0;
  blackout.setAttribute('role', 'button');
  blackout.setAttribute('aria-label', '启动动态世界');
  blackout.addEventListener('click', wakeOriginalPlayer);
  blackout.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') wakeOriginalPlayer();
  });
  const panel = document.createElement('div');
  panel.className = 'dynamic-timeline';
  const header = document.createElement('div');
  header.className = 'dynamic-header';
  const kicker = document.createElement('span');
  kicker.className = 'dynamic-kicker';
    kicker.textContent = '世界线 / Timeline';
  const status = document.createElement('span');
  status.className = 'dynamic-status';
  status.setAttribute('aria-live', 'polite');
  const events = document.createElement('div');
  events.className = 'dynamic-events';
  header.append(kicker, status);
  panel.append(header, events);
  root.append(blackout, panel);
  document.body.appendChild(root);
  return { root, status, events };
}

function renderSnapshot(view, snapshot, selectEvent) {
  view.root.dataset.mode = snapshot.playback?.mode === 'playing' ? 'playing' : 'black';
  view.status.textContent = statusText(snapshot.playback);
  view.events.replaceChildren(...(snapshot.events ?? []).map((event) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dynamic-event';
    button.setAttribute('aria-pressed', String(event.selected));
    button.addEventListener('click', () => {
      wakeOriginalPlayer();
      selectEvent(event.id);
    });
    const label = document.createElement('span');
    const name = document.createElement('span');
    name.className = 'dynamic-event-name';
    name.textContent = event.title;
    const location = document.createElement('span');
    location.className = 'dynamic-location';
    location.textContent = event.location + ' · ' + event.readyCount + ' ready';
    label.append(name, location);
    const segments = document.createElement('span');
    segments.className = 'dynamic-segments';
    for (const render of event.renders) {
      const segment = document.createElement('span');
      segment.className = 'dynamic-segment';
      segment.dataset.status = render.status;
      segment.style.flexGrow = String(Math.max(1, render.estimatedPlayMs));
      segment.title = render.title + ' · ' + render.status;
      segments.appendChild(segment);
    }
    button.append(label, segments);
    return button;
  }));
}

function statusText(playback = {}) {
  if (!playback.rendererConnected) return '渲染器离线 · 舞台保持黑屏';
  if (playback.mode === 'loading') return '正在加载下一段';
  if (playback.mode === 'playing') return '正在播放 · ' + playback.renderId;
  return '暂无可播放内容 · 舞台保持黑屏';
}

function parseJson(value) {
  try { return JSON.parse(value); } catch { return null; }
}

function wakeOriginalPlayer() {
  const entrance = document.querySelector('.html-body__title-enter');
  if (entrance && window.getComputedStyle(entrance).display !== 'none') entrance.click();
}
