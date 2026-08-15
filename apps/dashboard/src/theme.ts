// Avatar palette + shape system — the one place saturated colour lives (Grok
// Bot's chrome is otherwise monochrome). Colours and blob shapes are sampled
// from the published avatars; see docs/00-research-report.md.

export const AVATAR_COLORS = [
  "#8b5cf6", // purple
  "#3b82f6", // blue
  "#2fbf5f", // green
  "#17a398", // teal
  "#3ecf8e", // mint
  "#f97316", // orange
  "#f5c542", // amber
  "#e9174b", // crimson
  "#ee5a9c", // pink
  "#6069f0", // indigo
  "#7a5548", // brown
  "#4a4a52", // slate
] as const;

// Organic blob outlines (viewBox 0..100). Each bot gets a stable shape derived
// from its id, filled with its colour, with two white "eyes" overlaid.
export const BLOB_SHAPES: string[] = [
  // circle
  "M50 6a44 44 0 1 0 .001 0Z",
  // droplet
  "M50 6C64 26 82 42 82 60a32 32 0 1 1-64 0C18 42 36 26 50 6Z",
  // rounded square
  "M28 8h44a20 20 0 0 1 20 20v44a20 20 0 0 1-20 20H28A20 20 0 0 1 8 72V28A20 20 0 0 1 28 8Z",
  // egg / oval
  "M50 8c20 0 34 20 34 42s-14 42-34 42S16 72 16 50 30 8 50 8Z",
  // hexagon (rounded)
  "M50 6 86 28v44L50 94 14 72V28L50 6Z",
  // capsule
  "M32 14h36a26 26 0 0 1 0 52H32a26 26 0 0 1 0-52Z",
  // cloud
  "M30 66a20 20 0 0 1-2-40 24 24 0 0 1 46-4 18 18 0 0 1 2 44Z",
  // soft triangle
  "M50 10 90 82a10 10 0 0 1-9 14H19a10 10 0 0 1-9-14Z",
];

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function shapeForId(id: string): string {
  return BLOB_SHAPES[hash(id) % BLOB_SHAPES.length];
}

export function colorForIndex(i: number): string {
  return AVATAR_COLORS[i % AVATAR_COLORS.length];
}

// Suggest a fresh colour that isn't already used, so a new roster stays varied.
export function suggestColor(used: string[]): string {
  const free = AVATAR_COLORS.find((c) => !used.includes(c));
  return free ?? AVATAR_COLORS[used.length % AVATAR_COLORS.length];
}

const THEME_KEY = "recons-theme";
export type ThemeMode = "light" | "dark";

export function loadTheme(): ThemeMode {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.classList.toggle("dark", mode === "dark");
  localStorage.setItem(THEME_KEY, mode);
}
