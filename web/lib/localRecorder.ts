"use client";
/**
 * Local mic capture for on-device speech-to-text (work profile). Records raw PCM with Web Audio, detects
 * speech by signal level, and hands back a 16 kHz mono WAV for POST /api/voice/transcribe. No browser
 * speech service is involved, so audio never leaves the machine.
 */

export interface LocalRecording {
  stop(): void;
}

export interface LocalRecorderOpts {
  silenceMs: number; // stop after this long without hearing a voice (counted from start, then from the last voice)
  endOfSpeechMs?: number; // hands-free: stop this long after the speaker goes quiet
  maxMs?: number;
  onVoice?: () => void; // first frame that sounds like speech
  onLevel?: (level: number, voiced: boolean) => void; // every audio frame (~85 ms): 0..1 level, voice detected
  partialMs?: number; // live caption: hand over the audio so far this often while you talk
  onPartial?: (wav: Blob) => void;
  onDone: (wav: Blob | null, reason: string) => void; // null: nothing was said
  onError: (message: string, code: string) => void;
}

const TARGET_RATE = 16000;
/** Live captions re-decode a rolling window of the most recent audio (whisper's window is 30 s). */
const PARTIAL_MAX_S = 25;

function tail(chunks: Float32Array[], maxSamples: number): Float32Array[] {
  const out: Float32Array[] = [];
  let n = 0;
  for (let i = chunks.length - 1; i >= 0 && n < maxSamples; i--) {
    out.unshift(chunks[i]);
    n += chunks[i].length;
  }
  return out;
}

export async function startLocalRecorder(o: LocalRecorderOpts): Promise<LocalRecording | null> {
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    const name = (e as { name?: string }).name ?? "Error";
    if (name === "NotAllowedError" || name === "SecurityError")
      o.onError(`Microphone blocked: allow mic access for ${location.host} in the browser's site settings`, "not-allowed");
    else if (name === "NotFoundError") o.onError("No microphone found", "audio-capture");
    else o.onError(`Mic failed to start: ${e}`, name);
    return null;
  }

  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  const startedAt = performance.now();
  let lastVoice = startedAt;
  let heard = false;
  let floor = 0.005; // running noise floor estimate
  let done = false;
  let lastPartial = startedAt;

  const finish = (reason: string) => {
    if (done) return;
    done = true;
    proc.disconnect();
    source.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
    o.onDone(heard ? encodeWav(chunks, ctx.sampleRate) : null, reason);
  };

  proc.onaudioprocess = (e) => {
    if (done) return;
    const data = e.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(data));
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    const now = performance.now();
    const voiced = rms > Math.max(0.012, floor * 3);
    if (voiced) {
      if (!heard) o.onVoice?.();
      heard = true;
      lastVoice = now;
    } else {
      floor = floor * 0.95 + rms * 0.05;
    }
    // level on a log scale: -60 dBFS (silence) .. -10 dBFS (loud)
    o.onLevel?.(Math.max(0, Math.min(1, (20 * Math.log10(rms + 1e-9) + 60) / 50)), voiced);
    if (o.onPartial && o.partialMs && heard && now - lastPartial >= o.partialMs) {
      lastPartial = now;
      o.onPartial(encodeWav(tail(chunks, ctx.sampleRate * PARTIAL_MAX_S), ctx.sampleRate));
    }
    if (now - lastVoice >= o.silenceMs) finish(`${o.silenceMs / 1000}s pause`);
    else if (heard && o.endOfSpeechMs && now - lastVoice >= o.endOfSpeechMs) finish("end of speech");
    else if (now - startedAt >= (o.maxMs ?? 60000)) finish("max length");
  };
  source.connect(proc);
  proc.connect(ctx.destination); // ScriptProcessor only runs when connected; it outputs silence

  return { stop: () => finish("released") };
}

function encodeWav(chunks: Float32Array[], rate: number): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const input = new Float32Array(total);
  let off = 0;
  for (const c of chunks) {
    input.set(c, off);
    off += c.length;
  }
  // linear-interpolation resample to 16 kHz
  const ratio = rate / TARGET_RATE;
  const n = Math.floor(total / ratio);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const x = i * ratio;
    const i0 = Math.floor(x);
    const i1 = Math.min(i0 + 1, total - 1);
    const v = input[i0] + (input[i1] - input[i0]) * (x - i0);
    pcm[i] = Math.max(-1, Math.min(1, v)) * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.byteLength);
  const dv = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((ch, i) => dv.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF");
  dv.setUint32(4, 36 + pcm.byteLength, true);
  str(8, "WAVE");
  str(12, "fmt ");
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, 1, true); // mono
  dv.setUint32(24, TARGET_RATE, true);
  dv.setUint32(28, TARGET_RATE * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  str(36, "data");
  dv.setUint32(40, pcm.byteLength, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: "audio/wav" });
}
