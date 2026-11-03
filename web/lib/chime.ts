/** Short two-tone "your turn to talk" chime via WebAudio (no assets). */
let ctx: AudioContext | null = null;

export function playChime() {
  try {
    ctx ??= new AudioContext();
    const t = ctx.currentTime;
    [880, 1320].forEach((f, i) => {
      const o = ctx!.createOscillator();
      const g = ctx!.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + i * 0.11);
      g.gain.exponentialRampToValueAtTime(0.25, t + i * 0.11 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.11 + 0.1);
      o.connect(g).connect(ctx!.destination);
      o.start(t + i * 0.11);
      o.stop(t + i * 0.11 + 0.12);
    });
  } catch {
    /* audio blocked until the first user gesture; the visual indicator still shows */
  }
}
