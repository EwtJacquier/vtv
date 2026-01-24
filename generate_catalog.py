#!/usr/bin/env python3
"""
generate_catalog.py

Gera catalog.json com lista de filmes em movies_hls/
Usado pelo streaming.html para listar o catálogo.

Uso:
  python generate_catalog.py
"""

import json
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).parent
    movies_dir = script_dir / "movies_hls"
    catalog_path = movies_dir / "catalog.json"

    if not movies_dir.exists():
        print(f"ERRO: Pasta não existe: {movies_dir}")
        sys.exit(1)

    # Listar todas as pastas de filmes com stream.m3u8
    movies = []
    for movie_dir in sorted(movies_dir.iterdir()):
        if movie_dir.is_dir() and (movie_dir / "stream.m3u8").exists():
            movies.append({"id": movie_dir.name})

    # Salvar catalog.json
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(movies, f, indent=2, ensure_ascii=False)

    print(f"Catálogo gerado: {catalog_path}")
    print(f"Total: {len(movies)} filmes")


if __name__ == "__main__":
    main()
