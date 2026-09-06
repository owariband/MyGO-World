import { access, mkdir, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { compileStory, loadStory, validateStory } from './mygo-author-lib.mjs';

const PROJECT_ID = /^[a-z][a-z0-9-]*$/;
const DEFAULT_TITLE_IMAGE = '角色生活地点/mygo/爱音家门口（白天有车）.jpg';
const DEFAULT_GAME_LOGO = 'WebGAL_MyGO_Enter.webp';
const DEFAULT_TITLE_BGM = 's_Title.mp3';

export class ProjectValidationError extends Error {
  constructor(diagnostics) {
    super(`Project validation failed with ${diagnostics.length} error(s)`);
    this.name = 'ProjectValidationError';
    this.diagnostics = diagnostics;
  }
}

export function resolveProjectPaths(devRoot, projectId) {
  assertProjectId(projectId);
  const projectDir = path.join(devRoot, 'projects', projectId);
  const buildDir = path.join(projectDir, 'build');
  return {
    projectId,
    projectDir,
    manifestPath: path.join(projectDir, 'project.json'),
    storyPath: path.join(projectDir, 'story.json'),
    buildDir,
    scriptOutputPath: path.join(buildDir, 'start.txt'),
    configOutputPath: path.join(buildDir, 'config.txt'),
    playerOutputPath: path.join(buildDir, 'player.json'),
  };
}

export async function resolvePreviewScene(webgalRoot, sceneRef) {
  if (typeof sceneRef !== 'string' || sceneRef.length === 0 || sceneRef.includes('\\')) {
    throw new Error('Preview scene must be a non-empty POSIX path under game/scene/generated');
  }
  const normalized = path.posix.normalize(sceneRef).replace(/^\.\//, '');
  if (!normalized.startsWith('generated/') || !normalized.endsWith('.txt')) {
    throw new Error('Preview scene must be a .txt file under game/scene/generated');
  }
  const sceneRoot = path.resolve(webgalRoot, 'game', 'scene');
  const candidate = path.resolve(sceneRoot, ...normalized.split('/'));
  if (!isWithinDirectory(candidate, sceneRoot)) {
    throw new Error('Preview scene must stay within game/scene/generated');
  }
  let resolved;
  let resolvedGeneratedRoot;
  try {
    [resolved, resolvedGeneratedRoot] = await Promise.all([
      realpath(candidate),
      realpath(path.resolve(sceneRoot, 'generated')),
    ]);
  } catch (error) {
    throw new Error(`Preview scene does not exist: ${candidate}`);
  }
  if (!isWithinDirectory(resolved, resolvedGeneratedRoot)) {
    throw new Error('Preview scene must stay within game/scene/generated');
  }
  return normalized;
}

export function renderPreviewStart(sceneRef) {
  return `changeScene:${sceneRef};\n`;
}

export async function loadProject(devRoot, projectId) {
  const paths = resolveProjectPaths(devRoot, projectId);
  const [manifest, story] = await Promise.all([
    readJson(paths.manifestPath, 'project manifest'),
    loadStory(paths.storyPath),
  ]);
  return { projectId, paths, manifest, story };
}

export async function validateProject(devRoot, webgalRoot, projectId) {
  const project = await loadProject(devRoot, projectId);
  const diagnostics = [];
  const { manifest, story } = project;

  requireString(manifest?.id, 'project.id', diagnostics);
  if (manifest?.id !== projectId) {
    diagnostics.push(`project.id "${manifest?.id}" must match directory "${projectId}"`);
  }
  requireString(manifest?.name, 'project.name', diagnostics);
  requireString(manifest?.gameKey, 'project.gameKey', diagnostics);
  requireString(manifest?.titleImage, 'project.titleImage', diagnostics);
  requireString(manifest?.titleBgm, 'project.titleBgm', diagnostics);
  requireString(manifest?.gameLogo, 'project.gameLogo', diagnostics);
  requireString(manifest?.defaultLanguage, 'project.defaultLanguage', diagnostics);

  if (!Number.isFinite(manifest?.textSpeed) || manifest.textSpeed < 0 || manifest.textSpeed > 100) {
    diagnostics.push('project.textSpeed must be a number between 0 and 100');
  }
  if (!Number.isInteger(manifest?.playerSettingsVersion) || manifest.playerSettingsVersion < 1) {
    diagnostics.push('project.playerSettingsVersion must be a positive integer');
  }

  const assetChecks = [
    checkAsset(webgalRoot, 'game/background', manifest?.titleImage, 'project.titleImage'),
    checkAsset(webgalRoot, 'game/background', manifest?.gameLogo, 'project.gameLogo'),
    checkAsset(webgalRoot, 'game/bgm', manifest?.titleBgm, 'project.titleBgm'),
  ];
  diagnostics.push(...(await Promise.all(assetChecks)).filter(Boolean));
  diagnostics.push(...(await validateStory(story, webgalRoot)).map((diagnostic) => `story: ${diagnostic}`));

  return { ...project, diagnostics };
}

export async function compileProject({ devRoot, webgalRoot, projectId }) {
  const project = await validateProject(devRoot, webgalRoot, projectId);
  if (project.diagnostics.length > 0) {
    throw new ProjectValidationError(project.diagnostics);
  }

  const { paths, manifest, story } = project;
  await mkdir(paths.buildDir, { recursive: true });
  const script = compileStory(story, `projects/${projectId}/story.json`);
  const config = renderGameConfig(manifest);
  const player = `${JSON.stringify(renderPlayerConfig(manifest), null, 2)}\n`;
  await Promise.all([
    writeFile(paths.scriptOutputPath, script, 'utf8'),
    writeFile(paths.configOutputPath, config, 'utf8'),
    writeFile(paths.playerOutputPath, player, 'utf8'),
  ]);

  return {
    ...project,
    sceneCount: story.scenes.length,
    beatCount: story.scenes.reduce((total, scene) => total + scene.beats.length, 0),
  };
}

export async function createProject({ devRoot, webgalRoot, projectId, title }) {
  assertProjectId(projectId);
  requireCreateTitle(title);
  const paths = resolveProjectPaths(devRoot, projectId);

  try {
    await access(paths.projectDir);
    throw new Error(`Project "${projectId}" already exists`);
  } catch (error) {
    if (error.message.includes('already exists')) throw error;
  }

  await mkdir(path.dirname(paths.projectDir), { recursive: true });
  await mkdir(paths.projectDir, { recursive: false });
  const manifest = createManifest(projectId, title);
  const story = createStarterStory(title);
  await Promise.all([
    writeFile(paths.manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' }),
    writeFile(paths.storyPath, `${JSON.stringify(story, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' }),
  ]);
  return compileProject({ devRoot, webgalRoot, projectId });
}

export async function listProjects(devRoot) {
  const projectsDir = path.join(devRoot, 'projects');
  let entries = [];
  try {
    entries = await readdir(projectsDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const projects = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !PROJECT_ID.test(entry.name)) continue;
    try {
      const manifest = await readJson(path.join(projectsDir, entry.name, 'project.json'), 'project manifest');
      projects.push({
        id: entry.name,
        name: manifest.name,
        gameKey: manifest.gameKey,
      });
    } catch {
      projects.push({ id: entry.name, name: '(invalid project)', gameKey: '' });
    }
  }
  return projects.sort((left, right) => left.id.localeCompare(right.id));
}

export function renderGameConfig(manifest) {
  const lines = [
    `Game_name:${cleanConfigValue(manifest.name)}`,
    `Game_key:${cleanConfigValue(manifest.gameKey)}`,
    `Title_img:${cleanConfigValue(manifest.titleImage)}`,
    `Title_bgm:${cleanConfigValue(manifest.titleBgm)}`,
    `Game_Logo:${cleanConfigValue(manifest.gameLogo)}`,
    `Default_Language:${cleanConfigValue(manifest.defaultLanguage)}`,
    `Show_panic:${manifest.showPanic !== false}`,
    `Enable_Appreciation:${manifest.enableAppreciation !== false}`,
    `Legacy_Expression_Blend_Mode:${manifest.legacyExpressionBlendMode !== false}`,
    `Positioning_Type:${cleanConfigValue(manifest.positioningType ?? 'M_3_1_0')}`,
    `Stage_Width:${manifest.stageWidth ?? 2560}`,
    `Stage_Height:${manifest.stageHeight ?? 1440}`,
    `Auto_Rotate:${manifest.autoRotate !== false}`,
  ];
  return `${lines.map((line) => `${line};`).join('\n')}\n`;
}

export function renderPlayerConfig(manifest) {
  return {
    version: manifest.playerSettingsVersion,
    storageKey: manifest.gameKey,
    textSpeed: manifest.textSpeed,
    migrateAtOrBelow: manifest.migrateTextSpeedAtOrBelow ?? 50,
  };
}

function createManifest(projectId, title) {
  return {
    formatVersion: 1,
    id: projectId,
    name: title,
    gameKey: `generative-mygo.${projectId}`,
    titleImage: DEFAULT_TITLE_IMAGE,
    titleBgm: DEFAULT_TITLE_BGM,
    gameLogo: DEFAULT_GAME_LOGO,
    defaultLanguage: 'zh_CN',
    textSpeed: 80,
    migrateTextSpeedAtOrBelow: 50,
    playerSettingsVersion: 1,
  };
}

function createStarterStory(title) {
  return {
    formatVersion: 1,
    title,
    entryScene: 'opening',
    characters: [
      {
        id: 'anon',
        name: '爱音',
        figure: 'anon/casual-2023/model.json',
      },
    ],
    scenes: [
      {
        id: 'opening',
        title: '新的故事',
        beats: [
          {
            id: 'opening_background',
            type: 'background',
            asset: DEFAULT_TITLE_IMAGE,
          },
          {
            id: 'anon_enters',
            type: 'show',
            character: 'anon',
            position: 'center',
            motion: 'anon/idle01',
            expression: 'anon/smile01',
            enter: 'enter-from-bottom',
          },
          {
            id: 'first_line',
            type: 'dialogue',
            character: 'anon',
            text: `这里是《${title}》的第一个场景。`,
          },
          {
            id: 'starter_ending',
            type: 'ending',
            title: 'STARTER END',
            text: '现在可以让 Agent 从这里继续创作。',
          },
        ],
      },
    ],
  };
}

async function readJson(filePath, label) {
  let raw;
  try {
    raw = await readFile(filePath, 'utf8');
  } catch (error) {
    throw new Error(`Cannot read ${label} ${filePath}: ${error.message}`);
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`Invalid JSON in ${filePath}: ${error.message}`);
  }
}

async function checkAsset(webgalRoot, baseDir, value, label) {
  if (typeof value !== 'string' || value.length === 0) return null;
  if (path.isAbsolute(value) || value.includes('..')) {
    return `${label} must be a project-relative asset path`;
  }
  const fullPath = path.join(webgalRoot, baseDir, value);
  try {
    await access(fullPath);
    return null;
  } catch {
    return `${label} references missing asset "${path.relative(webgalRoot, fullPath)}"`;
  }
}

function assertProjectId(projectId) {
  if (!PROJECT_ID.test(projectId ?? '')) {
    throw new Error('Project id must start with a lowercase letter and contain only lowercase letters, numbers, or "-"');
  }
}

function isWithinDirectory(filePath, directory) {
  const relativePath = path.relative(directory, filePath);
  return relativePath === '' || (
    !relativePath.startsWith(`..${path.sep}`)
    && relativePath !== '..'
    && !path.isAbsolute(relativePath)
  );
}

function requireString(value, label, diagnostics) {
  if (typeof value !== 'string' || value.trim().length === 0) {
    diagnostics.push(`${label} must be a non-empty string`);
  }
}

function requireCreateTitle(title) {
  if (typeof title !== 'string' || title.trim().length === 0) {
    throw new Error('Creating a project requires --title <title>');
  }
}

function cleanConfigValue(value) {
  return String(value).replaceAll('\n', ' ').replaceAll('\r', ' ').replaceAll(';', '；');
}
