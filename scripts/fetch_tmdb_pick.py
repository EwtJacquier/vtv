#!/usr/bin/env python3
"""Interactive TMDB picker for a single slug.

Pass the slug + an optional search hint (free text). The script lists TMDB
candidates, you pick the right one by number, and the entry is written to
db.json + cover saved. Use this to fix entries that fetch_tmdb.py couldn't
match or that came out wrong (the ones flagged with `_review: true`).

Usage:
    python scripts/fetch_tmdb_pick.py john_wick_capitulo_1
    python scripts/fetch_tmdb_pick.py john_wick_capitulo_1 "john wick 2014"
    python scripts/fetch_tmdb_pick.py casshan_parte_1 "casshern sins"
    python scripts/fetch_tmdb_pick.py homem_aranha --no-cover

When run, you can also type:
    r <new query>    retry search with a different query
    q                quit without saving
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

# Reuse helpers from the main crawler
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_tmdb import (
    COVERS_DIR,
    DB_PATH,
    TMDB_BASE,
    collect_slugs,
    derive_tags,
    download_cover,
    load_env,
    tmdb_get,
)

PAGE_SIZE = 10  # how many candidates to show


def split_query_and_year(raw):
    """If query ends with a 4-digit year (1900-2099), extract it as a filter."""
    m = re.search(r"\s+(19\d{2}|20\d{2})\s*$", raw)
    if m:
        return raw[: m.start()].strip(), m.group(1)
    return raw, None


def search_both(api_key, query, year=None):
    """Return list of dicts: [{kind, id, title, year, popularity}, ...]."""
    out = []
    for kind, endpoint in [("movie", "/search/movie"), ("tv", "/search/tv")]:
        params = {"query": query, "language": "pt-BR", "include_adult": "true"}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        try:
            data = tmdb_get(api_key, endpoint, params)
        except requests.RequestException as e:
            print(f"  ! search/{kind} failed: {e}")
            continue
        for r in (data.get("results") or [])[:PAGE_SIZE]:
            date = r.get("release_date") or r.get("first_air_date") or ""
            year = date[:4] if date else "----"
            title = r.get("title") or r.get("name") or "(no title)"
            original = r.get("original_title") or r.get("original_name") or ""
            out.append({
                "kind": kind,
                "id": r["id"],
                "title": title,
                "original": original if original and original != title else "",
                "year": year,
                "popularity": r.get("popularity") or 0,
            })
    out.sort(key=lambda x: x["popularity"], reverse=True)
    return out[: PAGE_SIZE * 2]


def print_candidates(candidates):
    if not candidates:
        print("  (no results)")
        return
    for i, c in enumerate(candidates, 1):
        extra = f"  [{c['original']}]" if c["original"] else ""
        print(f"  [{i}] {c['title']} ({c['year']}){extra}  {c['kind']}  id={c['id']}  pop={c['popularity']:.1f}")


def prompt_choice(candidates):
    while True:
        try:
            raw = input("\nPick (1-N), 'r <new query>' to retry, 'q' to quit: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ("quit", None)
        if not raw:
            continue
        if raw.lower() == "q":
            return ("quit", None)
        if raw.lower().startswith("r "):
            new_q = raw[2:].strip()
            if new_q:
                return ("retry", new_q)
            print("  empty query, try again")
            continue
        try:
            n = int(raw)
        except ValueError:
            print("  invalid input")
            continue
        if 1 <= n <= len(candidates):
            return ("pick", candidates[n - 1])
        print(f"  out of range (1..{len(candidates)})")


def main():
    p = argparse.ArgumentParser(description="Interactive TMDB picker for a single slug")
    p.add_argument("slug", help="slug to fetch (must exist as a folder/id; not strictly required to exist in channels)")
    p.add_argument("hint", nargs="?", help="optional search query (overrides slug-derived query)")
    p.add_argument("--no-cover", action="store_true", help="don't download cover image")
    p.add_argument("--keep-cover", action="store_true", help="don't overwrite existing cover")
    args = p.parse_args()

    load_env()
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: TMDB_API_KEY not set (put it in .env or export it)")

    slug = args.slug
    catalog = collect_slugs()
    info = catalog.get(slug, {"channels": set(), "duration": 0})
    channels = info["channels"]
    duration = info.get("duration") or 0

    print(f"slug:     {slug}")
    print(f"channels: {sorted(channels) if channels else '(none — slug not in any channel JSON)'}")
    print(f"duration: {duration}s" if duration else "duration: 0s (slug not in channels — fill manually if needed)")

    db = {}
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text() or "{}")
        except json.JSONDecodeError:
            db = {}
    if slug in db:
        cur = db[slug]
        flag = " [REVIEW]" if cur.get("_review") else ""
        print(f"existing entry: {cur['name']!r}  tags={cur['tags']}{flag}")

    query = args.hint if args.hint else slug.replace("_", " ")

    while True:
        q, year = split_query_and_year(query)
        suffix = f" (year={year})" if year else ""
        print(f"\nSearching TMDB for {q!r}{suffix} ...")
        candidates = search_both(api_key, q, year)
        print_candidates(candidates)

        action, payload = prompt_choice(candidates)
        if action == "quit":
            print("aborted, nothing saved.")
            return
        if action == "retry":
            query = payload
            continue
        if action == "pick":
            chosen = payload
            break

    # Fetch details of chosen result
    try:
        details = tmdb_get(api_key, f"/{chosen['kind']}/{chosen['id']}", {"language": "pt-BR"})
    except requests.RequestException as e:
        sys.exit(f"ERROR: details fetch failed: {e}")

    name = details.get("title") or details.get("name") or slug
    synopsis = (details.get("overview") or "").strip()
    tags = derive_tags(details, chosen["kind"], channels)
    poster_path = details.get("poster_path")

    print()
    print(f"chosen:   {name} ({chosen['year']})  {chosen['kind']}  id={chosen['id']}")
    print(f"synopsis: {synopsis[:200]}{'...' if len(synopsis) > 200 else ''}")
    print(f"tags:     {tags}")
    print(f"poster:   {'yes' if poster_path else 'NONE on TMDB'}")

    try:
        confirm = input("\nSave to db.json? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\naborted, nothing saved.")
        return
    if confirm and confirm not in ("y", "yes"):
        print("aborted, nothing saved.")
        return

    entry = {
        "name": name,
        "slug": slug,
        "duration": duration,
        "synopsis": synopsis,
        "tags": tags,
    }
    db[slug] = entry
    DB_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")
    print(f"saved entry to {DB_PATH.name}")

    cover_path = COVERS_DIR / f"{slug}.jpg"
    if args.no_cover:
        print("(skipped cover — --no-cover)")
    elif args.keep_cover and cover_path.exists():
        print(f"(kept existing cover: {cover_path.name})")
    else:
        if cover_path.exists():
            cover_path.unlink()  # force re-download
        if download_cover(poster_path, slug):
            print(f"cover saved to covers/{slug}.jpg")
        else:
            print("(no cover — TMDB had no poster_path)")


if __name__ == "__main__":
    main()
