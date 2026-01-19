#!/usr/bin/env python3
"""
inspector.py - Retorna a duração de um filme HLS

Uso:
  python inspector.py <pasta_filme>
  python inspector.py ./movies_hls/meu_filme
"""

import sys
from pathlib import Path


def parse_m3u8_duration(m3u8_path: Path) -> float:
    total = 0.0
    text = m3u8_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            val = line[len("#EXTINF:"):]
            if "," in val:
                val = val.split(",", 1)[0]
            try:
                total += float(val)
            except ValueError:
                pass
    return total


def fmt_duration(seconds: float) -> str:
    secs = int(round(seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def main():
    if len(sys.argv) < 2:
        print("Uso: python inspector.py <pasta_filme>")
        print("  Ex: python inspector.py ./movies_hls/meu_filme")
        sys.exit(1)

    folder = Path(sys.argv[1]).resolve()

    if not folder.exists():
        print(f"ERRO: pasta não existe: {folder}")
        sys.exit(1)

    if not folder.is_dir():
        print(f"ERRO: não é uma pasta: {folder}")
        sys.exit(1)

    m3u8 = folder / "stream.m3u8"
    if not m3u8.exists():
        print(f"ERRO: stream.m3u8 não encontrado em: {folder}")
        sys.exit(1)

    duration = parse_m3u8_duration(m3u8)
    print(f"{folder.name}: {fmt_duration(duration)} ({int(round(duration))}s)")


if __name__ == "__main__":
    main()
