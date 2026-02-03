#!/usr/bin/env python3
"""Verifica quais filmes em movies_hls/ não estão em nenhum channels/*.json"""

import os
import json
from pathlib import Path

def main():
    base_dir = Path(__file__).parent
    movies_dir = base_dir / "movies_hls"
    channels_dir = base_dir / "channels"

    # Lista filmes em movies_hls/ (ordenados, igual ao playlist.py)
    if not movies_dir.exists():
        print("Pasta movies_hls/ não encontrada")
        return

    filmes_ordenados = sorted([d.name for d in movies_dir.iterdir() if d.is_dir()])
    # Mapa de nome -> índice
    indice_filme = {nome: i for i, nome in enumerate(filmes_ordenados)}
    filmes = set(filmes_ordenados)

    # Coleta IDs usados nos canais
    ids_usados = set()
    for channel_file in channels_dir.glob("*.json"):
        with open(channel_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for key, value in data.items():
            if key.startswith("dia_") and isinstance(value, list):
                for item in value:
                    if "id" in item:
                        ids_usados.add(item["id"])

    # Filmes não usados
    nao_usados = filmes - ids_usados

    if nao_usados:
        print(f"Filmes não usados ({len(nao_usados)}):")
        for filme in sorted(nao_usados):
            idx = indice_filme[filme]
            print(f"  [{idx}] {filme}")
    else:
        print("Todos os filmes estão em uso!")

    # Bonus: IDs referenciados que não existem
    nao_existem = ids_usados - filmes
    if nao_existem:
        print(f"\nIDs nos canais que não existem em movies_hls/ ({len(nao_existem)}):")
        for id_ in sorted(nao_existem):
            print(f"  - {id_}")

if __name__ == "__main__":
    main()
