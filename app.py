#!/usr/bin/env python3
"""Ara sona: mostra el que sona al compte de Spotify (només lectura).

Dos modes:
  python app.py auth    -> autoritza el compte (només la primera vegada)
  python app.py         -> engega la web (és el que fa servir el contenidor)

Només fa servir la biblioteca estàndard de Python.
"""
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))

# Es poden canviar només per fer proves amb un Spotify simulat.
AUTH_URL = os.environ.get("SPOTIFY_AUTH_URL", "https://accounts.spotify.com/authorize")
TOKEN_URL = os.environ.get("SPOTIFY_TOKEN_URL", "https://accounts.spotify.com/api/token")
API_URL = os.environ.get("SPOTIFY_API_URL", "https://api.spotify.com/v1/me/player")

SCOPE = "user-read-playback-state"
REFRESH_FILE = os.path.join(DATA_DIR, "refresh_token")
CACHE_SECONDS = 5  # no preguntem a Spotify més d'un cop cada 5 s, tingui els espectadors que tingui


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- tokens

def token_request(params):
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(params).encode(),
        headers={
            "Authorization": "Basic " + basic,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def save_refresh_token(token):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = REFRESH_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, REFRESH_FILE)


def run_auth():
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit("Falten SPOTIFY_CLIENT_ID i SPOTIFY_CLIENT_SECRET al fitxer d'entorn.")
    sys.stdout.reconfigure(line_buffering=True)  # que el text surti abans de demanar l'adreça
    state = base64.urlsafe_b64encode(os.urandom(9)).decode()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
    })
    print()
    print("1) Obre aquesta adreça al navegador i accepta:")
    print()
    print(url)
    print()
    print("2) Després d'acceptar, el navegador mostrarà un error de pàgina (és normal).")
    print("   Copia l'adreça sencera de la barra del navegador i enganxa-la aquí.")
    print()
    pasted = sys.argv[2] if len(sys.argv) > 2 else input("Adreça: ")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query)
    if q.get("state", [""])[0] != state:
        sys.exit("L'adreça no correspon a aquesta autorització. Torna-ho a provar des del principi.")
    if "code" not in q:
        sys.exit("Spotify no ha donat cap codi: " + q.get("error", ["error desconegut"])[0])
    try:
        data = token_request({
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": REDIRECT_URI,
        })
    except urllib.error.HTTPError as e:
        sys.exit(f"Spotify ha rebutjat el codi ({e.code}): {e.read().decode(errors='replace')}")
    save_refresh_token(data["refresh_token"])
    print()
    print("Fet! Autorització desada a " + REFRESH_FILE)


_access = {"token": None, "exp": 0.0}


def access_token():
    if _access["token"] and time.time() < _access["exp"] - 30:
        return _access["token"]
    try:
        with open(REFRESH_FILE) as f:
            refresh = f.read().strip()
    except FileNotFoundError:
        raise RuntimeError("Falta l'autorització. Executa: python app.py auth")
    data = token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    _access["token"] = data["access_token"]
    _access["exp"] = time.time() + data.get("expires_in", 3600)
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        save_refresh_token(new_refresh)
    return _access["token"]


# ---------------------------------------------------------------- Spotify

_lock = threading.Lock()
_cache = {"t": 0.0, "data": None}
_backoff_until = 0.0


def pick_image(images):
    if not images:
        return None
    # Les imatges venen de la més gran a la més petita; la del mig va bé.
    return images[1]["url"] if len(images) > 1 else images[0]["url"]


def normalize(j):
    """Converteix la resposta de Spotify en alguna cosa senzilla."""
    item = j.get("item")
    if not item:
        return {"state": "idle"}
    if item.get("type") == "episode":
        show = item.get("show") or {}
        artist = show.get("publisher") or ""
        album = show.get("name") or ""
        cover = pick_image(item.get("images") or show.get("images"))
    else:
        artist = ", ".join(a.get("name", "") for a in item.get("artists", []))
        album_obj = item.get("album") or {}
        album = album_obj.get("name") or ""
        cover = pick_image(album_obj.get("images"))
    return {
        "state": "playing" if j.get("is_playing") else "paused",
        "title": item.get("name") or "",
        "artist": artist,
        "album": album,
        "cover": cover,
        "device": (j.get("device") or {}).get("name") or "",
        "progress_ms": j.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
    }


def fetch_now():
    global _backoff_until
    if time.time() < _backoff_until:
        raise RuntimeError("Spotify demana esperar una estona; es tornarà a provar sol.")
    req = urllib.request.Request(
        API_URL + "?additional_types=track,episode",
        headers={"Authorization": "Bearer " + access_token()},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _access["token"] = None  # es renovarà a la propera
        if e.code == 429:
            _backoff_until = time.time() + int(e.headers.get("Retry-After", "30"))
        raise RuntimeError(f"Spotify ha respost {e.code}")
    if status == 204 or not body:
        return {"state": "idle"}
    return normalize(json.loads(body))


def now_playing():
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["t"] < CACHE_SECONDS:
            return _cache["data"]
        try:
            data = fetch_now()
        except Exception as e:  # noqa: BLE001 - volem mostrar l'error a la pàgina
            log("Error: " + str(e))
            data = {"state": "error", "error": str(e)}
        _cache["t"], _cache["data"] = time.time(), data
        return data


# ---------------------------------------------------------------- web

PAGE = """<!doctype html>
<html lang="ca">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ara sona</title>
<style>
  :root { color-scheme: dark; --fons:#121212; --text:#f2f2f2; --fluix:#9a9a9a; --barra:#333; --accent:#1db954; }
  @media (prefers-color-scheme: dark) { :root { --fons:#121212; --text:#f2f2f2; --fluix:#9a9a9a; --barra:#333; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:var(--fons); color:var(--text); font-family: system-ui, sans-serif; padding:16px; }
  .targeta { width:min(92vw, 520px); text-align:center; }
  .portada { width:min(100%, 52vh); margin:0 auto; aspect-ratio:1; border-radius:14px;
             background:var(--barra) center/cover no-repeat; box-shadow:0 10px 30px #0004; }
  .etiqueta { margin:22px 0 4px; font-size:.85rem; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); }
  .titol { font-size:clamp(1.4rem, 5vw, 2.1rem); font-weight:700; line-height:1.2; margin:0; overflow-wrap:anywhere; }
  .artista { font-size:clamp(1.05rem, 3.5vw, 1.4rem); color:var(--fluix); margin:6px 0 0; overflow-wrap:anywhere; }
  .album, .dispositiu { color:var(--fluix); font-size:.95rem; margin:4px 0 0; }
  .progres { height:5px; background:var(--barra); border-radius:3px; margin:20px 0 0; overflow:hidden; }
  .progres > div { height:100%; width:0; background:var(--accent); transition:width .9s linear; }
  .buit { font-size:1.4rem; color:var(--fluix); }
  [hidden] { display:none !important; }

  /* Poc espai en alçada (per exemple, dins d'un quadre de Homepage): portada a l'esquerra, text a la dreta */
  @media (max-height: 380px) {
    body { padding:10px; min-height:100vh; }
    .targeta { width:100%; }
    #reproduint { display:grid; grid-template-columns:min(42%, 80vh) 1fr; gap:14px; align-items:center; text-align:left; }
    .portada { width:100%; margin:0; border-radius:10px; box-shadow:0 4px 14px #0004; }
    .etiqueta { margin:0 0 3px; font-size:.7rem; }
    .titol { font-size:clamp(1rem, 7.5vh, 1.5rem); }
    .artista { font-size:clamp(.85rem, 5.5vh, 1.1rem); margin-top:3px; }
    .album, .dispositiu { font-size:.8rem; margin-top:2px; }
    .progres { margin-top:10px; }
  }
  @media (max-height: 230px) { .album { display:none; } }
</style>
</head>
<body>
<main class="targeta">
  <div id="reproduint" hidden>
    <div class="portada" id="portada"></div>
    <div class="info">
      <p class="etiqueta" id="etiqueta">Ara sona</p>
      <h1 class="titol" id="titol"></h1>
      <p class="artista" id="artista"></p>
      <p class="album" id="album"></p>
      <div class="progres"><div id="barra"></div></div>
      <p class="dispositiu" id="dispositiu"></p>
    </div>
  </div>
  <p class="buit" id="buit">Carregant...</p>
</main>
<script>
const $ = (id) => document.getElementById(id);
let dades = null, rebutA = 0;

async function actualitza() {
  try {
    const r = await fetch("api/now", { cache: "no-store" });
    dades = await r.json();
    rebutA = Date.now();
  } catch (e) {
    dades = { state: "error", error: "No es pot arribar al servidor." };
  }
  pinta();
}

function pinta() {
  if (!dades) return;
  const sona = dades.state === "playing" || dades.state === "paused";
  $("reproduint").hidden = !sona;
  $("buit").hidden = sona;
  if (!sona) {
    $("buit").textContent = dades.state === "error" ? "Error: " + dades.error : "Ara no sona res";
    document.title = "Ara sona";
    return;
  }
  $("etiqueta").textContent = dades.state === "playing" ? "Ara sona" : "En pausa";
  $("titol").textContent = dades.title;
  $("artista").textContent = dades.artist;
  $("album").textContent = dades.album;
  $("dispositiu").textContent = dades.device ? "a " + dades.device : "";
  $("portada").style.backgroundImage = dades.cover ? 'url("' + dades.cover + '")' : "none";
  let ms = dades.progress_ms;
  if (dades.state === "playing") ms += Date.now() - rebutA;
  const pct = dades.duration_ms ? Math.min(100, (ms / dades.duration_ms) * 100) : 0;
  $("barra").style.width = pct + "%";
  document.title = dades.title + " - " + dades.artist;
}

actualitza();
setInterval(actualitza, 5000);
setInterval(pinta, 1000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/now":
            body = json.dumps(now_playing(), ensure_ascii=False).encode()
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"no trobat", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # silenciem el registre de cada petició
        pass


def run_server():
    if not os.path.exists(REFRESH_FILE):
        log("AVÍS: encara no hi ha autorització. Executa: python app.py auth")
    log(f"Ara sona escoltant al port {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        run_auth()
    else:
        run_server()
