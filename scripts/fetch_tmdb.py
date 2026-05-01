#!/usr/bin/env python3
"""TMDB metadata crawler for VTV.

Reads channels/*.json, collects unique slugs + their channels, queries TMDB,
maps genres to a basic tag set, downloads covers, and writes db.json.

Usage:
    python scripts/fetch_tmdb.py                   # process missing entries
    python scripts/fetch_tmdb.py --slug homem_aranha --dry-run
    python scripts/fetch_tmdb.py --force           # reprocess everything
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CHANNELS_DIR = ROOT / "channels"
COVERS_DIR = ROOT / "covers"
DB_PATH = ROOT / "db.json"
FAILED_LOG = Path(__file__).resolve().parent / "_failed.txt"
ENV_PATH = ROOT / ".env"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"

# TMDB genre id -> basic tag(s).
# See https://developer.themoviedb.org/reference/genre-movie-list
GENRE_MAP = {
    28: ["acao"],
    12: ["aventura"],
    16: ["animacao"],          # may be replaced by "anime" if origin = JP
    35: ["comedia"],
    80: ["suspense"],          # Crime
    18: ["drama"],
    14: ["fantasia"],
    27: ["terror"],
    9648: ["suspense"],        # Mystery
    10749: ["romance"],
    878: ["ficcao_cientifica"],
    53: ["suspense"],          # Thriller
    10751: ["familia"],
    10752: ["guerra"],
    99: ["documentario"],
    # TV-specific combined genres
    10759: ["acao", "aventura"],
    10765: ["fantasia", "ficcao_cientifica"],
    10768: ["guerra"],
}


def load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def slug_to_query(slug: str) -> str:
    return slug.replace("_", " ")


def collect_slugs():
    """Returns dict {slug: {"channels": set, "duration": int}}."""
    catalog = {}
    for ch_path in sorted(CHANNELS_DIR.glob("*.json")):
        ch_name = ch_path.stem
        try:
            data = json.loads(ch_path.read_text())
        except json.JSONDecodeError:
            print(f"  ! skipping invalid JSON: {ch_path.name}")
            continue
        for key, items in data.items():
            if not key.startswith("dia_") or not isinstance(items, list):
                continue
            for item in items:
                slug = item.get("id")
                if not slug:
                    continue
                rec = catalog.setdefault(slug, {"channels": set(), "duration": 0})
                rec["channels"].add(ch_name)
                if not rec["duration"] and item.get("duration"):
                    rec["duration"] = item["duration"]
    return catalog


def tmdb_get(api_key, path, params=None):
    params = dict(params or {})
    params["api_key"] = api_key
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def search_title(api_key, query):
    """Try /search/movie first, then /search/tv. Returns (kind, id, n_results) or None."""
    for kind, endpoint in [("movie", "/search/movie"), ("tv", "/search/tv")]:
        data = tmdb_get(api_key, endpoint, {"query": query, "language": "pt-BR", "include_adult": "true"})
        results = data.get("results") or []
        if results:
            return kind, results[0]["id"], len(results)
    return None


def is_japanese(details, kind):
    if kind == "tv":
        return "JP" in (details.get("origin_country") or [])
    countries = details.get("production_countries") or []
    return any(c.get("iso_3166_1") == "JP" for c in countries)


def derive_tags(details, kind, channels):
    tags = set()
    for g in details.get("genres") or []:
        for t in GENRE_MAP.get(g.get("id"), []):
            tags.add(t)

    if is_japanese(details, kind) and "animacao" in tags:
        tags.discard("animacao")
        tags.add("anime")

    # Channel-based overrides (always applied, regardless of TMDB result)
    if "animetv" in channels:
        tags.discard("animacao")
        tags.add("anime")
    if "superHero" in channels:
        tags.add("super_herois")
    if "afterDark" in channels and not (tags & {"terror", "suspense"}):
        tags.add("suspense")

    return sorted(tags)


def download_cover(poster_path, slug):
    if not poster_path:
        return False
    out = COVERS_DIR / f"{slug}.jpg"
    if out.exists() and out.stat().st_size > 0:
        return True
    try:
        r = requests.get(f"{IMG_BASE}{poster_path}", timeout=30)
        r.raise_for_status()
        out.write_bytes(r.content)
        return True
    except requests.RequestException as e:
        print(f"    ! cover failed for {slug}: {e}")
        return False


def main():
    p = argparse.ArgumentParser(description="TMDB metadata crawler for VTV")
    p.add_argument("--dry-run", action="store_true", help="don't write db.json or covers")
    p.add_argument("--force", action="store_true", help="reprocess existing entries")
    p.add_argument("--slug", help="process only this slug")
    p.add_argument("--limit", type=int, default=0, help="stop after N slugs (debug)")
    p.add_argument("--sleep", type=float, default=0.25, help="delay between TMDB requests")
    args = p.parse_args()

    load_env()
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: TMDB_API_KEY not set (put it in .env or export it)")

    catalog = collect_slugs()
    if args.slug:
        if args.slug not in catalog:
            sys.exit(f"slug {args.slug!r} not found in any channels/*.json")
        catalog = {args.slug: catalog[args.slug]}
    print(f"Collected {len(catalog)} unique slugs from channels/")

    db = {}
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text() or "{}")
        except json.JSONDecodeError:
            db = {}

    processed = new_count = 0
    failures = []
    items = sorted(catalog.items())

    for i, (slug, info) in enumerate(items, 1):
        if args.limit and processed >= args.limit:
            break

        cover_path = COVERS_DIR / f"{slug}.jpg"
        if not args.force and slug in db and cover_path.exists():
            continue

        processed += 1
        query = slug_to_query(slug)
        print(f"[{i}/{len(items)}] {slug}  query={query!r}")

        try:
            search = search_title(api_key, query)
        except requests.RequestException as e:
            print(f"    ! search error: {e}")
            failures.append(f"{slug}\tsearch_error\t{e}")
            time.sleep(args.sleep * 2)
            continue
        time.sleep(args.sleep)

        if not search:
            print(f"    ! no TMDB match")
            failures.append(f"{slug}\tno_match\tquery={query}")
            continue
        kind, tmdb_id, n_results = search

        try:
            details = tmdb_get(api_key, f"/{kind}/{tmdb_id}", {"language": "pt-BR"})
        except requests.RequestException as e:
            print(f"    ! details error: {e}")
            failures.append(f"{slug}\tdetails_error\t{e}")
            continue
        time.sleep(args.sleep)

        name = details.get("title") or details.get("name") or slug
        synopsis = (details.get("overview") or "").strip()
        tags = derive_tags(details, kind, info["channels"])

        entry = {
            "name": name,
            "slug": slug,
            "duration": info.get("duration") or 0,
            "synopsis": synopsis,
            "tags": tags,
        }
        if n_results > 1:
            entry["_review"] = True  # multiple matches — user may want to verify

        poster_path = details.get("poster_path")
        if args.dry_run:
            cover_ok = bool(poster_path)
        else:
            cover_ok = download_cover(poster_path, slug)

        flag = " [REVIEW]" if n_results > 1 else ""
        print(f"    ok  {name!r}  tags={tags}  cover={'ok' if cover_ok else 'NO'}{flag}")

        if args.dry_run:
            continue

        db[slug] = entry
        new_count += 1
        # Persist after each entry so Ctrl+C doesn't lose progress
        DB_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")

    if failures and not args.dry_run:
        FAILED_LOG.write_text("\n".join(failures) + "\n")

    print()
    print(f"Processed: {processed}")
    print(f"New/updated entries: {new_count}")
    print(f"Failures: {len(failures)}")
    if failures:
        print(f"  -> details in {FAILED_LOG}")


if __name__ == "__main__":
    main()
