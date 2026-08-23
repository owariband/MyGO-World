import assert from 'node:assert/strict';
import { once } from 'node:events';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { WebSocket } from 'ws';
import {
  compileTimeline,
  DynamicTimelineRuntime,
  DynamicTimelineValidationError,
  loadAndCompileTimeline,
  materializeRenderScript,
} from '../extensions/dynamic-render/core.mjs';
import { attachDynamicRender } from '../extensions/dynamic-render/server.mjs';

const devRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webgalRoot = process.env.WEBGAL_ROOT
  ? path.resolve(process.env.WEBGAL_ROOT)
  : path.resolve(devRoot, '../MyGO_v3.1.1_ForScript');
const timelinePath = path.join(devRoot, 'projects/rain-after/timeline.json');

test('the sample dynamic timeline compiles ordered render segments', async () => {
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  assert.equal(timeline.events.length, 3);
  assert.deepEqual(timeline.events[0].renders.map((render) => render.id), ['anon_soyo_001', 'anon_soyo_002']);
  assert.match(timeline.events[0].renders[0].script, /changeBg:演出、排练\/RiNG\/RiNG排练室\.png -next;/);
  assert.match(timeline.events[0].renders[0].script, /爱音:Soyorin/);
  assert.doesNotMatch(timeline.events[0].renders[0].script, /Dynamic render boundary/);
  assert.equal(timeline.events[0].renders[0].contentHash.length, 64);
});

test('materialized scripts expose start and completion markers', async () => {
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  const script = materializeRenderScript(timeline.events[0].renders[0], 17);
  assert.match(script, /^setVar:__dynamic_render_active=17 -next;/);
  assert.match(script, /setVar:__dynamic_render_done=17;/);
});

test('runtime advances progressively and returns to black when no render is ready', async () => {
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  const runtime = new DynamicTimelineRuntime(timeline);
  runtime.setRendererConnected(true);
  assert.equal(runtime.beginNext(1).render.id, 'anon_soyo_001');
  assert.equal(runtime.snapshot().playback.mode, 'loading');
  assert.equal(runtime.markPlaying(1), true);
  assert.equal(runtime.snapshot().playback.mode, 'playing');
  assert.equal(runtime.complete(1), true);
  assert.equal(runtime.beginNext(2).render.id, 'anon_soyo_002');
  runtime.complete(2);
  assert.equal(runtime.beginNext(3), null);
  assert.equal(runtime.snapshot().playback.mode, 'black');
});

test('an event with no render is valid and stays black', async () => {
  const timeline = await compileTimeline({
    formatVersion: 1,
    revision: 1,
    activeEventId: 'quiet-event',
    events: [{
      id: 'quiet-event',
      title: 'Quiet event',
      location: 'Black stage',
      characters: [{ id: 'anon', name: '爱音', figure: 'anon/casual-2023/model.json' }],
      renders: [],
    }],
  }, webgalRoot);
  const runtime = new DynamicTimelineRuntime(timeline);
  runtime.setRendererConnected(true);
  assert.equal(runtime.beginNext(1), null);
  assert.equal(runtime.snapshot().playback.mode, 'black');
  assert.equal(runtime.snapshot().events[0].readyCount, 0);
});

test('appending future renders does not restart the active render', async () => {
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  const runtime = new DynamicTimelineRuntime(timeline);
  runtime.setRendererConnected(true);
  runtime.beginNext(21);
  runtime.markPlaying(21);
  runtime.replaceTimeline({
    ...timeline,
    revision: timeline.revision + 1,
    events: timeline.events.map((event) => event.id === 'anon-soyo'
      ? { ...event, renders: [...event.renders, { ...event.renders[1], id: 'anon_soyo_003', contentHash: 'future' }] }
      : event),
  });
  assert.equal(runtime.active.dispatchId, 21);
  assert.equal(runtime.active.render.id, 'anon_soyo_001');
  assert.equal(runtime.snapshot().playback.mode, 'playing');
});

test('timeline replacement cannot rewrite an active render', async () => {
  const timeline = await loadAndCompileTimeline({ timelinePath, rootDir: webgalRoot });
  const runtime = new DynamicTimelineRuntime(timeline);
  runtime.setRendererConnected(true);
  runtime.beginNext(31);
  const changed = {
    ...timeline,
    revision: timeline.revision + 1,
    events: timeline.events.map((event) => event.id === 'anon-soyo'
      ? { ...event, renders: event.renders.map((render) => render.id === 'anon_soyo_001' ? { ...render, contentHash: 'changed' } : render) }
      : event),
  };
  assert.throws(() => runtime.replaceTimeline(changed), /cannot remove or rewrite active render/);
  assert.equal(runtime.active.render.id, 'anon_soyo_001');
});

test('dynamic renders reject branching and non-self-contained input', async () => {
  const invalid = {
    formatVersion: 1,
    revision: 1,
    activeEventId: 'bad-event',
    events: [{
      id: 'bad-event',
      title: 'Bad event',
      location: 'Nowhere',
      characters: [{ id: 'anon', name: '爱音', figure: 'anon/casual-2023/model.json' }],
      renders: [{ id: 'bad_render', title: 'Bad render', beats: [{ id: 'bad_choice', type: 'choice', options: [] }] }],
    }],
  };
  await assert.rejects(
    compileTimeline(invalid, webgalRoot),
    (error) => error instanceof DynamicTimelineValidationError
      && error.diagnostics.some((item) => item.includes('not supported by dynamic renders'))
      && error.diagnostics.some((item) => item.includes('must establish a background')),
  );
});

test('webgalsync advances from one render segment to the next', async (context) => {
  let controller = null;
  const server = http.createServer((request, response) => {
    if (controller?.handleHttp(request, response)) return;
    response.writeHead(404);
    response.end();
  });
  controller = await attachDynamicRender({
    server,
    devRoot,
    webgalRoot,
    projectId: 'rain-after',
    watchTimeline: false,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  const origin = 'http://127.0.0.1:' + address.port;
  const renderer = new WebSocket('ws://127.0.0.1:' + address.port + '/api/webgalsync', {
    headers: { Origin: origin },
  });
  context.after(async () => {
    renderer.close();
    controller.close();
    await new Promise((resolve) => server.close(resolve));
  });
  await once(renderer, 'open');

  renderer.send(JSON.stringify(syncMessage({})));
  const first = await nextJsonMessage(renderer);
  assert.equal(first.data.command, 6);
  assert.match(first.data.message, /爱音:Soyorin/);
  const dispatchId = Number(/__dynamic_render_active=(\d+)/.exec(first.data.message)?.[1]);
  assert.ok(dispatchId > 0);

  renderer.send(JSON.stringify(syncMessage({ __dynamic_render_active: dispatchId })));
  renderer.send(JSON.stringify(syncMessage({
    __dynamic_render_active: dispatchId,
    __dynamic_render_done: dispatchId,
  })));
  const second = await nextJsonMessage(renderer);
  assert.equal(second.data.command, 6);
  assert.match(second.data.message, /排练室的灯没有关/);
  assert.notEqual(second.data.message, first.data.message);
});

function syncMessage(gameVars) {
  return {
    event: 'message',
    data: {
      command: 1,
      sceneMsg: { scene: 'temp', sentence: 0 },
      stageSyncMsg: { GameVar: gameVars },
      message: 'sync',
    },
  };
}

function nextJsonMessage(socket) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Timed out waiting for WebSocket message')), 2000);
    socket.once('message', (data) => {
      clearTimeout(timeout);
      resolve(JSON.parse(data.toString()));
    });
  });
}
