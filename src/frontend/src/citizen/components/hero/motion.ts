export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a: number, b: number, p: number) => a + (b - a) * p;
export const prog = (t: number, a: number, d: number) => clamp((t - a) / d);
export const eo = (p: number) => 1 - Math.pow(1 - p, 3);
export const eio = (p: number) => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
export function spring(t: number, t0: number, w = 20, z = .84) {
  const x = t - t0;
  if (x <= 0) return 0;
  const wd = w * Math.sqrt(1 - z * z);
  return 1 - Math.exp(-z * w * x) * (Math.cos(wd * x) + (z * w / wd) * Math.sin(wd * x));
}
export const pulse = (t: number, t0: number) => spring(t, t0, 34, .7) - spring(t, t0 + .11, 34, .7);
export const mix = (a: string, b: string, p: number) => `color-mix(in srgb, ${b} ${clamp(p) * 100}%, ${a})`;
