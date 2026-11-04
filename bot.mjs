// METANOIA — robot de YouTube
// 1) Lee los episodios de la base (la misma de la web), 2) baja el audio de SoundCloud/Mixcloud,
// 3) dibuja el video con el diseño del show (render.html de la web), 4) lo une con ffmpeg y 5) lo sube a YouTube en Privado.
// Lo que ya subió queda anotado en uploaded.json (así no repite).
import { chromium } from "playwright";
import { google } from "googleapis";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const SITE = (process.env.SITE || "https://mtn-radioshow-web.netlify.app").replace(/\/$/, "");
const SUPA = "https://eoirgezuhpbztyypcgfk.supabase.co";
const KEY = "sb_publishable_wVtFu79YSsm18QhKKcex-g_7vendG1W";          // clave pública de lectura (la misma de la web)
const COUNT = +process.env.COUNT || 10;
const ORDER = process.env.ORDER || "newest";                             // newest | oldest
const ONLY = String(process.env.ONLY || "").split(/[\s,]+/).filter(Boolean).map(Number);
const DRY = process.env.DRY_RUN === "1" || process.env.DRY_RUN === "true";
const PRIVACY = process.env.PRIVACY || "private";
const REQUIRE_TL = !/^(0|false|no)$/i.test(process.env.REQUIRE_TRACKLIST || "1");
const WORK = path.resolve("work"); fs.mkdirSync(WORK, { recursive: true });
const DONE_FILE = "uploaded.json";
const done = fs.existsSync(DONE_FILE) ? JSON.parse(fs.readFileSync(DONE_FILE, "utf8")) : [];
const saveDone = () => fs.writeFileSync(DONE_FILE, JSON.stringify(done, null, 2) + "\n");
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf8", maxBuffer: 1 << 26, ...opts });
  if (r.status !== 0) throw new Error(`${cmd} falló: ${(r.stderr || r.stdout || "").slice(-1500)}`);
  return r.stdout;
}
const probe = (f) => +run("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).trim();

// ---------- datos ----------
async function siteData() {
  if (process.env.DATA_FILE) return JSON.parse(fs.readFileSync(process.env.DATA_FILE, "utf8"));
  const r = await fetch(`${SUPA}/rest/v1/rpc/site_data`, { method: "POST", headers: { apikey: KEY, "Content-Type": "application/json" }, body: "{}" });
  if (!r.ok) throw new Error("No pude leer la base: " + r.status);
  return r.json();
}
const mediaOf = (e) => (e ? [e.audio && { kind: "mp3", url: e.audio }, e.soundcloud && { kind: "sc", url: e.soundcloud },
  e.mixcloud && { kind: "mc", urls: e.mixcloud.split(/\s+/).map((k) => "https://www.mixcloud.com" + k) }].filter(Boolean) : []);

function plan(DB) {
  const byId = new Map(DB.episodes.map((e) => [e.id, e]));
  const artists = new Map(DB.artists.map((a) => [a.id, a]));
  const doneIds = new Set(done.map((d) => d.id));
  const abs = (u) => (!u ? "" : /^https?:/.test(u) ? u : `${SITE}/${u.replace(/^\//, "")}`);
  const list = [];
  for (const ep of DB.episodes) {
    if (ep.type !== "episode" || !ep.n || ep.partOf || !ep.sets?.length || doneIds.has(ep.id)) continue;
    if (ONLY.length && !ONLY.includes(ep.n)) continue;
    // solo episodios con tracklist en todos sus sets (sin tracklist el video y la descripción quedan pobres)
    if (!ONLY.length && REQUIRE_TL && !ep.sets.slice(0, 2).every((s) => (s.tracklist || []).length)) continue;
    const sourced = ep.sets.some((s) => s.src);
    // partes de audio en orden: por set (episodios viejos) o el episodio entero
    const parts = sourced ? ep.sets.map((s) => ({ set: s, media: mediaOf(byId.get(s.src)) })) : [{ set: null, media: mediaOf(ep) }];
    if (parts.some((p) => !p.media.length)) continue;                       // falta audio de alguna parte
    // la Hora 1 es el primer set (en el orden en que salieron), la Hora 2 el segundo
    const g = ep.sets[0], r = ep.sets[1];
    const art = (s) => {
      if (!s) return null; const a = artists.get(s.artist) || { name: s.artist };
      return { name: a.name, photo: abs(a.photo) || abs(byId.get(s.src)?.cover) || abs(ep.cover), logo: abs(a.logo), city: a.city || "", country: a.country || "", genres: a.genres || [] };
    };
    list.push({ ep, parts, sourced, payload: { ep: ep.n, date: ep.date, time: "17HS AR", guest: art(g), resident: art(r),
      tracklists: { guestmix: g?.tracklist || [], resident: r?.tracklist || [] }, roles: [g.role, r?.role || "resident"], residentStart: r?.start || 0, freq: FREQ[ep.n % FREQ.length] } });
  }
  list.sort((a, b) => (ORDER === "oldest" ? a.ep.n - b.ep.n : b.ep.n - a.ep.n));
  return list.slice(0, COUNT);
}
// frecuencias del manual, rotando para que no se repita dos semanas seguidas (Cian queda para especiales)
const FREQ = ["#ADD709", "#6405D5", "#AF5D2D", "#D4BA07"];

// ---------- audio ----------
async function download(m, base) {
  if (process.env.LOCAL_AUDIO) { const f = path.join(process.env.LOCAL_AUDIO, path.basename(base) + ".mp3"); if (fs.existsSync(f)) return [f]; }
  if (m.kind === "mp3") {
    const r = await fetch(m.url); if (!r.ok) throw new Error("MP3 " + r.status);
    const f = base + ".mp3"; fs.writeFileSync(f, Buffer.from(await r.arrayBuffer())); return [f];
  }
  const urls = m.urls || [m.url];
  return urls.map((u, i) => {
    run("yt-dlp", ["-q", "--no-playlist", "-f", "bestaudio/best", "-o", `${base}-${i}.%(ext)s`, u]);
    return fs.readdirSync(WORK).map((x) => path.join(WORK, x)).find((x) => x.startsWith(`${base}-${i}.`));
  });
}
async function getAudio(item) {
  const groups = [];
  for (const [i, p] of item.parts.entries()) {
    let files = null, lastErr = null;
    for (const m of p.media) { try { files = await download(m, path.join(WORK, `ep${item.ep.n}-p${i}`)); break; } catch (e) { lastErr = e; } }
    if (!files) throw new Error(`No pude bajar el audio de la parte ${i + 1}: ${lastErr?.message}`);
    groups.push(files);
  }
  const all = groups.flat(), durs = all.map(probe);
  const firstGroup = groups[0].length;
  const hour2 = item.sourced && groups.length > 1 ? durs.slice(0, firstGroup).reduce((a, b) => a + b, 0) : item.payload.residentStart || 0;
  return { files: all, total: durs.reduce((a, b) => a + b, 0), hour2 };
}

// ---------- video ----------
let browser = null;
async function render(payload) {
  // Google Chrome (viene instalado en GitHub) trae el codificador H.264; el Chromium de Playwright a veces no
  if (!browser) {
    try { browser = await chromium.launch({ channel: "chrome", args: ["--autoplay-policy=no-user-gesture-required"] }); log("  (usando Google Chrome)"); }
    catch { browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] }); log("  (usando Chromium)"); }
  }
  const page = await browser.newPage(); page.setDefaultTimeout(0);
  page.on("pageerror", (e) => log("  [página]", e.message));
  await page.goto(`${SITE}/render.html?b=${Date.now()}`, { waitUntil: "load" });
  await page.waitForFunction(() => window.renderReady === true, null, { timeout: 60000 });
  const out = await page.evaluate((o) => window.renderEpisode(o), payload);
  await page.close();
  return out;
}
function buildList(clips, hour2, total, dir) {
  const lines = [], esc = (f) => `file '${f.replace(/'/g, "'\\''")}'`;
  const file = (c, i) => { const f = path.join(dir, `clip${i}-${c.kind}${c.hour}.mp4`); if (!fs.existsSync(f)) fs.writeFileSync(f, Buffer.from(c.b64, "base64")); return f; };
  let t = 0;
  const hours = [...new Set(clips.map((c) => c.hour))];
  for (const h of hours) {
    const end = h === hours[hours.length - 1] ? total + 2 : hour2;
    const intro = clips.findIndex((c) => c.hour === h && c.kind === "intro"), loop = clips.findIndex((c) => c.hour === h && c.kind === "loop");
    if (intro > -1 && t < end) { lines.push(esc(file(clips[intro], intro))); t += clips[intro].seconds; }
    const L = clips[loop].seconds, f = file(clips[loop], loop);
    while (end - t > 0.05) { const rest = end - t; lines.push(esc(f)); if (rest < L) { lines.push(`outpoint ${rest.toFixed(3)}`); t = end; } else t += L; }
  }
  const lf = path.join(dir, "list.txt"); fs.writeFileSync(lf, lines.join("\n") + "\n"); return lf;
}

// ---------- YouTube ----------
function youtube() {
  // se limpian espacios/saltos de línea que a veces quedan al pegar
  const clean = (v) => String(v || "").trim().replace(/^["']|["']$/g, "");
  const id = clean(process.env.YT_CLIENT_ID), secret = clean(process.env.YT_CLIENT_SECRET), token = clean(process.env.YT_REFRESH_TOKEN);
  if (!id || !secret || !token) throw new Error("Faltan los secretos YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN en GitHub.");
  const auth = new google.auth.OAuth2(id, secret); auth.setCredentials({ refresh_token: token });
  return google.youtube({ version: "v3", auth });
}
async function upload(yt, video, thumb, text) {
  const tags = []; let len = 0; for (const t of text.tags) { if (len + t.length + 3 > 480) break; tags.push(t); len += t.length + 3; }
  const r = await yt.videos.insert({
    part: ["snippet", "status"], notifySubscribers: false,
    requestBody: { snippet: { title: text.title.slice(0, 100), description: text.description.slice(0, 4900).replace(/[<>]/g, ""), tags, categoryId: "10", defaultLanguage: "es" },
      status: { privacyStatus: PRIVACY, selfDeclaredMadeForKids: false } },
    media: { body: fs.createReadStream(video) }
  });
  const id = r.data.id;
  try { await yt.thumbnails.set({ videoId: id, media: { mimeType: "image/jpeg", body: fs.createReadStream(thumb) } }); }
  catch (e) { log("  (miniatura no aplicada:", e.message, "— el canal tiene que estar verificado por teléfono)"); }
  if (process.env.PLAYLIST_ID) { try { await yt.playlistItems.insert({ part: ["snippet"], requestBody: { snippet: { playlistId: process.env.PLAYLIST_ID, resourceId: { kind: "youtube#video", videoId: id } } } }); } catch (e) { log("  (no se pudo agregar a la lista:", e.message, ")"); } }
  return id;
}

// ---------- principal ----------
const DB = await siteData();
const todo = plan(DB);
log(`Episodios para subir ahora: ${todo.map((x) => x.ep.n).join(", ") || "ninguno"}${DRY ? " (prueba: no sube a YouTube)" : ""}`);
const yt = DRY ? null : youtube();
if (yt) {   // probar el permiso antes de gastar tiempo armando videos
  try { const me = await yt.channels.list({ part: ["snippet"], mine: true }); log(`Canal de YouTube: ${me.data.items?.[0]?.snippet?.title || "(sin canal)"}`); }
  catch (e) {
    const m = String(e.message || e);
    log("ERROR con el permiso de YouTube: " + m);
    if (/invalid_client/.test(m)) log("→ El ID de cliente o el secreto no coinciden (YT_CLIENT_ID / YT_CLIENT_SECRET). Revisá que sean los del cliente “Robot YouTube Metanoia”, sin espacios.");
    if (/invalid_grant/.test(m)) log("→ El refresh token no sirve (venció o se sacó con otro cliente). Volvé a sacarlo en OAuth Playground con “Use your own OAuth credentials”.");
    process.exit(1);
  }
}
let ok = 0, fail = 0;
for (const item of todo) {
  const dir = path.join(WORK, `ep${item.ep.n}`); fs.mkdirSync(dir, { recursive: true });
  try {
    log(`Ep.${item.ep.n} · ${item.ep.title}`);
    const a = await getAudio(item);
    log(`  audio: ${a.files.length} archivo(s), ${Math.round(a.total / 60)} min, Hora 2 en ${Math.round(a.hour2 / 60)} min`);
    const payload = { ...item.payload, hour2Start: item.payload.resident ? a.hour2 : 0, plan: process.env.PLAN ? JSON.parse(process.env.PLAN) : undefined };
    const t0 = Date.now(); const R = await render(payload);
    log(`  dibujo listo en ${Math.round((Date.now() - t0) / 1000)} s (${R.clips.length} clips)`);
    const list = buildList(R.clips, payload.hour2Start, a.total, dir);
    const out = path.join(dir, `metanoia-ep${item.ep.n}.mp4`), thumb = path.join(dir, "thumb.jpg");
    fs.writeFileSync(thumb, Buffer.from(R.thumb, "base64"));
    const inputs = a.files.flatMap((f) => ["-i", f]);
    const amap = a.files.length > 1 ? ["-filter_complex", a.files.map((_, i) => `[${i + 1}:a]`).join("") + `concat=n=${a.files.length}:v=0:a=1[a]`, "-map", "[a]"] : ["-map", "1:a"];
    run("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list, ...inputs, "-map", "0:v", ...amap,
      "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-shortest", "-movflags", "+faststart", out]);
    log(`  video: ${Math.round(fs.statSync(out).size / 1e6)} MB, ${Math.round(probe(out) / 60)} min`);
    if (DRY) { log("  prueba OK (no se sube)"); if (process.env.KEEP) continue; }
    else {
      const id = await upload(yt, out, thumb, R.text);
      done.push({ id: item.ep.id, n: item.ep.n, youtube: id, title: R.text.title, at: new Date().toISOString() }); saveDone();
      log(`  subido: https://youtu.be/${id} (${PRIVACY})`);
    }
    ok++;
  } catch (e) {
    fail++; log(`  ERROR Ep.${item.ep.n}: ${e.message}`);
    if (/quota|rateLimit|uploadLimitExceeded/i.test(e.message)) { log("Se alcanzó el límite de YouTube por hoy; sigue mañana."); break; }
  } finally { if (!process.env.KEEP) fs.rmSync(dir, { recursive: true, force: true }); fs.readdirSync(WORK).filter((x) => x.startsWith(`ep${item.ep.n}-`)).forEach((x) => fs.rmSync(path.join(WORK, x), { force: true })); }
}
await browser?.close();
log(`Listo: ${ok} subidos, ${fail} con error. Total en YouTube: ${done.length}.`);
if (fail && !ok) process.exitCode = 1;
