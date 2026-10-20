# Tawyers IPTV — Música

> 🎧 **Menos ruido, más música.**

Lista IPTV **solo de música** y **solo con canales vivos**, regenerada sola todos los días por GitHub Actions para usarla en [Tawyers](https://github.com/hamunoz/Tawyers).

## La lista

Pega esta URL en Tawyers → *Plugins → IPTV → Agregar lista*:

```
https://raw.githubusercontent.com/hamunoz/Tawyers-IPTV/main/lists/music_vivos.m3u
```

| Archivo | Qué es |
|---|---|
| `lists/music_vivos.m3u` | Canales de música que respondieron en la última prueba |
| `lists/music_todos.m3u` | Todos los canales de música de la fuente (vivos o no) |
| `lists/music_reporte.csv` | Nombre, grupo, URL, estado y motivo de cada canal |

## Cómo funciona

1. **Fuente:** [iptv-org](https://github.com/iptv-org/iptv) completa (`index.m3u`).
2. **Filtro:** canales cuyo `group-title` sea *Music / Música / Musique / Musik* (iptv-org usa `Music;Entertainment`, se revisa cada parte).
3. **Prueba:** cada enlace se abre con User-Agent de VLC (o el que trae la lista en `#EXTVLCOPT`). En HLS no basta con que responda la `.m3u8`: se baja hasta un segmento real.
4. **Publicación:** si algo cambió, el workflow hace commit en `lists/`.


> ⚠️ La prueba se hace desde los servidores de GitHub (EE. UU.). Un canal bloqueado por región o que rechaza IPs de datacenter puede salir como muerto aunque a ti te funcione; mira `music_reporte.csv` si echas de menos alguno.

## Usar el script en local

```bash
python3 scripts/iptv_music.py <URL o archivo.m3u> [--group REGEX] [--deep] [--out carpeta]
python3 scripts/iptv_music.py lista.m3u --list-groups   # ver los grupos de una lista
```

Solo librería estándar de Python; `--deep` usa `ffprobe` si está instalado.

## Créditos

Los canales vienen de [iptv-org/iptv](https://github.com/iptv-org/iptv). Este repo no aloja ningún stream: solo enlaces públicos.
