// Renders video/index.html to an MP4, one exact frame at a time (no screen recording).
//   node render.mjs                       -> citation-lens.mp4 (1920x1080, 60 fps, narration.wav mixed in)
//   node render.mjs --still 12.5 a.png    -> one frame at t = 12.5 s
//   node render.mjs --fps 30 --out x.mp4  -> other frame rate or file
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import puppeteer from "puppeteer-core";
import ffmpegPath from "ffmpeg-static";

const root = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const fps = Number(opt("--fps", 60));
const audio = opt("--audio", fs.existsSync(path.join(root, "narration.wav")) ? "narration.wav" : "");
const chrome = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
if (!fs.existsSync(chrome)) throw new Error("Chrome not found; set CHROME=/path/to/chrome");

const browser = await puppeteer.launch({ executablePath: chrome, headless: true, args: ["--hide-scrollbars", "--force-device-scale-factor=1"] });
const page = await browser.newPage();
await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.join(root, "index.html")).href + "?render");
await page.waitForFunction("window.__ready === true", { timeout: 30000 });

if (args.includes("--still")) {
  const t = Number(opt("--still"));
  await page.evaluate((x) => window.__setTime(x), t);
  await page.screenshot({ path: args[args.indexOf("--still") + 2], type: "png" });
  await browser.close();
  process.exit(0);
}

const duration = await page.evaluate(() => window.__duration);
const out = path.resolve(root, opt("--out", "citation-lens.mp4"));
const total = Math.round(duration * fps);
const withAudio = audio ? ["-i", path.resolve(root, audio), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aformat=sample_rates=48000:channel_layouts=mono", "-c:a", "aac", "-b:a", "192k", "-shortest"] : [];
const ff = spawn(ffmpegPath, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-i", "-", ...withAudio,
  "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
const done = new Promise((resolve, reject) => { ff.on("close", (code) => (code === 0 ? resolve() : reject(new Error("ffmpeg exited " + code)))); });
const started = Date.now();
for (let i = 0; i < total; i++) {
  await page.evaluate((t) => window.__setTime(t), i / fps);
  const frame = await page.screenshot({ type: "jpeg", quality: 96 });
  if (!ff.stdin.write(frame)) await new Promise((r) => ff.stdin.once("drain", r));
  if (i % 120 === 0) console.log(`frame ${i}/${total}  ${(((Date.now() - started) / 1000) | 0)}s`);
}
ff.stdin.end();
await done;
await browser.close();
console.log("wrote", out, ((fs.statSync(out).size / 1e6).toFixed(1)) + " MB");
