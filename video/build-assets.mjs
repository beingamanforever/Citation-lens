// Inlines the icons, agent logos and fonts the animation uses, so index.html needs no node_modules.
import fs from "node:fs";
import path from "node:path";

import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const nm = path.join(root, "node_modules");
const lucide = (name) => fs.readFileSync(path.join(nm, "lucide-static/icons", name + ".svg"), "utf8");
const logo = (name) => fs.readFileSync(path.join(nm, "@lobehub/icons-static-svg/icons", name + ".svg"), "utf8");
const strip = (svg) => svg.replace(/<!--[\s\S]*?-->/g, "").replace(/\s+/g, " ").trim();

const ICONS = {};
for (const name of [
  "search", "target", "share-2", "badge-check", "chart-no-axes-column", "graduation-cap", "library",
  "file-text", "quote", "scroll-text", "git-fork", "sparkles", "link", "circle-check", "check", "x",
  "book-open", "clock", "layers", "telescope", "arrow-right", "microscope", "flask-conical", "zap",
  "mouse-pointer-2", "terminal", "circle-x", "timer", "list-ordered", "link-2", "file-search", "waypoints", "trending-up",
]) ICONS[name] = strip(lucide(name));
// Logos keep their brand colors; they are marks of OpenAI (Codex) and Anthropic (Claude Code).
ICONS["logo-claude-code"] = strip(logo("claudecode-color"));
ICONS["logo-codex"] = strip(logo("codex-color"));
fs.writeFileSync(path.join(root, "icons.js"), "window.ICONS = " + JSON.stringify(ICONS) + ";\n");

const font = (pkg, file, family, weight) => {
  const data = fs.readFileSync(path.join(nm, "@fontsource", pkg, "files", file)).toString("base64");
  return `@font-face{font-family:"${family}";font-weight:${weight};font-style:normal;font-display:block;src:url(data:font/woff2;base64,${data}) format("woff2");}`;
};
const css = [
  ...[400, 500, 600].map((w) => font("inter-tight", `inter-tight-latin-${w}-normal.woff2`, "Inter Tight", w)),
  ...[400, 500].map((w) => font("inter", `inter-latin-${w}-normal.woff2`, "Inter", w)),
].join("\n");
fs.writeFileSync(path.join(root, "fonts.css"), css + "\n");
console.log("icons:", Object.keys(ICONS).length, "fonts.css:", Math.round(css.length / 1024), "KB");
