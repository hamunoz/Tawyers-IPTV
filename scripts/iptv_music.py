#!/usr/bin/env python3
"""
iptv_music.py — Saca de una lista IPTV (.m3u/.m3u8) los canales de una
categoría (por defecto "music") y comprueba cuáles siguen vivos.

Uso:
  python3 scripts/iptv_music.py <URL o archivo.m3u> [opciones]

Ejemplos:
  python3 scripts/iptv_music.py https://iptv-org.github.io/iptv/index.m3u
  python3 scripts/iptv_music.py mi_lista.m3u --group "music|musica|música|musique"
  python3 scripts/iptv_music.py lista.m3u --deep          # además prueba con ffprobe
  python3 scripts/iptv_music.py lista.m3u --no-check      # solo filtrar, sin probar

Salida (en --out, por defecto la carpeta actual):
  <nombre>_music_vivos.m3u     solo los canales que respondieron
  <nombre>_music_todos.m3u     todos los de la categoría (vivos o no)
  <nombre>_music_reporte.csv   nombre, grupo, url, estado, detalle
  (con --basename X: X_vivos.m3u, X_todos.m3u, X_reporte.csv)

Solo librería estándar de Python (ffprobe es opcional para --deep).
"""
import argparse, concurrent.futures as cf, csv, os, re, shutil, ssl, subprocess, sys
import urllib.request, urllib.error
from urllib.parse import urljoin, urlparse

DEFAULT_GROUP = r"m[uú]si[ck]a?|musique|musik"
DEFAULT_UA = "VLC/3.0.20 LibVLC/3.0.20"
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


# ---------- lectura de la lista ----------
def load_text(src, ua):
    if re.match(r"https?://", src, re.I):
        req = urllib.request.Request(src, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    with open(src, encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_m3u(text):
    """Devuelve una lista de canales: dict(name, group, url, lines, ua, referer)."""
    channels, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.upper().startswith("#EXTM3U"):
            continue
        if line.upper().startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            cur = {"name": name or attrs.get("tvg-name", ""),
                   "group": attrs.get("group-title", ""),
                   "lines": [raw], "ua": None, "referer": None}
        elif line.startswith("#"):
            if cur is None:
                continue
            cur["lines"].append(raw)
            low = line.lower()
            if low.startswith("#extgrp:") and not cur["group"]:
                cur["group"] = line.split(":", 1)[1].strip()
            elif low.startswith("#extvlcopt:http-user-agent="):
                cur["ua"] = line.split("=", 1)[1].strip()
            elif low.startswith("#extvlcopt:http-referrer=") or low.startswith("#extvlcopt:http-referer="):
                cur["referer"] = line.split("=", 1)[1].strip()
        else:
            if cur is not None:
                cur["url"] = line
                cur["lines"].append(raw)
                channels.append(cur)
            cur = None
    return channels


def in_group(ch, rx):
    # iptv-org usa "Music;Entertainment": se revisa cada parte
    parts = re.split(r"[;|/,]", ch["group"]) if ch["group"] else []
    return any(rx.search(p.strip()) for p in parts)


# ---------- verificación ----------
_INSECURE = ssl.create_default_context()
_INSECURE.check_hostname = False
_INSECURE.verify_mode = ssl.CERT_NONE


def fetch(url, headers, timeout, nbytes=65536):
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        note = ""
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            r = urllib.request.urlopen(req, timeout=timeout, context=_INSECURE)
            note = "cert inválido"
        else:
            raise
    with r:
        return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(nbytes), note


def check_hls(body, base, headers, timeout, depth=0):
    """Una lista HLS viva debe llevar a segmentos que respondan."""
    text = body.decode("utf-8", "replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    uris = [l for l in lines if not l.startswith("#")]
    if not uris:
        return False, "m3u8 vacío"
    first = urljoin(base, uris[0])
    if "#EXT-X-STREAM-INF" in text and depth < 2:   # lista maestra → variante
        st, final, ctype, b, _ = fetch(first, headers, timeout)
        if st >= 400:
            return False, f"variante HTTP {st}"
        return check_hls(b, final, headers, timeout, depth + 1)
    # lista de segmentos: probar el último (el más reciente en vivo)
    seg = urljoin(base, uris[-1])
    st, _, _, b, _ = fetch(seg, headers, timeout, nbytes=2048)
    if st >= 400 or not b:
        return False, f"segmento HTTP {st}"
    return True, "HLS ok"


def check_ffprobe(url, ch, timeout):
    cmd = ["ffprobe", "-v", "error", "-rw_timeout", str(timeout * 1_000_000),
           "-user_agent", ch["ua"] or DEFAULT_UA]
    if ch["referer"]:
        cmd += ["-referer", ch["referer"]]
    cmd += ["-show_entries", "stream=codec_type", "-of", "csv=p=0", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        kinds = sorted(set(out.stdout.split()))
        if out.returncode == 0 and kinds:
            return True, "ffprobe: " + "+".join(kinds)
        return False, "ffprobe: " + (out.stderr.strip().splitlines() or ["sin streams"])[-1][:120]
    except subprocess.TimeoutExpired:
        return False, "ffprobe: timeout"


def check(ch, timeout, deep):
    url = ch["url"]
    headers = {"User-Agent": ch["ua"] or DEFAULT_UA, "Accept": "*/*"}
    if ch["referer"]:
        headers["Referer"] = ch["referer"]
    if not urlparse(url).scheme.startswith("http"):
        if deep:
            return check_ffprobe(url, ch, timeout)
        return None, "no-http (usa --deep)"
    try:
        st, final, ctype, body, note = fetch(url, headers, timeout)
        if st >= 400:
            return False, f"HTTP {st}"
        is_hls = body.lstrip()[:7].upper() == b"#EXTM3U" or "mpegurl" in ctype.lower()
        if is_hls:
            ok, detail = check_hls(body, final, headers, timeout)
        else:
            ok, detail = (len(body) > 0), (f"{ctype or 'stream'} ok" if body else "sin datos")
        if ok and deep and shutil.which("ffprobe"):
            ok, detail = check_ffprobe(url, ch, timeout)
        if note:
            detail += f" ({note})"
        return ok, detail
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        msg = getattr(e, "reason", e)
        return False, f"{type(e).__name__}: {msg}"[:120]


# ---------- salida ----------
def write_m3u(path, chans):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in chans:
            f.write("\n".join(ch["lines"]) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Filtra la categoría música de una lista IPTV y prueba los enlaces.")
    ap.add_argument("source", help="URL o archivo .m3u/.m3u8")
    ap.add_argument("--group", default=DEFAULT_GROUP,
                    help=f"regex (sin mayúsculas/minúsculas) para group-title. Por defecto: {DEFAULT_GROUP}")
    ap.add_argument("--name-too", action="store_true",
                    help="también acepta canales cuyo NOMBRE coincida con la regex (si la lista no trae grupos)")
    ap.add_argument("--out", default=".", help="carpeta de salida")
    ap.add_argument("--timeout", type=int, default=10, help="segundos por enlace (def. 10)")
    ap.add_argument("--workers", type=int, default=20, help="pruebas en paralelo (def. 20)")
    ap.add_argument("--deep", action="store_true", help="confirmar con ffprobe (más lento, más fiable)")
    ap.add_argument("--no-check", action="store_true", help="solo filtrar, no probar enlaces")
    ap.add_argument("--basename", help="prefijo fijo de los archivos de salida (ej. 'music' → music_vivos.m3u)")
    ap.add_argument("--quiet", action="store_true", help="no imprimir una línea por canal")
    ap.add_argument("--list-groups", action="store_true", help="mostrar los grupos de la lista y salir")
    a = ap.parse_args()

    text = load_text(a.source, DEFAULT_UA)
    chans = parse_m3u(text)
    if not chans:
        sys.exit("No se encontraron canales (¿es una lista M3U?).")

    if a.list_groups:
        from collections import Counter
        c = Counter(p.strip() for ch in chans for p in re.split(r"[;|/,]", ch["group"] or "(sin grupo)"))
        for g, n in c.most_common():
            print(f"{n:6}  {g}")
        return

    rx = re.compile(a.group, re.I)
    sel = [ch for ch in chans if in_group(ch, rx) or (a.name_too and rx.search(ch["name"]))]
    # quitar URLs repetidas
    seen, uniq = set(), []
    for ch in sel:
        if ch["url"] not in seen:
            seen.add(ch["url"]); uniq.append(ch)
    sel = uniq
    print(f"Lista: {len(chans)} canales · categoría '{a.group}': {len(sel)}")
    if not sel:
        print("Nada coincide. Prueba --list-groups para ver los nombres de grupo.")
        return

    os.makedirs(a.out, exist_ok=True)
    if a.basename:
        base = a.basename
    else:
        base = (os.path.splitext(os.path.basename(urlparse(a.source).path) or "lista")[0] or "lista") + "_music"
    write_m3u(os.path.join(a.out, f"{base}_todos.m3u"), sel)
    if a.no_check:
        print(f"Guardado {base}_todos.m3u en {os.path.abspath(a.out)}")
        return

    if a.deep and not shutil.which("ffprobe"):
        print("Aviso: ffprobe no está instalado; --deep se ignora.")

    results = {}
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(check, ch, a.timeout, a.deep): i for i, ch in enumerate(sel)}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            i = futs[fut]
            results[i] = fut.result()
            ok, detail = results[i]
            if a.quiet:
                continue
            mark = "✅" if ok else ("❔" if ok is None else "❌")
            print(f"[{n:>4}/{len(sel)}] {mark} {sel[i]['name'][:45]:45} {detail}")

    alive = [sel[i] for i in range(len(sel)) if results[i][0]]
    write_m3u(os.path.join(a.out, f"{base}_vivos.m3u"), alive)
    with open(os.path.join(a.out, f"{base}_reporte.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nombre", "grupo", "url", "estado", "detalle"])
        for i, ch in enumerate(sel):
            ok, d = results[i]
            w.writerow([ch["name"], ch["group"], ch["url"],
                        "vivo" if ok else ("sin probar" if ok is None else "muerto"), d])
    dead = sum(1 for r in results.values() if r[0] is False)
    print(f"\nVivos: {len(alive)} · Muertos: {dead} · Sin probar: {len(sel)-len(alive)-dead}")
    print(f"Archivos en {os.path.abspath(a.out)}: {base}_vivos.m3u, {base}_todos.m3u, {base}_reporte.csv")


if __name__ == "__main__":
    main()
