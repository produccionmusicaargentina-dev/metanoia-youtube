# METANOIA — Prueba: ¿en qué minuto empieza cada tema del tracklist?
# Baja el audio de cada set, lo "escucha" cada STEP segundos con el reconocedor de Shazam (no oficial, shazamio)
# y cruza lo reconocido con el tracklist que ya está en la base. Los temas que no se reconocen se estiman
# entre el anterior y el siguiente. Resultado: tracktimes/ep<N>.json (no toca la base ni la web).
import asyncio, json, os, re, subprocess, sys, tempfile, time, unicodedata, urllib.request
from rapidfuzz import fuzz
from shazamio import Shazam

SUPA = "https://eoirgezuhpbztyypcgfk.supabase.co"
KEY = "sb_publishable_wVtFu79YSsm18QhKKcex-g_7vendG1W"   # clave pública de lectura (la misma de la web)
ONLY = [int(x) for x in re.findall(r"\d+", os.environ.get("ONLY", ""))]
COUNT = int(os.environ.get("COUNT", "3"))
STEP = int(os.environ.get("STEP", "20"))      # cada cuántos segundos escucha
CLIP = 12                                     # cuántos segundos escucha cada vez
WORK = tempfile.mkdtemp()

def site_data():
    req = urllib.request.Request(f"{SUPA}/rest/v1/rpc/site_data", data=b"{}", method="POST",
                                 headers={"apikey": KEY, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))

def media_of(e):
    if not e: return []
    m = []
    if e.get("audio"): m.append(("mp3", e["audio"]))
    if e.get("soundcloud"): m.append(("sc", e["soundcloud"]))
    if e.get("mixcloud"): m.append(("mc", "https://www.mixcloud.com" + e["mixcloud"].split()[0]))
    return m

def download(media, base):
    for kind, url in media:
        try:
            out = f"{base}.%(ext)s"
            subprocess.run(["yt-dlp", "-q", "--no-playlist", "-f", "bestaudio/best", "-o", out, url], check=True, timeout=1800)
            f = next(os.path.join(WORK, x) for x in os.listdir(WORK) if x.startswith(os.path.basename(base) + "."))
            wav = base + ".wav"   # mono 16 kHz: más liviano para cortar
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f, "-ac", "1", "-ar", "16000", wav], check=True)
            os.remove(f); return wav
        except Exception as e:
            print("  no pude bajar", url, e)
    return None

def duration(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True).stdout)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(original mix|extended mix|extended|remix|edit|mix|feat\.?|ft\.?|vs\.?|presents|pres\.?)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

async def recognize(sh, f):
    for attempt in range(4):
        try:
            fn = getattr(sh, "recognize", None) or sh.recognize_song
            out = await fn(f)
            t = (out or {}).get("track")
            return (t.get("subtitle", ""), t.get("title", "")) if t else None
        except Exception as e:
            wait = 5 * (attempt + 1); print(f"    reintento en {wait}s ({e})"); await asyncio.sleep(wait)
    return None

async def listen(sh, wav, start=0.0, end=None):
    """Lista de (segundo, artista, tema) reconocidos en [start, end)."""
    end = end or duration(wav); hits = []; t = start + 5
    clip = os.path.join(WORK, "clip.wav")
    while t + CLIP <= end:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-t", str(CLIP), "-i", wav, clip], check=True)
        r = await recognize(sh, clip)
        if r: hits.append((round(t - start), r[0], r[1]))
        print(f"    {int(t//60):02d}:{int(t%60):02d}  {' – '.join(r) if r else '—'}", flush=True)
        t += STEP; await asyncio.sleep(1.2)
    return hits

def align(tracklist, hits, length):
    """Para cada tema del tracklist: segundo de inicio (reconocido o estimado)."""
    n = len(tracklist); keys = [norm(x) for x in tracklist]
    seq = []   # (segundo, índice del tracklist)
    for sec, artist, title in hits:
        cand = norm(f"{artist} {title}"); tt = norm(title)
        best, score = None, 0
        for i, k in enumerate(keys):
            sc = max(fuzz.token_set_ratio(cand, k), fuzz.partial_ratio(tt, k) if len(tt) > 3 else 0)
            if sc > score: best, score = i, sc
        if score >= 72: seq.append((sec, best))
    # quedarse con la secuencia más larga en orden (descarta reconocimientos fuera de lugar)
    m = len(seq); L = [1] * m; P = [-1] * m
    for i in range(m):
        for j in range(i):
            if seq[j][1] <= seq[i][1] and L[j] + 1 > L[i]: L[i], P[i] = L[j] + 1, j
    keep = []
    if m:
        i = max(range(m), key=lambda k: L[k])
        while i != -1: keep.append(seq[i]); i = P[i]
        keep.reverse()
    first = {}
    for sec, i in keep: first.setdefault(i, sec)
    # primer tema siempre en 0; los demás sin dato se reparten entre los conocidos
    known = sorted(first.items()); starts = [None] * n; src = ["estimado"] * n
    for i, s in known: starts[i] = max(0, s - STEP // 2); src[i] = "shazam"   # empezó entre la muestra anterior y esta
    if starts[0] is None or src[0] == "estimado": starts[0] = 0
    anchors = [(i, starts[i]) for i in range(n) if starts[i] is not None]
    if anchors[-1][0] != n - 1: anchors.append((n, length))
    else: anchors.append((n, length))
    for (a, sa), (b, sb) in zip(anchors, anchors[1:]):
        for k in range(a + 1, min(b, n)):
            starts[k] = round(sa + (sb - sa) * (k - a) / (b - a))
    return [{"i": i, "track": tracklist[i], "start": int(starts[i]), "mmss": f"{int(starts[i]//60):02d}:{int(starts[i]%60):02d}", "fuente": src[i]} for i in range(n)]

async def main():
    DB = site_data(); by_id = {e["id"]: e for e in DB["episodes"]}; arts = {a["id"]: a for a in DB["artists"]}
    eps = [e for e in DB["episodes"] if e.get("type") == "episode" and e.get("n") and not e.get("partOf") and e.get("sets")]
    if ONLY: eps = [e for e in eps if e["n"] in ONLY]
    else: eps = [e for e in eps if all((s.get("tracklist") or []) for s in e["sets"][:2])]
    eps.sort(key=lambda e: -e["n"]); eps = eps[: (len(ONLY) or COUNT)]
    sh = Shazam(); os.makedirs("tracktimes", exist_ok=True)
    for ep in eps:
        print(f"\n=== Episodio {ep['n']} ===", flush=True); t0 = time.time()
        sets = ep["sets"]; sourced = any(s.get("src") for s in sets); res = {"ep": ep["n"], "step": STEP, "sets": []}
        if sourced:
            parts = [(s, download(media_of(by_id.get(s.get("src"))), os.path.join(WORK, f"ep{ep['n']}-s{i}"))) for i, s in enumerate(sets)]
        else:
            whole = download(media_of(ep), os.path.join(WORK, f"ep{ep['n']}"))
            parts = [(s, whole) for s in sets]
        for i, (s, wav) in enumerate(parts):
            name = arts.get(s.get("artist"), {}).get("name", s.get("artist"))
            tl = s.get("tracklist") or []
            if not wav or not tl: res["sets"].append({"artist": name, "error": "sin audio o sin tracklist"}); continue
            total = duration(wav)
            if sourced: a, b = 0.0, total
            else:   # episodio entero: la hora 2 arranca en `start` del segundo set (o a la mitad)
                h2 = next((x.get("start") for x in sets[1:2] if x.get("start")), None) or total / 2
                a, b = (0.0, h2) if i == 0 else (h2, total)
            print(f"  Set {i+1}: {name} ({len(tl)} temas, {int((b-a)//60)} min)", flush=True)
            hits = await listen(sh, wav, a, b)
            rows = align(tl, hits, int(b - a))
            ok = sum(r["fuente"] == "shazam" for r in rows)
            res["sets"].append({"artist": name, "offset": int(a), "length": int(b - a), "reconocidos": ok, "total": len(tl), "temas": rows,
                                "escuchado": [{"seg": h[0], "artista": h[1], "tema": h[2]} for h in hits]})
            print(f"  → {ok}/{len(tl)} temas ubicados por reconocimiento", flush=True)
        res["minutos_proceso"] = round((time.time() - t0) / 60, 1)
        json.dump(res, open(f"tracktimes/ep{ep['n']}.json", "w"), ensure_ascii=False, indent=1)
        for f in os.listdir(WORK): os.remove(os.path.join(WORK, f))

asyncio.run(main())
