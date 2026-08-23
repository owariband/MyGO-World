import { access, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const SUPPORTED_BEATS = new Set([
  'chapter',
  'bgm',
  'stop_bgm',
  'background',
  'show',
  'hide',
  'dialogue',
  'narration',
  'choice',
  'jump',
  'ending',
]);
const POSITIONS = new Set(['left', 'center', 'right']);
const SAFE_ID = /^[a-z][a-z0-9_-]*$/;

export class StoryValidationError extends Error {
  constructor(diagnostics) {
    super(`Story validation failed with ${diagnostics.length} error(s)`);
    this.name = 'StoryValidationError';
    this.diagnostics = diagnostics;
  }
}

export async function loadStory(storyPath) {
  let raw;
  try {
    raw = await readFile(storyPath, 'utf8');
  } catch (error) {
    throw new Error(`Cannot read story file ${storyPath}: ${error.message}`);
  }

  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`Invalid JSON in ${storyPath}: ${error.message}`);
  }
}

export async function validateStory(story, rootDir) {
  const diagnostics = [];
  const assetChecks = [];
  const capabilityChecks = [];
  const modelCapabilities = new Map();
  const characters = new Map();
  const scenes = new Map();
  const beatLocations = new Map();

  requireString(story?.title, 'title', diagnostics);
  optionalString(story?.subtitle, 'subtitle', diagnostics);
  requireId(story?.entryScene, 'entryScene', diagnostics);
  requireArray(story?.characters, 'characters', diagnostics);
  requireArray(story?.scenes, 'scenes', diagnostics);

  for (const [index, character] of entries(story?.characters)) {
    const location = `characters[${index}]`;
    requireId(character?.id, `${location}.id`, diagnostics);
    requireString(character?.name, `${location}.name`, diagnostics);
    requireAsset(character?.figure, `${location}.figure`, 'game/figure', rootDir, diagnostics, assetChecks);
    if (character?.id && characters.has(character.id)) {
      diagnostics.push(`${location}.id duplicates character "${character.id}"`);
    } else if (character?.id) {
      characters.set(character.id, character);
    }
  }

  for (const [index, scene] of entries(story?.scenes)) {
    const location = `scenes[${index}]`;
    requireId(scene?.id, `${location}.id`, diagnostics);
    requireString(scene?.title, `${location}.title`, diagnostics);
    requireArray(scene?.beats, `${location}.beats`, diagnostics);
    if (scene?.id && scenes.has(scene.id)) {
      diagnostics.push(`${location}.id duplicates scene "${scene.id}"`);
    } else if (scene?.id) {
      scenes.set(scene.id, scene);
    }
  }

  if (story?.entryScene && !scenes.has(story.entryScene)) {
    diagnostics.push(`entryScene references unknown scene "${story.entryScene}"`);
  }

  for (const [sceneIndex, scene] of entries(story?.scenes)) {
    const seenBeatIds = new Set();
    for (const [beatIndex, beat] of entries(scene?.beats)) {
      const location = `scenes[${sceneIndex}].beats[${beatIndex}]`;
      requireId(beat?.id, `${location}.id`, diagnostics);
      requireString(beat?.type, `${location}.type`, diagnostics);
      if (beat?.id && seenBeatIds.has(beat.id)) {
        diagnostics.push(`${location}.id duplicates beat "${beat.id}" inside scene "${scene.id}"`);
      }
      if (beat?.id && beatLocations.has(beat.id)) {
        diagnostics.push(`${location}.id duplicates beat "${beat.id}" already used at ${beatLocations.get(beat.id)}`);
      } else if (beat?.id) {
        beatLocations.set(beat.id, location);
      }
      seenBeatIds.add(beat?.id);

      if (beat?.type && !SUPPORTED_BEATS.has(beat.type)) {
        diagnostics.push(`${location}.type "${beat.type}" is not supported`);
        continue;
      }

      switch (beat?.type) {
        case 'chapter':
          requireString(beat.title, `${location}.title`, diagnostics);
          optionalString(beat.subtitle, `${location}.subtitle`, diagnostics);
          break;
        case 'bgm':
          requireAsset(beat.asset, `${location}.asset`, 'game/bgm', rootDir, diagnostics, assetChecks);
          optionalRange(beat.volume, `${location}.volume`, 0, 100, diagnostics);
          optionalRange(beat.fadeMs, `${location}.fadeMs`, 0, 60000, diagnostics);
          break;
        case 'stop_bgm':
          optionalRange(beat.fadeMs, `${location}.fadeMs`, 0, 60000, diagnostics);
          break;
        case 'background':
          requireAsset(beat.asset, `${location}.asset`, 'game/background', rootDir, diagnostics, assetChecks);
          break;
        case 'show':
          validateCharacterRef(beat.character, `${location}.character`, characters, diagnostics);
          validatePosition(beat.position, `${location}.position`, diagnostics);
          optionalString(beat.motion, `${location}.motion`, diagnostics);
          optionalString(beat.expression, `${location}.expression`, diagnostics);
          optionalString(beat.enter, `${location}.enter`, diagnostics);
          validateCharacterCapabilities({
            beat,
            location,
            characters,
            rootDir,
            modelCapabilities,
            checks: capabilityChecks,
          });
          break;
        case 'hide':
          validatePosition(beat.position, `${location}.position`, diagnostics);
          break;
        case 'dialogue':
          validateCharacterRef(beat.character, `${location}.character`, characters, diagnostics);
          requireString(beat.text, `${location}.text`, diagnostics);
          optionalString(beat.motion, `${location}.motion`, diagnostics);
          optionalString(beat.expression, `${location}.expression`, diagnostics);
          validateCharacterCapabilities({
            beat,
            location,
            characters,
            rootDir,
            modelCapabilities,
            checks: capabilityChecks,
          });
          break;
        case 'narration':
          requireString(beat.text, `${location}.text`, diagnostics);
          break;
        case 'choice':
          requireArray(beat.options, `${location}.options`, diagnostics);
          if (Array.isArray(beat.options) && beat.options.length < 2) {
            diagnostics.push(`${location}.options must contain at least two choices`);
          }
          for (const [optionIndex, option] of entries(beat.options)) {
            requireString(option?.text, `${location}.options[${optionIndex}].text`, diagnostics);
            validateSceneRef(option?.target, `${location}.options[${optionIndex}].target`, scenes, diagnostics);
          }
          break;
        case 'jump':
          validateSceneRef(beat.target, `${location}.target`, scenes, diagnostics);
          break;
        case 'ending':
          requireString(beat.title, `${location}.title`, diagnostics);
          optionalString(beat.text, `${location}.text`, diagnostics);
          break;
        default:
          break;
      }
    }
  }

  const missingAssets = await Promise.all(assetChecks);
  diagnostics.push(...missingAssets.filter(Boolean));
  const invalidCapabilities = await Promise.all(capabilityChecks);
  diagnostics.push(...invalidCapabilities.flat().filter(Boolean));
  validateGraph(story, scenes, diagnostics);

  return diagnostics;
}

export function compileStory(story, sourcePath = 'authoring/story.json') {
  const characters = new Map(story.characters.map((character) => [character.id, character]));
  const lines = [
    `; Generated from ${sourcePath} by tools/mygo-author.mjs.`,
    '; Edit the project story and recompile; do not hand-edit this file.',
    '',
    `intro:${escapeContent(story.title)}|${escapeContent(story.subtitle ?? '一部由结构化剧本生成的 MyGO 可玩样片')};`,
    `jumpLabel:${story.entryScene};`,
    '',
  ];

  for (const scene of story.scenes) {
    lines.push(`; Scene: ${scene.title}`, `label:${scene.id};`);
    const positions = new Map();

    for (const beat of scene.beats) {
      switch (beat.type) {
        case 'chapter':
          lines.push(
            `intro:${escapeContent(beat.title)}${beat.subtitle ? `|${escapeContent(beat.subtitle)}` : ''};`,
          );
          break;
        case 'bgm':
          lines.push(
            `bgm:${escapeContent(beat.asset)} -volume=${beat.volume ?? 70} -enter=${beat.fadeMs ?? 1000};`,
          );
          break;
        case 'stop_bgm':
          lines.push(`bgm:none -enter=${beat.fadeMs ?? 1000};`);
          break;
        case 'background':
          lines.push(`changeBg:${escapeContent(beat.asset)} -next;`);
          break;
        case 'show': {
          const character = characters.get(beat.character);
          positions.set(beat.position, beat.character);
          lines.push(renderFigure(character, beat.position, beat));
          break;
        }
        case 'hide':
          positions.delete(beat.position);
          lines.push(`changeFigure:none${spacedPositionArg(beat.position)} -next;`);
          break;
        case 'dialogue': {
          const character = characters.get(beat.character);
          const position = findCharacterPosition(positions, beat.character);
          if (position && (beat.motion || beat.expression)) {
            lines.push(renderFigure(character, position, beat));
          }
          lines.push(`${escapeSpeaker(character.name)}:${escapeContent(beat.text)};`);
          break;
        }
        case 'narration':
          lines.push(`:${escapeContent(beat.text)};`);
          break;
        case 'choice':
          lines.push(`choose:${beat.options
            .map((option) => `${escapeChoice(option.text)}:${option.target}`)
            .join('|')};`);
          break;
        case 'jump':
          lines.push(`jumpLabel:${beat.target};`);
          break;
        case 'ending':
          lines.push(
            'changeFigure:none -left -next;',
            'changeFigure:none -next;',
            'changeFigure:none -right -next;',
            `intro:${escapeContent(beat.title)}${beat.text ? `|${escapeContent(beat.text)}` : ''};`,
            'end;',
          );
          break;
        default:
          break;
      }
    }

    lines.push('');
  }

  return `${lines.join('\n').trim()}\n`;
}

export async function compileStoryFile({ storyPath, outputPath, rootDir }) {
  const story = await loadStory(storyPath);
  const diagnostics = await validateStory(story, rootDir);
  if (diagnostics.length > 0) {
    throw new StoryValidationError(diagnostics);
  }
  const script = compileStory(story);
  await writeFile(outputPath, script, 'utf8');
  return {
    story,
    outputPath,
    sceneCount: story.scenes.length,
    beatCount: story.scenes.reduce((total, scene) => total + scene.beats.length, 0),
  };
}

function renderFigure(character, position, beat) {
  const args = [positionArg(position), '-next'];
  if (beat.motion) args.unshift(`-motion=${escapeArg(beat.motion)}`);
  if (beat.expression) args.unshift(`-expression=${escapeArg(beat.expression)}`);
  if (beat.enter) args.unshift(`-enter=${escapeArg(beat.enter)}`);
  return `changeFigure:${escapeContent(character.figure)} ${args.filter(Boolean).join(' ')};`;
}

function findCharacterPosition(positions, characterId) {
  for (const [position, activeCharacter] of positions) {
    if (activeCharacter === characterId) return position;
  }
  return null;
}

function validateGraph(story, scenes, diagnostics) {
  if (!story?.entryScene || !scenes.has(story.entryScene)) return;

  const reachable = new Set();
  const queue = [story.entryScene];
  while (queue.length > 0) {
    const sceneId = queue.shift();
    if (reachable.has(sceneId)) continue;
    reachable.add(sceneId);
    for (const target of sceneTargets(scenes.get(sceneId))) {
      if (scenes.has(target)) queue.push(target);
    }
  }

  for (const sceneId of scenes.keys()) {
    if (!reachable.has(sceneId)) diagnostics.push(`scene "${sceneId}" is unreachable from entryScene`);
  }

  const reverseEdges = new Map([...scenes.keys()].map((sceneId) => [sceneId, []]));
  const canReachEnding = new Set();
  const endingQueue = [];
  for (const [sceneId, scene] of scenes) {
    if (scene.beats.some((beat) => beat.type === 'ending')) {
      canReachEnding.add(sceneId);
      endingQueue.push(sceneId);
    }
    for (const target of sceneTargets(scene)) {
      if (reverseEdges.has(target)) reverseEdges.get(target).push(sceneId);
    }
  }

  while (endingQueue.length > 0) {
    const sceneId = endingQueue.shift();
    for (const predecessor of reverseEdges.get(sceneId) ?? []) {
      if (canReachEnding.has(predecessor)) continue;
      canReachEnding.add(predecessor);
      endingQueue.push(predecessor);
    }
  }

  for (const sceneId of reachable) {
    if (!canReachEnding.has(sceneId)) {
      diagnostics.push(`scene "${sceneId}" cannot reach an ending`);
    }
  }
}

function sceneTargets(scene) {
  const targets = [];
  for (const beat of scene?.beats ?? []) {
    if (beat.type === 'jump') targets.push(beat.target);
    if (beat.type === 'choice') targets.push(...(beat.options ?? []).map((option) => option.target));
  }
  return targets;
}

function validateCharacterCapabilities({ beat, location, characters, rootDir, modelCapabilities, checks }) {
  if (!beat?.character || (!beat.motion && !beat.expression)) return;
  const character = characters.get(beat.character);
  if (!character?.figure) return;

  let capabilitiesPromise = modelCapabilities.get(character.figure);
  if (!capabilitiesPromise) {
    const modelPath = path.join(rootDir, 'game/figure', character.figure);
    capabilitiesPromise = readFile(modelPath, 'utf8')
      .then((raw) => {
        const model = JSON.parse(raw);
        return {
          motions: new Set(Object.keys(model.motions ?? {})),
          expressions: new Set((model.expressions ?? []).map((expression) => expression.name)),
        };
      })
      .catch(() => null);
    modelCapabilities.set(character.figure, capabilitiesPromise);
  }

  checks.push(
    capabilitiesPromise.then((capabilities) => {
      if (!capabilities) return [];
      const diagnostics = [];
      if (beat.motion && !capabilities.motions.has(beat.motion)) {
        diagnostics.push(`${location}.motion "${beat.motion}" is not provided by ${character.figure}`);
      }
      if (beat.expression && !capabilities.expressions.has(beat.expression)) {
        diagnostics.push(`${location}.expression "${beat.expression}" is not provided by ${character.figure}`);
      }
      return diagnostics;
    }),
  );
}

function requireAsset(value, location, baseDir, rootDir, diagnostics, checks) {
  requireString(value, location, diagnostics);
  if (typeof value !== 'string' || value.length === 0) return;
  if (path.isAbsolute(value) || value.includes('..')) {
    diagnostics.push(`${location} must be a project-relative asset path`);
    return;
  }
  const fullPath = path.join(rootDir, baseDir, value);
  checks.push(
    access(fullPath).then(
      () => null,
      () => `${location} references missing asset "${path.relative(rootDir, fullPath)}"`,
    ),
  );
}

function validateCharacterRef(value, location, characters, diagnostics) {
  requireId(value, location, diagnostics);
  if (value && !characters.has(value)) diagnostics.push(`${location} references unknown character "${value}"`);
}

function validateSceneRef(value, location, scenes, diagnostics) {
  requireId(value, location, diagnostics);
  if (value && !scenes.has(value)) diagnostics.push(`${location} references unknown scene "${value}"`);
}

function validatePosition(value, location, diagnostics) {
  if (!POSITIONS.has(value)) diagnostics.push(`${location} must be one of left, center, right`);
}

function requireString(value, location, diagnostics) {
  if (typeof value !== 'string' || value.trim().length === 0) {
    diagnostics.push(`${location} must be a non-empty string`);
  }
}

function optionalString(value, location, diagnostics) {
  if (value !== undefined && (typeof value !== 'string' || value.trim().length === 0)) {
    diagnostics.push(`${location} must be a non-empty string when present`);
  }
}

function optionalRange(value, location, min, max, diagnostics) {
  if (value === undefined) return;
  if (!Number.isFinite(value) || value < min || value > max) {
    diagnostics.push(`${location} must be a number between ${min} and ${max}`);
  }
}

function requireId(value, location, diagnostics) {
  requireString(value, location, diagnostics);
  if (typeof value === 'string' && !SAFE_ID.test(value)) {
    diagnostics.push(`${location} must match ${SAFE_ID}`);
  }
}

function requireArray(value, location, diagnostics) {
  if (!Array.isArray(value) || value.length === 0) {
    diagnostics.push(`${location} must be a non-empty array`);
  }
}

function entries(value) {
  return Array.isArray(value) ? value.entries() : [];
}

function positionArg(position) {
  if (position === 'left') return '-left';
  if (position === 'right') return '-right';
  return '';
}

function spacedPositionArg(position) {
  const argument = positionArg(position);
  return argument ? ` ${argument}` : '';
}

function escapeContent(value) {
  return String(value).replaceAll('\\', '\\\\').replaceAll(';', '\\;');
}

function escapeChoice(value) {
  return escapeContent(value).replaceAll(':', '\\:').replaceAll('|', '\\|');
}

function escapeSpeaker(value) {
  return String(value).replaceAll(':', '：').replaceAll(';', '；');
}

function escapeArg(value) {
  return String(value).replaceAll(' ', '_').replaceAll(';', '');
}
