import fs from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

/** KEY=VALUE from ../vario.conf (the backend's config file; pallas.conf before the rename); the environment overrides it. */
function conf(key: string): string | undefined {
  if (process.env[key] !== undefined) return process.env[key];
  try {
    const file = ["vario.conf", "pallas.conf"].map((n) => path.join(__dirname, "..", n)).find((f) => fs.existsSync(f));
    if (!file) return undefined;
    const text = fs.readFileSync(file, "utf8");
    const m = text.match(new RegExp(`^\\s*${key}\\s*=\\s*([^#\\s]*)`, "m"));
    return m?.[1];
  } catch {
    return undefined;
  }
}

// Built as static files (`next build` -> out/) that the Python backend serves on its own port, so an installed
// Vario is one process and needs no Node at runtime. `next dev` (make dev) still runs on :3000 against :8787.
export default function config(phase: string): NextConfig {
  const dev = phase === PHASE_DEVELOPMENT_SERVER;
  return {
    output: "export",
    env: {
      // the API: same origin when served by the backend; the dev server talks to :8787
      NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? (dev ? "http://127.0.0.1:8787" : ""),
      // aviation wording for state labels on screen; read at build/dev start
      NEXT_PUBLIC_FLIGHT_SPEAK: conf("FLIGHT_SPEAK") === "on" ? "on" : "off",
    },
    turbopack: {
      root: path.join(__dirname),
      rules: {
        "*.css": {
          loaders: ["@tailwindcss/turbopack"],
          as: "*.css",
        },
      },
    },
  };
}
