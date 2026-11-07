# METANOIA — ¿en qué minuto empieza cada tema del tracklist?
# Baja el audio del episodio, lo "escucha" cada STEP segundos con el reconocedor de Shazam (no oficial, shazamio)
# y cruza lo reconocido con el tracklist que ya está en la base. Los temas que no se reconocen se estiman
# entre el anterior y el siguiente. En los episodios de dos horas grabados de corrido, además encuentra
# dónde empieza el segundo set (el cambio de artista), en vez de suponer que es a la hora justa.
#
# Deja el resultado en tracktimes/all.json (y el detalle en tracktimes/ep<N>.json). La base lo trae sola cada hora
# (función tt_import en Supabase) y lo carga en sets.times / sets.start_s de los sets que todavía no tienen minutos.
import asyncio, json, os, re, subprocess, tempfile, time, unicodedata, urllib.request
from rapidfuzz import fuzz

SUPA = "https://eoirgezuhpbztyypcgfk.supabase.co"
KEY = "sb_publishable_wVtFu79YSsm18QhKKcex-g_7vendG1W"   # clave pública (la misma de la web)
ONLY = [int(x) for x in re.findall(r"\d+", os.environ.get("ONLY", ""))]
COUNT = int(os.environ.get("COUNT", "2"))
STEP = int(os.environ.get("STEP", "40"))      # cada cuántos segundos escucha
CLIP = 12                                     # cuántos segundos escucha cada vez
REDO = os.environ.get("REDO") == "1"          # volver a calcular aunque ya tenga minutos
WORK = tempfile.mkdtemp()

def rpc(name, body):
    req = urllib.request.Request(f"{SUPA}/rest/v1/rpc/{name}", data=json.dumps(body).encode(), method="POST",
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
            f = next(os.path.join(WORK, x) for x in os.listdir(WORK) if x.startswith(os.path.basename(base) + ".") and not x.endswith(".wav"))
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
    s = re.sub(r"\[[^\]]*\]", " ", s)   # sello discográfico entre corchetes
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

async def listen(sh, wav, end):
    """Lista de (segundo, artista, tema) reconocidos en todo el audio."""
    hits = []; t = 5.0; clip = os.path.join(WORK, "clip.wav")
    while t + CLIP <= end:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-t", str(CLIP), "-i", wav, clip], check=True)
        r = await recognize(sh, clip)
        if r: hits.append((round(t), r[0], r[1]))
        print(f"    {int(t//60):02d}:{int(t%60):02d}  {' – '.join(r) if r else '—'}", flush=True)
        t += STEP; await asyncio.sleep(1.2)
    return hits

def align(tracks, hits, length, step):
    """tracks: lista de (set, texto) en el orden del episodio. Devuelve (inicio en segundos, reconocido?) por tema."""
    n = len(tracks); keys = [norm(x) for _, x in tracks]
    seq = []   # (segundo, índice)
    for sec, artist, title in hits:
        cand = norm(f"{artist} {title}"); tt = norm(title)
        best, score = None, 0
        for i, k in enumerate(keys):
            sc = max(fuzz.token_set_ratio(cand, k), fuzz.partial_ratio(tt, k) if len(tt) > 3 else 0)
            if sc > score: best, score = i, sc
        if score >= 72: seq.append((sec, best))
    # la secuencia más larga en orden (descarta reconocimientos fuera de lugar)
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
    starts = [None] * n; ok = [False] * n
    for i, s in first.items(): starts[i] = max(0, s - step // 2); ok[i] = True   # empezó entre la muestra anterior y esta
    starts[0] = 0
    anchors = [(i, starts[i]) for i in range(n) if starts[i] is not None] + [(n, length)]
    for (a, sa), (b, sb) in zip(anchors, anchors[1:]):
        for k in range(a + 1, min(b, n)):
            starts[k] = round(sa + (sb - sa) * (k - a) / (b - a))
    return [(int(starts[i]), ok[i]) for i in range(n)]

def solve(sets, hits, length, step):
    """Episodio de corrido: alinea todos los tracklists juntos. Devuelve por set (inicio del set, [minutos relativos], reconocidos)."""
    tracks = [(si, x) for si, s in enumerate(sets) for x in (s.get("tracklist") or [])]
    res = align(tracks, hits, length, step); out = []; k = 0
    for si, s in enumerate(sets):
        n = len(s.get("tracklist") or [])
        if not n: out.append(None); continue
        part = res[k:k + n]; k += n
        st = 0 if si == 0 else part[0][0]
        out.append({"start": st, "times": [t - st for t, _ in part], "ok": sum(o for _, o in part), "n": n})
    return out

ALL = "tracktimes/all.json"
def save(ep_id, pos, times, start):
    os.makedirs("tracktimes", exist_ok=True)
    d = json.load(open(ALL)) if os.path.exists(ALL) else {}
    d.setdefault(ep_id, {})[str(pos)] = {"times": times, **({"start": start} if start is not None else {})}
    json.dump(d, open(ALL, "w"), ensure_ascii=False, indent=0)
    return "anotado"

async def main():
    from shazamio import Shazam
    DB = rpc("site_data", {}); by_id = {e["id"]: e for e in DB["episodes"]}; arts = {a["id"]: a for a in DB["artists"]}
    eps = [e for e in DB["episodes"] if e.get("type") == "episode" and e.get("n") and not e.get("partOf") and e.get("sets")]
    if ONLY: eps = [e for e in eps if e["n"] in ONLY]
    else:
        eps = [e for e in eps if any(s.get("tracklist") for s in e["sets"])]
        if not REDO: eps = [e for e in eps if any(s.get("tracklist") and not s.get("times") for s in e["sets"])]
    eps.sort(key=lambda e: -e["n"]); eps = eps[: (len(ONLY) or COUNT)]
    print(f"Episodios a escuchar: {[e['n'] for e in eps]}", flush=True)
    sh = Shazam(); os.makedirs("tracktimes", exist_ok=True)
    for ep in eps:
        print(f"\n=== Episodio {ep['n']} ===", flush=True); t0 = time.time()
        sets = ep["sets"]; res = {"ep": ep["n"], "step": STEP, "sets": []}
        if any(s.get("src") for s in sets):
            # sets sueltos: cada uno con su propio audio
            for i, s in enumerate(sets):
                if not s.get("tracklist"): continue
                wav = download(media_of(by_id.get(s.get("src"))), os.path.join(WORK, f"ep{ep['n']}-s{i}"))
                if not wav: continue
                length = int(duration(wav)); hits = await listen(sh, wav, length)
                r = solve([s], hits, length, STEP)[0]
                print(f"  Set {i+1}: {r['ok']}/{r['n']} reconocidos → {save(ep['id'], i, r['times'], None)}", flush=True)
                res["sets"].append({"artist": arts.get(s["artist"], {}).get("name"), **r, "escuchado": hits})
        else:
            wav = download(media_of(ep), os.path.join(WORK, f"ep{ep['n']}"))
            if not wav: continue
            length = int(duration(wav)); hits = await listen(sh, wav, length)
            for i, (s, r) in enumerate(zip(sets, solve(sets, hits, length, STEP))):
                if not r: continue
                st = r["start"] if i > 0 and r["ok"] else None   # solo corrijo el inicio del set si lo encontró
                print(f"  Set {i+1}: empieza {r['start']//60}:{r['start']%60:02d} · {r['ok']}/{r['n']} reconocidos → {save(ep['id'], i, r['times'], st)}", flush=True)
                res["sets"].append({"artist": arts.get(s["artist"], {}).get("name"), **r})
            res["escuchado"] = hits
        res["minutos_proceso"] = round((time.time() - t0) / 60, 1)
        json.dump(res, open(f"tracktimes/ep{ep['n']}.json", "w"), ensure_ascii=False, indent=1)
        for f in os.listdir(WORK): os.remove(os.path.join(WORK, f))

if __name__ == "__main__":
    asyncio.run(main())
