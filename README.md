# Metanoia · robot de YouTube

Todos los días (03:13 AR) GitHub Actions:
1. lee los episodios de la base de la web (Supabase, lectura pública),
2. elige los próximos 20 (del más nuevo al más viejo) que todavía no están en YouTube, que tienen audio publicado
   y **tracklist en los dos sets** (variable `REQUIRE_TRACKLIST=0` para incluir también los que no tienen),
3. baja el audio de SoundCloud / Mixcloud (si el episodio viejo está en dos sets, los une),
4. dibuja el video con el diseño del show (`render.html` de la web: Isotipo como portada al arrancar y al cambiar de hora + Ficha en loop
   con la banda que gira) y lo une al audio con ffmpeg,
5. lo sube a YouTube **en Privado** con título, descripción (capítulos + tracklist), etiquetas y miniatura,
6. anota lo subido en `uploaded.json` (así no repite).

## Secretos (Settings → Secrets and variables → Actions)
| Nombre | Qué es |
|---|---|
| `YT_CLIENT_ID` | ID de cliente OAuth del proyecto de Google Cloud |
| `YT_CLIENT_SECRET` | Secreto de ese cliente |
| `YT_REFRESH_TOKEN` | Permiso de la cuenta de Metanoia (se saca una vez en OAuth Playground) |

Variables opcionales (pestaña *Variables*): `COUNT` (por día, 20), `ORDER` (`newest` / `oldest`), `PLAYLIST_ID` (lista de YouTube donde agregarlos), `SITE`.

## Correrlo a mano
Actions → *Subir episodios a YouTube* → *Run workflow*. Con “Prueba” tildado arma los videos pero no sube nada.
