#!/usr/bin/env python3
"""Extrae metadatos públicos de series finalizadas de JKAnime.

No descarga videos ni intenta acceder a contenido privado. El resultado se
guarda en archivos JSON independientes para permitir reanudar la extracción.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import html
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://jkanime.net"
DIRECTORY_URL = f"{BASE_URL}/directorio?estado=finalizados&p={{page}}"
ROOT = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "catalogo"
ANIME_DIR = CATALOG_DIR / "animes"
STATE_DIR = CATALOG_DIR / ".estado"
INDEX_FILE = CATALOG_DIR / "index.json"
MANIFEST_FILE = CATALOG_DIR / "manifest.json"
ERROR_FILE = CATALOG_DIR / "errores.json"
DIRECTORY_STATE_FILE = STATE_DIR / "directorio.json"
USER_AGENT = "AnimeJD-Catalogo/1.0 (archivo de metadatos; contacto: https://animejd.lat)"
EPISODES_PER_PAGE = 16


class RateLimiter:
    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, delay)
        self.lock = threading.Lock()
        self.next_allowed = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_allowed:
                time.sleep(self.next_allowed - now)
            self.next_allowed = time.monotonic() + self.delay


class WebSession:
    def __init__(self, limiter: RateLimiter, timeout: float = 30.0) -> None:
        self.limiter = limiter
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(self, url: str, *, data: dict[str, str] | None = None, referer: str = "") -> str:
        encoded = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        if data is not None:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        last_error: Exception | None = None
        for attempt in range(5):
            self.limiter.wait()
            try:
                req = urllib.request.Request(url, data=encoded, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 401, 403, 404}:
                    raise
                time.sleep(min(20.0, 1.5 * (2**attempt)))
        raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_directories() -> None:
    ANIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def extract_assigned_json(document: str, variable: str) -> dict[str, Any]:
    marker = f"var {variable} = "
    start = document.find(marker)
    if start < 0:
        raise ValueError(f"No se encontró la variable {variable}")
    start += len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(document[start:].lstrip())
    if not isinstance(value, dict):
        raise ValueError(f"La variable {variable} no contiene un objeto")
    return value


def clean_text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def normalize_anime(row: dict[str, Any]) -> dict[str, Any]:
    slug = clean_text(row.get("slug"))
    return {
        "id_origen": row.get("id"),
        "slug": slug,
        "titulo": clean_text(row.get("title")),
        "titulo_corto": clean_text(row.get("short_title")),
        "sinopsis": clean_text(row.get("synopsis")),
        "portada_origen": clean_text(row.get("image")),
        "tipo": clean_text(row.get("tipo") or row.get("type")),
        "estado": clean_text(row.get("estado") or "Concluido"),
        "estado_origen": clean_text(row.get("status") or "finished"),
        "estudio": clean_text(row.get("studios")),
        "url_origen": clean_text(row.get("url")) or f"{BASE_URL}/{slug}/",
        "episodios": [],
        "episodios_extraidos": False,
        "extraido_en": utc_now(),
        "fuente": "JKAnime",
    }


def extract_directory(session: WebSession) -> list[dict[str, Any]]:
    ensure_directories()
    saved = read_json(DIRECTORY_STATE_FILE, {"pages": {}, "last_page": None})
    pages: dict[str, list[dict[str, Any]]] = saved.get("pages", {})
    last_page = saved.get("last_page")
    page = 1

    while last_page is None or page <= int(last_page):
        key = str(page)
        if key not in pages:
            payload = extract_assigned_json(session.request(DIRECTORY_URL.format(page=page)), "animes")
            last_page = int(payload.get("last_page") or page)
            pages[key] = [normalize_anime(row) for row in payload.get("data", []) if row.get("status") == "finished"]
            saved = {"last_page": last_page, "pages": pages, "updated_at": utc_now()}
            write_json(DIRECTORY_STATE_FILE, saved)
            print(f"Directorio: página {page}/{last_page} ({len(pages[key])} series)", flush=True)
        page += 1

    by_slug: dict[str, dict[str, Any]] = {}
    for number in sorted((int(item) for item in pages), key=int):
        for anime in pages[str(number)]:
            if anime.get("slug"):
                by_slug[anime["slug"]] = anime

    existing_index = {item.get("slug"): item for item in read_json(INDEX_FILE, [])}
    index: list[dict[str, Any]] = []
    for slug, anime in sorted(by_slug.items(), key=lambda pair: pair[1]["titulo"].casefold()):
        anime_path = ANIME_DIR / f"{slug}.json"
        existing = read_json(anime_path, {})
        if existing.get("episodios_extraidos"):
            anime["episodios"] = existing.get("episodios", [])
            anime["episodios_extraidos"] = True
            anime["detalle_extraido_en"] = existing.get("detalle_extraido_en")
        write_json(anime_path, anime)
        previous = existing_index.get(slug, {})
        index.append({
            "slug": slug,
            "titulo": anime["titulo"],
            "portada_origen": anime["portada_origen"],
            "tipo": anime["tipo"],
            "estado": anime["estado"],
            "url_origen": anime["url_origen"],
            "cantidad_episodios": len(anime.get("episodios", [])) if anime.get("episodios_extraidos") else previous.get("cantidad_episodios"),
            "episodios_extraidos": bool(anime.get("episodios_extraidos")),
        })

    write_json(INDEX_FILE, index)
    update_manifest(index)
    return index


def episode_thumbnail(anime: dict[str, Any], image: str) -> str:
    image = clean_text(image)
    if not image:
        return anime.get("portada_origen", "")
    if image.startswith("http://") or image.startswith("https://"):
        return image
    cover = anime.get("portada_origen", "")
    if "/animes/image/" in cover:
        prefix = cover.replace("/animes/image/", "/animes/video/image_thumb/").rsplit("/", 1)[0]
        return f"{prefix}/{image}"
    return image


def extract_episode_page(session: WebSession, anime: dict[str, Any], source_id: str, token: str, page: int) -> dict[str, Any]:
    endpoint = f"{BASE_URL}/ajax/episodes/{source_id}/{page}"
    raw = session.request(endpoint, data={"_token": token}, referer=anime["url_origen"])
    return json.loads(raw)


def extract_anime(anime: dict[str, Any], limiter: RateLimiter, force: bool) -> tuple[str, int, str | None]:
    path = ANIME_DIR / f"{anime['slug']}.json"
    current = read_json(path, anime)
    if current.get("episodios_extraidos") and not force:
        return anime["slug"], len(current.get("episodios", [])), None

    try:
        session = WebSession(limiter)
        document = session.request(anime["url_origen"])
        token_match = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)', document, re.I)
        id_match = re.search(r'/ajax/episodes/(\d+)/', document)
        count_match = re.search(r'Episodios:</span>\s*([0-9]+)', document, re.I)
        if not token_match or not id_match:
            raise ValueError("La página no expone la lista pública de episodios")

        token = token_match.group(1)
        source_id = id_match.group(1)
        expected = int(count_match.group(1)) if count_match else 0
        episodes: list[dict[str, Any]] = []
        page = 1
        last_page = max(1, math.ceil(expected / EPISODES_PER_PAGE))

        while page <= last_page:
            payload = extract_episode_page(session, anime, source_id, token, page)
            last_page = int(payload.get("last_page") or math.ceil(int(payload.get("total") or expected) / EPISODES_PER_PAGE) or page)
            for item in payload.get("data", []):
                number = item.get("number")
                episodes.append({
                    "id_origen": item.get("id"),
                    "numero": number,
                    "titulo": clean_text(item.get("title")) or f"Episodio {number}",
                    "miniatura_origen": episode_thumbnail(anime, item.get("image", "")),
                    "url_origen": f"{anime['url_origen'].rstrip('/')}/{number}/",
                    "publicado_en_origen": item.get("timestamp"),
                })
            page += 1

        unique = {str(item["numero"]): item for item in episodes}
        episodes = sorted(unique.values(), key=lambda item: float(item["numero"]) if str(item["numero"]).replace(".", "", 1).isdigit() else str(item["numero"]))
        current.update(anime)
        current.update({
            "id_catalogo_origen": source_id,
            "episodios": episodes,
            "episodios_extraidos": True,
            "cantidad_episodios_declarada": expected,
            "detalle_extraido_en": utc_now(),
        })
        write_json(path, current)
        return anime["slug"], len(episodes), None
    except Exception as exc:  # El error queda registrado y se puede reintentar.
        return anime["slug"], 0, str(exc)


def extract_episodes(index: list[dict[str, Any]], *, workers: int, delay: float, force: bool) -> None:
    ensure_directories()
    limiter = RateLimiter(delay)
    errors = read_json(ERROR_FILE, {})
    completed = 0
    total = len(index)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {}
        for item in index:
            anime = read_json(ANIME_DIR / f"{item['slug']}.json", item)
            if anime.get("episodios_extraidos") and not force:
                completed += 1
                continue
            futures[pool.submit(extract_anime, anime, limiter, force)] = item["slug"]

        for future in concurrent.futures.as_completed(futures):
            slug, count, error = future.result()
            completed += 1
            if error:
                errors[slug] = {"error": error, "updated_at": utc_now()}
            else:
                errors.pop(slug, None)
            if completed % 10 == 0 or error:
                print(f"Fichas: {completed}/{total} | {slug}: {count} episodios" + (f" | ERROR: {error}" if error else ""), flush=True)
                write_json(ERROR_FILE, errors)
                refresh_index_from_files()

    write_json(ERROR_FILE, errors)
    refresh_index_from_files()


def refresh_index_from_files() -> list[dict[str, Any]]:
    index = read_json(INDEX_FILE, [])
    refreshed = []
    for item in index:
        anime = read_json(ANIME_DIR / f"{item['slug']}.json", item)
        item["episodios_extraidos"] = bool(anime.get("episodios_extraidos"))
        item["cantidad_episodios"] = len(anime.get("episodios", [])) if anime.get("episodios_extraidos") else None
        refreshed.append(item)
    write_json(INDEX_FILE, refreshed)
    update_manifest(refreshed)
    return refreshed


def update_manifest(index: list[dict[str, Any]]) -> None:
    completed = sum(1 for item in index if item.get("episodios_extraidos"))
    manifest = {
        "fuente": "https://jkanime.net/directorio?estado=finalizados",
        "filtro": "finalizados",
        "generado_en": utc_now(),
        "total_animes": len(index),
        "animes_con_episodios_extraidos": completed,
        "animes_pendientes": len(index) - completed,
        "total_episodios": sum(int(item.get("cantidad_episodios") or 0) for item in index),
        "incluye_videos": False,
        "incluye_credenciales": False,
    }
    write_json(MANIFEST_FILE, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", action="store_true", help="Extraer solamente el directorio finalizado")
    parser.add_argument("--episodios", action="store_true", help="Completar las listas públicas de episodios")
    parser.add_argument("--todo", action="store_true", help="Extraer directorio y episodios")
    parser.add_argument("--workers", type=int, default=3, help="Cantidad de fichas procesadas en paralelo")
    parser.add_argument("--delay", type=float, default=0.5, help="Pausa global mínima entre solicitudes")
    parser.add_argument("--force", action="store_true", help="Volver a extraer fichas ya completadas")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not (args.directorio or args.episodios or args.todo):
        args.todo = True
    ensure_directories()
    session = WebSession(RateLimiter(args.delay))

    if args.directorio or args.todo or not INDEX_FILE.exists():
        index = extract_directory(session)
    else:
        index = read_json(INDEX_FILE, [])

    if args.episodios or args.todo:
        extract_episodes(index, workers=args.workers, delay=args.delay, force=args.force)

    print(json.dumps(read_json(MANIFEST_FILE, {}), ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

