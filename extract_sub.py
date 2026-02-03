#!/usr/bin/env python3
"""
extract_sub.py

Extrai uma faixa de legenda de um vídeo e salva como .srt
Suporta legendas de texto (SRT, ASS, SSA, etc.) e de imagem (PGS, DVB) via OCR.

Uso:
  python extract_sub.py <video> [saida.srt]
"""

import subprocess
import sys
from pathlib import Path

from conversor import (
    ffprobe_meta,
    is_image_subtitle,
    pick_stream,
    require_bin,
    stream_label,
)


def main():
    require_bin("ffmpeg")
    require_bin("ffprobe")

    if len(sys.argv) < 2:
        print("Uso: python extract_sub.py <video> [saida.srt]")
        sys.exit(1)

    input_path = sys.argv[1]
    if not Path(input_path).is_file():
        print(f"ERRO: arquivo não existe: {input_path}")
        sys.exit(1)

    meta = ffprobe_meta(input_path)
    streams = meta.get("streams", []) or []
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    if not sub_streams:
        print("ERRO: nenhuma faixa de legenda encontrada no vídeo.")
        sys.exit(1)

    print(f"== LEGENDAS ({len(sub_streams)} faixa(s)) ==")
    chosen = pick_stream(sub_streams, "Escolha a legenda [0..]: ")
    if chosen is None:
        sys.exit(1)

    codec = chosen.get("codec_name", "")
    sub_index = chosen["index"]

    if is_image_subtitle(codec):
        print(f"\nATENÇÃO: legenda de imagem ({codec}).")
        print("FFmpeg não converte PGS/DVB para SRT diretamente.")
        print("Use uma ferramenta de OCR como:")
        print("  - SubtitleEdit (GUI)")
        print("  - PGS2SRT / Subtitle2Go")
        print(f"\nPara extrair o .sup (formato nativo):")

        sup_out = sys.argv[2] if len(sys.argv) >= 3 else Path(input_path).stem + ".sup"
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-map", f"0:{sub_index}",
            "-c:s", "copy",
            str(sup_out),
        ]
        print(f"Comando: {' '.join(cmd)}\n")
        p = subprocess.Popen(cmd)
        code = p.wait()
        if code != 0:
            print("ERRO: ffmpeg falhou.")
            sys.exit(code)
        print(f"Pronto! Salvo: {sup_out}")
        sys.exit(0)

    # Legenda de texto — extrai como SRT
    out_path = sys.argv[2] if len(sys.argv) >= 3 else Path(input_path).stem + ".srt"

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_path,
        "-map", f"0:{sub_index}",
        "-c:s", "srt",
        str(out_path),
    ]

    print(f"\nExtraindo legenda ({codec}) → {out_path}")
    print(f"Comando: {' '.join(cmd)}\n")

    p = subprocess.Popen(cmd)
    code = p.wait()

    if code != 0:
        print("ERRO: ffmpeg falhou.")
        sys.exit(code)

    print(f"Pronto! Legenda salva: {out_path}")


if __name__ == "__main__":
    main()
