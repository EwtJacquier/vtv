#!/usr/bin/env python3
"""
thumbnail.py

Gera thumbnails para filmes HLS.
- Extrai frame no minuto 5 de cada filme
- Salva como thumb.jpg na pasta do filme

Uso:
  python thumbnail.py                    # Gera para todos em movies_hls/
  python thumbnail.py pasta_filme        # Gera para um filme específico
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def require_bin(name: str):
    if shutil.which(name) is None:
        print(f"ERRO: '{name}' não encontrado no PATH. Instale FFmpeg.")
        sys.exit(1)


def get_segment_duration(movie_dir: Path) -> float:
    """Lê duração do segmento do m3u8 (geralmente 4s)."""
    playlist = movie_dir / "stream.m3u8"
    try:
        with open(playlist, "r") as f:
            for line in f:
                if line.startswith("#EXT-X-TARGETDURATION:"):
                    return float(line.split(":")[1].strip())
    except:
        pass
    return 4.0


def generate_thumbnail(movie_dir: Path, target_seconds: int = 600) -> bool:
    """
    Gera thumbnail de um filme HLS (fMP4) no minuto 10.

    Args:
        movie_dir: Pasta do filme com stream.m3u8
        target_seconds: Tempo em segundos (padrão: 600 = 10 min)

    Returns:
        True se gerou com sucesso, False caso contrário
    """
    init_file = movie_dir / "init.mp4"
    thumb_path = movie_dir / "thumb.jpg"

    if not init_file.exists():
        print(f"  [SKIP] {movie_dir.name}: sem init.mp4")
        return False

    # Deletar thumbnail existente
    if thumb_path.exists():
        thumb_path.unlink()

    # Listar segmentos
    segments = sorted(movie_dir.glob("seg_*.m4s"))
    if not segments:
        print(f"  [SKIP] {movie_dir.name}: sem segmentos")
        return False

    # Calcular qual segmento corresponde ao tempo desejado
    seg_duration = get_segment_duration(movie_dir)

    # Tentar diferentes tempos (10min, 5min, 1min, 10s)
    for target_sec in [target_seconds, 300, 60, 10]:
        seg_idx = int(target_sec / seg_duration)

        if seg_idx >= len(segments):
            continue

        # Criar arquivo MP4 temporário concatenando init + segmento
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            temp_mp4 = f.name

        try:
            # Concatenar binariamente: init.mp4 + segmento.m4s
            with open(temp_mp4, 'wb') as out:
                with open(init_file, 'rb') as init_f:
                    out.write(init_f.read())
                with open(segments[seg_idx], 'rb') as seg_f:
                    out.write(seg_f.read())

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-i", temp_mp4,
                "-vframes", "1",
                "-vf", "scale=320:-1",
                "-q:v", "2",
                "-y",
                str(thumb_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
                print(f"  [OK] {movie_dir.name}")
                return True
        finally:
            os.unlink(temp_mp4)

    print(f"  [ERRO] {movie_dir.name}")
    return False


def generate_all_thumbnails(movies_dir: Path):
    """Gera thumbnails para todos os filmes em uma pasta."""
    if not movies_dir.exists():
        print(f"ERRO: Pasta não existe: {movies_dir}")
        sys.exit(1)

    # Listar todas as pastas de filmes
    movie_dirs = sorted([
        d for d in movies_dir.iterdir()
        if d.is_dir() and (d / "stream.m3u8").exists()
    ])

    if not movie_dirs:
        print("Nenhum filme encontrado.")
        return

    print(f"Gerando thumbnails para {len(movie_dirs)} filmes...\n")

    success = 0

    for movie_dir in movie_dirs:
        if generate_thumbnail(movie_dir):
            success += 1

    print(f"\nResumo: {success} gerados, {len(movie_dirs) - success} erros")


def main():
    require_bin("ffmpeg")

    if len(sys.argv) >= 2:
        # Gerar para pasta específica
        target = Path(sys.argv[1])
        if target.is_dir():
            generate_thumbnail(target)
        else:
            print(f"ERRO: {target} não é uma pasta válida")
            sys.exit(1)
    else:
        # Gerar para todos em movies_hls/
        script_dir = Path(__file__).parent
        movies_dir = script_dir / "movies_hls"
        generate_all_thumbnails(movies_dir)


if __name__ == "__main__":
    main()
