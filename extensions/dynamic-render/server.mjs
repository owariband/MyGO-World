import { watch } from 'node:fs';
import path from 'node:path';
import { WebSocket, WebSocketServer } from 'ws';
import { DynamicTimelineRuntime, loadAndCompileTimeline, materializeRenderScript } from './core.mjs';

const TEMP_SCENE = 6;
const SYNCFC = 1;
const MAX_PAYLOAD = 1024 * 1024;

export async function attachDynamicRender({ server, devRoot, webgalRoot, projectId, watchTimeline = true }) {
  const timelinePath = path.join(devRoot, 'projects', projectId, 'timeline.json');
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  const controller = new DynamicRenderController({ server, webgalRoot, timelinePath, timeline, watchTimeline });
  controller.start();
  return controller;
}

class DynamicRenderController {
  constructor({ server, webgalRoot, timelinePath, timeline, watchTimeline }) {
    this.server = server;
    this.webgalRoot = webgalRoot;
    this.timelinePath = timelinePath;
    this.watchTimeline = watchTimeline;
    this.runtime = new DynamicTimelineRuntime(timeline);
    this.rendererServer = new WebSocketServer({ noServer: true, maxPayload: MAX_PAYLOAD });
    this.hostServer = new WebSocketServer({ noServer: true, maxPayload: MAX_PAYLOAD });
    this.rendererSocket = null;
    this.rendererReady = false;
    this.hostSockets = new Set();
    this.dispatchSequence = 0;
    this.onUpgrade = this.handleUpgrade.bind(this);
  }

  start() {
    this.server.on('upgrade', this.onUpgrade);
    this.rendererServer.on('connection', (socket) => this.handleRendererConnection(socket));
    this.hostServer.on('connection', (socket) => this.handleHostConnection(socket));
    if (this.watchTimeline) {
      const timelineName = path.basename(this.timelinePath);
      this.timelineWatcher = watch(path.dirname(this.timelinePath), (_eventType, filename) => {
        if (!filename || filename.toString() === timelineName) this.scheduleTimelineReload();
      });
    }
  }

  handleHttp(request, response) {
    const url = new URL(request.url ?? '/', 'http://localhost');
    if (url.pathname !== '/api/dynamic-render/state') return false;
    const body = JSON.stringify(this.runtime.snapshot());
    response.writeHead(200, {
      'Cache-Control': 'no-store',
      'Content-Length': Buffer.byteLength(body),
      'Content-Type': 'application/json; charset=utf-8',
    });
    response.end(body);
    return true;
  }

  close() {
    clearTimeout(this.reloadTimer);
    this.timelineWatcher?.close();
    this.server.off('upgrade', this.onUpgrade);
    for (const socket of this.hostSockets) socket.close();
    this.rendererSocket?.close();
    this.rendererServer.close();
    this.hostServer.close();
  }

  handleUpgrade(request, socket, head) {
    const url = new URL(request.url ?? '/', 'http://localhost');
    const target = url.pathname === '/api/webgalsync'
      ? this.rendererServer
      : url.pathname === '/api/dynamic-render'
        ? this.hostServer
        : null;
    if (!target) {
      socket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }
    if (!sameOrigin(request)) {
      socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      socket.destroy();
      return;
    }
    target.handleUpgrade(request, socket, head, (webSocket) => target.emit('connection', webSocket, request));
  }

  handleRendererConnection(socket) {
    if (this.rendererSocket?.readyState === WebSocket.OPEN) this.rendererSocket.close(1012, 'renderer replaced');
    this.rendererSocket = socket;
    this.rendererReady = false;
    this.runtime.setRendererConnected(true);
    this.runtime.cancelActive();
    this.broadcastSnapshot();
    socket.on('message', (data) => this.handleRendererMessage(data));
    socket.on('close', () => {
      if (this.rendererSocket !== socket) return;
      this.rendererSocket = null;
      this.rendererReady = false;
      this.runtime.setRendererConnected(false);
      this.broadcastSnapshot();
    });
  }

  handleRendererMessage(data) {
    const message = parseJson(data);
    if (message?.data?.command !== SYNCFC) return;
    this.rendererReady = true;
    const gameVars = message.data.stageSyncMsg?.GameVar ?? {};
    const activeId = Number(gameVars.__dynamic_render_active);
    const doneId = Number(gameVars.__dynamic_render_done);
    const active = this.runtime.active;
    if (active && activeId === active.dispatchId) this.runtime.markPlaying(active.dispatchId);
    if (active && doneId === active.dispatchId && this.runtime.complete(active.dispatchId)) {
      this.broadcastSnapshot();
      this.sendNextRender();
      return;
    }
    this.broadcastSnapshot();
    this.sendNextRender();
  }

  handleHostConnection(socket) {
    this.hostSockets.add(socket);
    sendJson(socket, this.runtime.snapshot());
    socket.on('message', (data) => {
      const message = parseJson(data);
      if (message?.type !== 'timeline.select-event') return;
      try {
        this.runtime.selectEvent(message.eventId);
        this.broadcastSnapshot();
        this.sendNextRender();
      } catch (error) {
        sendJson(socket, { type: 'timeline.error', message: error.message });
      }
    });
    socket.on('close', () => this.hostSockets.delete(socket));
  }

  sendNextRender() {
    if (!this.rendererReady || this.rendererSocket?.readyState !== WebSocket.OPEN) return;
    const active = this.runtime.beginNext(++this.dispatchSequence);
    if (!active) {
      this.broadcastSnapshot();
      return;
    }
    sendJson(this.rendererSocket, {
      event: 'message',
      data: { command: TEMP_SCENE, message: materializeRenderScript(active.render, active.dispatchId) },
    });
    this.broadcastSnapshot();
  }

  scheduleTimelineReload() {
    clearTimeout(this.reloadTimer);
    this.reloadTimer = setTimeout(() => this.reloadTimeline(), 120);
  }

  async reloadTimeline() {
    try {
      const timeline = await loadAndCompileTimeline({ timelinePath: this.timelinePath, rootDir: this.webgalRoot });
      this.runtime.replaceTimeline(timeline);
      this.broadcastSnapshot();
      this.sendNextRender();
    } catch (error) {
      this.broadcast({ type: 'timeline.error', message: error.message, diagnostics: error.diagnostics ?? [] });
    }
  }

  broadcastSnapshot() { this.broadcast(this.runtime.snapshot()); }

  broadcast(message) {
    for (const socket of this.hostSockets) sendJson(socket, message);
  }
}

function sameOrigin(request) {
  const origin = request.headers.origin;
  if (!origin) return true;
  try { return new URL(origin).host === request.headers.host; } catch { return false; }
}

function parseJson(data) {
  try { return JSON.parse(data.toString()); } catch { return null; }
}

function sendJson(socket, message) {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}
