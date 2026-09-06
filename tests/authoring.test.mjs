import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { compileStory, loadStory, validateStory } from '../tools/mygo-author-lib.mjs';
import {
  compileProject,
  renderGameConfig,
  renderPlayerConfig,
  renderPreviewStart,
  resolvePreviewScene,
  validateProject,
} from '../tools/mygo-project.mjs';

const devRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webgalRoot = process.env.WEBGAL_ROOT
  ? path.resolve(process.env.WEBGAL_ROOT)
  : path.resolve(devRoot, '../MyGO_v3.1.1_ForScript');
const storyPath = path.join(devRoot, 'projects/rain-after/story.json');

test('the bundled story validates and compiles into a playable branch', async () => {
  const story = await loadStory(storyPath);
  const diagnostics = await validateStory(story, webgalRoot);
  assert.deepEqual(diagnostics, []);

  const script = compileStory(story);
  assert.match(script, /jumpLabel:rainy_entrance;/);
  assert.match(
    script,
    /choose:承认自己害怕失去这支乐队:honest_rooftop\|继续只谈演奏的问题:technical_distance;/,
  );
  assert.match(script, /-motion=tomori\/idle01/);
  assert.match(script, /intro:GOOD END · 同一个节拍/);
  assert.match(script, /intro:NORMAL END · 正确的距离/);
  assert.doesNotMatch(script, /changeFigure:none-left/);
  assert.doesNotMatch(script, /changeFigure:none-right/);
});

test('validation rejects missing assets and impossible branches', async () => {
  const story = JSON.parse(await readFile(storyPath, 'utf8'));
  story.characters[0].figure = 'anon/not-found/model.json';
  story.scenes[2].beats = story.scenes[2].beats.filter((beat) => beat.type !== 'ending');

  const diagnostics = await validateStory(story, webgalRoot);
  assert.ok(diagnostics.some((diagnostic) => diagnostic.includes('missing asset')));
  assert.ok(diagnostics.some((diagnostic) => diagnostic.includes('cannot reach an ending')));
});

test('validation checks Live2D motion and expression names against the model', async () => {
  const story = JSON.parse(await readFile(storyPath, 'utf8'));
  const showBeat = story.scenes[0].beats.find((beat) => beat.type === 'show');
  showBeat.motion = 'anon/not-a-motion';
  showBeat.expression = 'anon/not-an-expression';

  const diagnostics = await validateStory(story, webgalRoot);
  assert.ok(diagnostics.some((diagnostic) => diagnostic.includes('.motion "anon/not-a-motion"')));
  assert.ok(diagnostics.some((diagnostic) => diagnostic.includes('.expression "anon/not-an-expression"')));
});

test('projects own isolated game configuration and save namespaces', async () => {
  const rainProject = await validateProject(devRoot, webgalRoot, 'rain-after');
  const secondProject = await validateProject(devRoot, webgalRoot, 'second-story');
  assert.deepEqual(rainProject.diagnostics, []);
  assert.deepEqual(secondProject.diagnostics, []);

  assert.notEqual(rainProject.manifest.gameKey, secondProject.manifest.gameKey);
  assert.equal(renderPlayerConfig(rainProject.manifest).storageKey, 'generative-mygo.rain-after');
  assert.equal(renderPlayerConfig(secondProject.manifest).storageKey, 'generative-mygo.second-story');
  assert.match(renderGameConfig(rainProject.manifest), /Game_name:雨声之后;/);
  assert.match(renderGameConfig(secondProject.manifest), /Game_name:第二个故事;/);
});

test('generated renders can be previewed without replacing WebGAL start.txt', async (context) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mygo-preview-'));
  context.after(() => rm(root, { recursive: true, force: true }));
  const generated = path.join(root, 'game', 'scene', 'generated', 'world-one');
  await mkdir(generated, { recursive: true });
  await writeFile(path.join(generated, 'render-one.txt'), ':preview;\n', 'utf8');

  const scene = await resolvePreviewScene(root, 'generated/world-one/render-one.txt');
  assert.equal(scene, 'generated/world-one/render-one.txt');
  assert.equal(renderPreviewStart(scene), 'changeScene:generated/world-one/render-one.txt;\n');
  await assert.rejects(resolvePreviewScene(root, '../config.txt'), /under game\/scene\/generated/);
});

test('project builds are separate while using the same shared asset library', async () => {
  const rainBuild = await compileProject({ devRoot, webgalRoot, projectId: 'rain-after' });
  const secondBuild = await compileProject({ devRoot, webgalRoot, projectId: 'second-story' });

  const [rainScript, secondScript, rainConfig, secondConfig] = await Promise.all([
    readFile(rainBuild.paths.scriptOutputPath, 'utf8'),
    readFile(secondBuild.paths.scriptOutputPath, 'utf8'),
    readFile(rainBuild.paths.configOutputPath, 'utf8'),
    readFile(secondBuild.paths.configOutputPath, 'utf8'),
  ]);
  assert.match(rainScript, /GOOD END · 同一个节拍/);
  assert.doesNotMatch(rainScript, /第二部作品/);
  assert.match(secondScript, /第二部作品/);
  assert.doesNotMatch(secondScript, /GOOD END · 同一个节拍/);
  assert.match(rainConfig, /Game_key:generative-mygo\.rain-after;/);
  assert.match(secondConfig, /Game_key:generative-mygo\.second-story;/);
});

test('the long-form adaptation keeps its title and four chapters in source order', async () => {
  const project = await validateProject(devRoot, webgalRoot, 'for-the-band');
  assert.deepEqual(project.diagnostics, []);
  assert.equal(project.manifest.name, '【爱素】为了乐队就让让直女吧～长崎大小姐防线崩溃中～');
  assert.equal(project.manifest.gameKey, 'generative-mygo.for-the-band');
  assert.equal(project.manifest.textSpeed, 58);

  const chapterTitles = project.story.scenes
    .flatMap((scene) => scene.beats)
    .filter((beat) => beat.type === 'chapter')
    .map((beat) => beat.title);
  assert.deepEqual(chapterTitles, ['第一章', '第二章', '第三章', '第四章']);
  const beats = project.story.scenes.flatMap((scene) => scene.beats);
  assert.equal(new Set(beats.map((beat) => beat.id)).size, beats.length);

  const build = await compileProject({ devRoot, webgalRoot, projectId: 'for-the-band' });
  const script = await readFile(build.paths.scriptOutputPath, 'utf8');
  const chapterOffsets = chapterTitles.map((title) => script.indexOf(`intro:${title}|`));
  assert.ok(chapterOffsets.every((offset) => offset >= 0));
  assert.deepEqual([...chapterOffsets].sort((left, right) => left - right), chapterOffsets);
  assert.doesNotMatch(script, /bgm:for-the-band\//);
  assert.match(script, /bgm:none -enter=2200;/);
  assert.match(script, /END · 长崎大小姐防线崩溃中/);

  const sourceAnchors = [
    '最近长崎素世假笑的次数变多了',
    '我觉得你的贝斯最需要练习',
    '万圣节还有八个月啊',
    '我还会看《安达与岛村》',
    '妈妈，今年我有特别想送的人',
    '你是爱音的队友',
    '你我之间有比巧克力更贵重的东西',
    '——灯子不会成为任何人',
    '从明年开始不许送别人巧克力',
    'Soyorin，我现在要亲你咯',
  ];
  const sourceOffsets = sourceAnchors.map((anchor) => script.indexOf(anchor));
  assert.deepEqual(
    sourceAnchors.filter((anchor, index) => sourceOffsets[index] < 0),
    [],
  );
  assert.deepEqual([...sourceOffsets].sort((left, right) => left - right), sourceOffsets);
  assert.ok(script.lastIndexOf('被亲两下就亲两下吧') > sourceOffsets.at(-1));
});
