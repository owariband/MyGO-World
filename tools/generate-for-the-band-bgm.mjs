#!/usr/bin/env node

import { mkdir, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const devRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webgalRoot = process.env.WEBGAL_ROOT
  ? path.resolve(process.env.WEBGAL_ROOT)
  : path.resolve(devRoot, '../MyGO_v3.1.1_ForScript');
const outputDir = path.join(webgalRoot, 'game/bgm/for-the-band');
const sampleRate = 44100;
const durationSeconds = 24;
const totalSamples = sampleRate * durationSeconds;

const tracks = [
  {
    file: 'rehearsal.mp3',
    bpm: 112,
    progression: [[60, 64, 67], [57, 60, 64], [53, 57, 60], [55, 59, 62]],
    melody: [67, 69, 71, 72, 71, 69, 67, 64],
    lead: 'triangle',
    bassGain: 0.18,
    padGain: 0.13,
    leadGain: 0.12,
    percussion: 0.08,
  },
  {
    file: 'home.mp3',
    bpm: 78,
    progression: [[60, 64, 67], [64, 67, 71], [57, 60, 64], [55, 60, 64]],
    melody: [64, 67, 69, 67, 64, 62, 60, 62],
    lead: 'sine',
    bassGain: 0.13,
    padGain: 0.2,
    leadGain: 0.1,
    percussion: 0.025,
  },
  {
    file: 'night.mp3',
    bpm: 66,
    progression: [[57, 60, 64], [53, 57, 60], [55, 59, 62], [52, 55, 59]],
    melody: [64, 63, 60, 59, 60, 57, 55, 59],
    lead: 'sine',
    bassGain: 0.16,
    padGain: 0.18,
    leadGain: 0.075,
    percussion: 0.015,
    detune: 0.35,
  },
  {
    file: 'live.mp3',
    bpm: 144,
    progression: [[60, 64, 67], [55, 59, 62], [57, 60, 64], [53, 57, 60]],
    melody: [72, 71, 69, 67, 69, 71, 72, 76],
    lead: 'softSaw',
    bassGain: 0.22,
    padGain: 0.1,
    leadGain: 0.15,
    percussion: 0.14,
  },
  {
    file: 'confession.mp3',
    bpm: 72,
    progression: [[60, 64, 67], [55, 60, 64], [57, 60, 64], [53, 57, 60]],
    melody: [67, 69, 72, 71, 69, 67, 64, 67],
    lead: 'triangle',
    bassGain: 0.12,
    padGain: 0.22,
    leadGain: 0.09,
    percussion: 0.02,
  },
];

await mkdir(outputDir, { recursive: true });
for (const track of tracks) {
  const wavPath = path.join(os.tmpdir(), `generative-mygo-${track.file.replace('.mp3', '.wav')}`);
  const pcm = renderTrack(track);
  await writeFile(wavPath, encodeWav(pcm));
  const outputPath = path.join(outputDir, track.file);
  const ffmpeg = spawnSync(
    'ffmpeg',
    ['-y', '-loglevel', 'error', '-i', wavPath, '-codec:a', 'libmp3lame', '-b:a', '160k', outputPath],
    { encoding: 'utf8' },
  );
  if (ffmpeg.status !== 0) {
    throw new Error(`ffmpeg failed for ${track.file}: ${ffmpeg.stderr}`);
  }
  console.log(`generated ${path.relative(webgalRoot, outputPath)}`);
}

function renderTrack(track) {
  const samples = new Int16Array(totalSamples * 2);
  const beatSeconds = 60 / track.bpm;
  const progressionSeconds = durationSeconds / track.progression.length;
  const melodyStepSeconds = durationSeconds / track.melody.length;

  for (let sampleIndex = 0; sampleIndex < totalSamples; sampleIndex += 1) {
    const time = sampleIndex / sampleRate;
    const loopEnvelope = smoothLoopEnvelope(time, durationSeconds);
    const chordIndex = Math.floor(time / progressionSeconds) % track.progression.length;
    const chord = track.progression[chordIndex];
    const melodyIndex = Math.floor(time / melodyStepSeconds) % track.melody.length;
    const melodyTime = time % melodyStepSeconds;
    const noteEnvelope = softEnvelope(melodyTime, melodyStepSeconds);

    const bass = oscillator('sine', midiToFrequency(chord[0] - 24), time) * track.bassGain;
    const pad =
      chord.reduce((sum, note, voiceIndex) => {
        const detune = track.detune ? (voiceIndex - 1) * track.detune : 0;
        return sum + oscillator('sine', midiToFrequency(note + detune), time);
      }, 0) /
      chord.length *
      track.padGain;
    const lead =
      oscillator(track.lead, midiToFrequency(track.melody[melodyIndex]), time) *
      noteEnvelope *
      track.leadGain;
    const beatPhase = (time % beatSeconds) / beatSeconds;
    const kick = Math.exp(-beatPhase * 18) * Math.sin(2 * Math.PI * 55 * time) * track.percussion;
    const shimmer =
      Math.sin(2 * Math.PI * midiToFrequency(chord[2] + 12) * time) *
      Math.exp(-((time % (beatSeconds * 2)) / beatSeconds) * 5) *
      track.percussion *
      0.24;

    const dry = (bass + pad + lead + kick + shimmer) * loopEnvelope;
    const left = Math.tanh(dry + pad * 0.08);
    const right = Math.tanh(dry - pad * 0.08 + lead * 0.04);
    samples[sampleIndex * 2] = Math.round(left * 32767);
    samples[sampleIndex * 2 + 1] = Math.round(right * 32767);
  }

  return samples;
}

function oscillator(type, frequency, time) {
  const phase = (frequency * time) % 1;
  if (type === 'triangle') return 1 - 4 * Math.abs(phase - 0.5);
  if (type === 'softSaw') {
    return (
      Math.sin(2 * Math.PI * phase) * 0.72 +
      Math.sin(4 * Math.PI * phase) * 0.2 +
      Math.sin(6 * Math.PI * phase) * 0.08
    );
  }
  return Math.sin(2 * Math.PI * phase);
}

function softEnvelope(time, duration) {
  const attack = Math.min(1, time / 0.08);
  const release = Math.min(1, (duration - time) / Math.min(0.35, duration / 3));
  return Math.max(0, Math.min(attack, release));
}

function smoothLoopEnvelope(time, duration) {
  const edge = 0.08;
  const fadeIn = Math.min(1, time / edge);
  const fadeOut = Math.min(1, (duration - time) / edge);
  return Math.max(0, Math.min(fadeIn, fadeOut));
}

function midiToFrequency(note) {
  return 440 * 2 ** ((note - 69) / 12);
}

function encodeWav(samples) {
  const dataSize = samples.byteLength;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(2, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 4, 28);
  buffer.writeUInt16LE(4, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(dataSize, 40);
  Buffer.from(samples.buffer).copy(buffer, 44);
  return buffer;
}
