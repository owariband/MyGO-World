#!/usr/bin/env node

import { createReadStream, watch } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { StoryValidationError } from './mygo-author-lib.mjs';
import {
  compileProject,
  createProject,
  listProjects,
  ProjectValidationError,
  resolveProjectPaths,
  validateProject,
} from './mygo-project.mjs';

const devRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webgalRoot = process.env.WEBGAL_ROOT
  ? path.resolve(process.env.WEBGAL_ROOT)
  : path.resolve(devRoot, '../MyGO_v3.1.1_ForScript');
const defaultProjectId = 'rain-after';
const command = process.argv[2] ?? 'help';
const options = parseOptions(process.argv.slice(3));
const projectId = options.project ?? defaultProjectId;
const dynamicMode = command === 'dynamic';
let dynamicRender = null;

try {
  switch (command) {
    case 'create':
      await create();
      break;
    case 'list':
      await list();
      break;
    case 'check':
      await check();
      break;
    case 'compile':
      await compile();
      break;
    case 'serve':
      await serve();
      break;
    case 'dev':
      await compile();
      watchStory();
      await serve();
      break;
    case 'dynamic':
      await compile();
      await serve();
      break;
    case 'help':
    case '--help':
    case '-h':
      printHelp();
      break;
    default:
      throw new Error(`Unknown command "${command}". Run "npm run dev -- --help" for usage.`);
  }
} catch (error) {
  reportError(error);
  process.exitCode = 1;
}

async function check() {
  const project = await validateProject(devRoot, webgalRoot, projectId);
  if (project.diagnostics.length > 0) throw new ProjectValidationError(project.diagnostics);
  console.log(`✓ project ${projectId} is valid`);
  console.log(`  ${project.story.characters.length} characters · ${project.story.scenes.length} scenes`);
}

async function compile() {
  const result = await compileProject({ devRoot, webgalRoot, projectId });
  console.log(`✓ compiled project ${projectId} → ${path.relative(devRoot, result.paths.buildDir)}`);
  console.log(`  ${result.sceneCount} scenes · ${result.beatCount} beats`);
  return result;
}

async function serve() {
  const project = await ensureBuiltProject();
  const staticRoots = await Promise.all(
    [...new Set([devRoot, webgalRoot])].map(async (root) => ({ path: root, realPath: await realpath(root) })),
  );
  const port = parsePort(options.port ?? process.env.PORT ?? '4173');
  const server = http.createServer((request, response) => {
    if (dynamicRender?.handleHttp(request, response)) return;
    handleRequest(request, response, project, staticRoots).catch((error) => {
      console.error(error);
      if (!response.headersSent) response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end('Internal server error');
    });
  });
  if (dynamicMode) {
    const { attachDynamicRender } = await import('../extensions/dynamic-render/server.mjs');
    dynamicRender = await attachDynamicRender({ server, devRoot, webgalRoot, projectId });
  }

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, options.host ?? '127.0.0.1', resolve);
  });

  const address = server.address();
  const host = typeof address === 'object' && address?.address === '::' ? '127.0.0.1' : options.host ?? '127.0.0.1';
  console.log(`▶ ${project.manifest.name} (${projectId}) is running at http://${host}:${port}`);
  if (dynamicMode) console.log('  Dynamic timeline: enabled');
  console.log(`  Save namespace: ${project.manifest.gameKey}`);
  console.log('  Press Ctrl+C to stop.');
  await new Promise(() => {});
}

function watchStory() {
  const paths = resolveProjectPaths(devRoot, projectId);
  let timer;
  const rebuild = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        await compile();
        console.log('  Reload the game or start a new game to see the revision.');
      } catch (error) {
        reportError(error);
      }
    }, 100);
  };
  watch(paths.storyPath, rebuild);
  watch(paths.manifestPath, rebuild);
  console.log(`◌ watching projects/${projectId}/{project.json,story.json}`);
}

async function handleRequest(request, response, project, staticRoots) {
  const requestTarget = request.url ?? '/';
  const queryIndex = requestTarget.indexOf('?');
  const rawPath = queryIndex === -1 ? requestTarget : requestTarget.slice(0, queryIndex);
  const decodedPath = decodeURIComponent(rawPath);
  const relativePath = decodedPath === '/' ? 'index.html' : decodedPath.replace(/^\/+/, '');
  const virtualPath = resolveVirtualProjectPath(relativePath, project.paths);
  const roots = virtualPath ? staticRoots.slice(0, 1) : staticRoots;
  const candidates = virtualPath
    ? [path.resolve(virtualPath)]
    : roots.map((root) => path.resolve(root.path, relativePath));

  if (candidates.some((candidate, index) => !isWithinRoot(candidate, roots[index].path))) {
    response.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Forbidden');
    return;
  }

  let absolutePath;
  let fileStat;
  for (const [index, candidate] of candidates.entries()) {
    try {
      const resolvedCandidate = await realpath(candidate);
      if (!isWithinRoot(resolvedCandidate, roots[index].realPath)) {
        response.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        response.end('Forbidden');
        return;
      }
      const candidateStat = await stat(resolvedCandidate);
      if (!candidateStat.isFile()) continue;
      absolutePath = resolvedCandidate;
      fileStat = candidateStat;
      break;
    } catch {
      // Try the WebGAL root when the file is absent from the development root.
    }
  }

  if (!absolutePath) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Not found');
    return;
  }

  const headers = {
    'Accept-Ranges': 'bytes',
    'Cache-Control': virtualPath ? 'no-store' : 'no-cache',
    'Content-Type': mimeType(absolutePath),
    'X-Generative-MyGO-Project': project.projectId,
  };
  const range = parseRange(request.headers.range, fileStat.size);

  if (range) {
    headers['Content-Length'] = String(range.end - range.start + 1);
    headers['Content-Range'] = `bytes ${range.start}-${range.end}/${fileStat.size}`;
    response.writeHead(206, headers);
    if (request.method === 'HEAD') {
      response.end();
      return;
    }
    createReadStream(absolutePath, range).pipe(response);
    return;
  }

  headers['Content-Length'] = String(fileStat.size);
  response.writeHead(200, headers);
  if (request.method === 'HEAD') {
    response.end();
    return;
  }
  createReadStream(absolutePath).pipe(response);
}

async function create() {
  const newProjectId = options.project;
  if (!newProjectId) throw new Error('Creating a project requires --project <project-id>');
  const result = await createProject({ devRoot, webgalRoot, projectId: newProjectId, title: options.title });
  console.log(`✓ created project ${newProjectId}`);
  console.log(`  ${path.relative(devRoot, result.paths.projectDir)}`);
}

async function list() {
  const projects = await listProjects(devRoot);
  if (projects.length === 0) {
    console.log('No projects found.');
    return;
  }
  for (const project of projects) {
    console.log(`${project.id}\t${project.name}\t${project.gameKey}`);
  }
}

async function ensureBuiltProject() {
  const paths = resolveProjectPaths(devRoot, projectId);
  try {
    await Promise.all([stat(paths.scriptOutputPath), stat(paths.configOutputPath), stat(paths.playerOutputPath)]);
    const project = await validateProject(devRoot, webgalRoot, projectId);
    if (project.diagnostics.length > 0) throw new ProjectValidationError(project.diagnostics);
    return project;
  } catch (error) {
    if (error instanceof ProjectValidationError) throw error;
    return compileProject({ devRoot, webgalRoot, projectId });
  }
}

function resolveVirtualProjectPath(relativePath, paths) {
  const virtualFiles = {
    'game/config.txt': paths.configOutputPath,
    'game/scene/start.txt': paths.scriptOutputPath,
    'authoring/player.json': paths.playerOutputPath,
  };
  return virtualFiles[relativePath] ?? null;
}

function isWithinRoot(filePath, root) {
  const relativePath = path.relative(root, filePath);
  return relativePath === '' || (!relativePath.startsWith(`..${path.sep}`) && relativePath !== '..' && !path.isAbsolute(relativePath));
}

function parseRange(header, fileSize) {
  if (!header) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match) return null;
  const start = match[1] ? Number.parseInt(match[1], 10) : 0;
  const end = match[2] ? Number.parseInt(match[2], 10) : fileSize - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start || start >= fileSize) {
    return null;
  }
  return { start, end: Math.min(end, fileSize - 1) };
}

function mimeType(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return {
    '.css': 'text/css; charset=utf-8',
    '.gif': 'image/gif',
    '.html': 'text/html; charset=utf-8',
    '.ico': 'image/x-icon',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.moc': 'application/octet-stream',
    '.mp3': 'audio/mpeg',
    '.mtn': 'application/octet-stream',
    '.ogg': 'audio/ogg',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.ttf': 'font/ttf',
    '.txt': 'text/plain; charset=utf-8',
    '.wav': 'audio/wav',
    '.webp': 'image/webp',
  }[extension] ?? 'application/octet-stream';
}

function parseOptions(args) {
  const parsed = {};
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (!argument.startsWith('--')) continue;
    const [rawKey, inlineValue] = argument.slice(2).split('=', 2);
    const value = inlineValue ?? args[index + 1];
    if (inlineValue === undefined) index += 1;
    parsed[rawKey] = value;
  }
  return parsed;
}

function parsePort(value) {
  const port = Number.parseInt(value, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port "${value}"`);
  }
  return port;
}

function reportError(error) {
  if (error instanceof StoryValidationError || error instanceof ProjectValidationError) {
    console.error(`✗ ${error.name === 'ProjectValidationError' ? 'project' : 'story'} validation failed`);
    for (const diagnostic of error.diagnostics) console.error(`  - ${diagnostic}`);
    return;
  }
  console.error(`✗ ${error.message}`);
}

function printHelp() {
  console.log(`Generative MyGO authoring commands

Usage:
  node tools/mygo-author.mjs list
  node tools/mygo-author.mjs create --project <id> --title <title>
  node tools/mygo-author.mjs check --project <id>
  node tools/mygo-author.mjs compile --project <id>
  node tools/mygo-author.mjs dev --project <id> [--port 4173]
  node tools/mygo-author.mjs dynamic --project <id> [--port 4173]
  node tools/mygo-author.mjs serve --project <id> [--port 4173]

Projects are isolated under projects/<id>. Each project owns its story, game
configuration, save namespace, player settings, and build output while sharing
the WebGAL MyGO runtime and the large game asset library.`);
}
