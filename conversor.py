#!/usr/bin/env python3
"""
hlsify.py

- Mostra infos do vídeo original (codec, resolução, fps, bitrate se existir)
- Você escolhe a faixa de áudio (ex: dublada)
- (Opcional) legenda: remover ou queimar (burn-in)
- Você escolhe o modo de encode: CPU (x264) / NVIDIA NVENC / Intel QSV / AMD AMF / COPY (tenta copiar vídeo sem re-encode)
- Converte para HLS "real" usando fMP4 (segmentos .m4s) pra reduzir overhead e tamanho
- Aplica otimizações pra não explodir o tamanho:
    * CRF/CQ mais alto (menor)
    * maxrate + bufsize (cap no bitrate)
    * áudio AAC 128k por padrão

Uso:
  python hlsify.py "filme.mkv" "saida_hls"
"""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def require_bin(name: str):
    if shutil.which(name) is None:
        print(f"ERRO: '{name}' não encontrado no PATH. Instale FFmpeg (ffmpeg + ffprobe).")
        sys.exit(1)


def ffprobe_meta(input_path: str) -> dict:
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        input_path,
    ]
    p = run(cmd)
    if p.returncode != 0:
        print("ERRO ao rodar ffprobe:\n", p.stderr)
        sys.exit(1)
    return json.loads(p.stdout)


def ffmpeg_encoders_text() -> str:
    p = run(["ffmpeg", "-hide_banner", "-encoders"])
    return (p.stdout or "") + "\n" + (p.stderr or "")


def has_encoder(encoders_text: str, name: str) -> bool:
    # Procurar " h264_nvenc " etc.
    return f" {name} " in encoders_text or f" {name}\n" in encoders_text


def parse_fraction(frac: str) -> float | None:
    # "30000/1001" -> 29.97
    if not frac or "/" not in frac:
        return None
    a, b = frac.split("/", 1)
    try:
        a = float(a)
        b = float(b)
        if b == 0:
            return None
        return a / b
    except Exception:
        return None


def human_bps(bps: int | None) -> str:
    if not bps or bps <= 0:
        return "desconhecido"
    # bits/s -> Mb/s
    return f"{bps/1_000_000:.2f} Mb/s"


def human_bytes(n: int | None) -> str:
    if not n or n <= 0:
        return "desconhecido"
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    i = 0
    while x >= 1024 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    return f"{x:.2f} {units[i]}"


def stream_label(s: dict) -> str:
    idx = s.get("index")
    codec = s.get("codec_name")
    ctype = s.get("codec_type")
    tags = s.get("tags", {}) or {}
    lang = tags.get("language", "und")
    title = tags.get("title", "")
    br = s.get("bit_rate")
    br_i = int(br) if isinstance(br, str) and br.isdigit() else (br if isinstance(br, int) else None)

    info = [f"#{idx}", f"{ctype}/{codec}", f"lang={lang}"]
    if title:
        info.append(f"title={title}")
    if ctype == "video":
        w = s.get("width")
        h = s.get("height")
        fps = parse_fraction(s.get("avg_frame_rate") or "")
        if w and h:
            info.append(f"{w}x{h}")
        if fps:
            info.append(f"{fps:.2f}fps")
    if ctype == "audio":
        ch = s.get("channels")
        sr = s.get("sample_rate")
        if ch:
            info.append(f"ch={ch}")
        if sr:
            info.append(f"hz={sr}")
    if br_i:
        info.append(f"bitrate={human_bps(br_i)}")
    return " | ".join(info)


def pick_stream(streams: list[dict], prompt: str, allow_none: bool = False) -> dict | None:
    if not streams:
        print("Nenhuma faixa encontrada.")
        return None
    for i, s in enumerate(streams):
        print(f"[{i}] {stream_label(s)}")
    while True:
        raw = input(prompt).strip()
        if allow_none and raw.lower() in ("n", "nao", "não", "none", ""):
            return None
        if raw.isdigit():
            i = int(raw)
            if 0 <= i < len(streams):
                return streams[i]
        print("Entrada inválida.")


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def guess_caps_by_resolution(w: int | None, h: int | None) -> tuple[str, str]:
    """
    Retorna (maxrate, bufsize) como strings tipo "5M", "10M".
    Heurística segura pra 'TV com amigos' sem explodir tamanho.
    """
    if not w or not h:
        return ("5M", "10M")

    pixels = w * h

    # Heurísticas bem conservadoras (H.264):
    # 720p ~ 3.5M, 1080p ~ 5.5M, 1440p ~ 8.5M, 4K ~ 16M
    if pixels <= 1280 * 720:
        return ("3.5M", "7M")
    if pixels <= 1920 * 1080:
        return ("3.5M", "8M")
    if pixels <= 2560 * 1440:
        return ("8.5M", "17M")
    return ("16M", "32M")


def choose_encode_mode(video_codec: str | None) -> str:
    enc_text = ffmpeg_encoders_text()

    options: list[tuple[str, str]] = []
    options.append(("cpu", "CPU (libx264) — mais lento, bom controle/qualidade"))

    if has_encoder(enc_text, "h264_nvenc"):
        options.append(("nvenc", "GPU NVIDIA (h264_nvenc) — bem mais rápido"))
    if has_encoder(enc_text, "h264_qsv"):
        options.append(("qsv", "GPU Intel Quick Sync (h264_qsv) — rápido"))
    if has_encoder(enc_text, "h264_amf"):
        options.append(("amf", "GPU AMD AMF (h264_amf) — rápido"))

    # COPY só faz sentido se o vídeo já for H.264 (ou às vezes HEVC com player, mas browser costuma sofrer).
    if video_codec == "h264":
        options.append(("copy", "COPY (sem re-encode do vídeo) — menor CPU e tamanho próximo do original (pode falhar)"))

    print("\n== MODO DE PROCESSAMENTO (VÍDEO) ==")
    for i, (_, desc) in enumerate(options):
        print(f"[{i}] {desc}")

    while True:
        raw = input("Escolha o modo [0..] (padrão 0): ").strip()
        if raw == "":
            return options[0][0]
        if raw.isdigit():
            i = int(raw)
            if 0 <= i < len(options):
                return options[i][0]
        print("Entrada inválida.")


def main():
    require_bin("ffmpeg")
    require_bin("ffprobe")

    if len(sys.argv) < 2:
        print("Uso: python hlsify.py <arquivo_video> [pasta_saida]")
        sys.exit(1)

    input_path = sys.argv[1]
    if not os.path.isfile(input_path):
        print("ERRO: arquivo não existe:", input_path)
        sys.exit(1)

    out_dir = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("hls_out")
    ensure_dir(out_dir)

    meta = ffprobe_meta(input_path)
    streams = meta.get("streams", []) or []
    fmt = meta.get("format", {}) or {}

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    if not video_streams:
        print("ERRO: não encontrei stream de vídeo.")
        sys.exit(1)
    if not audio_streams:
        print("ERRO: não encontrei stream de áudio.")
        sys.exit(1)

    v0 = video_streams[0]
    v_codec = v0.get("codec_name")
    v_w = v0.get("width")
    v_h = v0.get("height")
    v_fps = parse_fraction(v0.get("avg_frame_rate") or "")

    file_size = None
    if isinstance(fmt.get("size"), str) and fmt["size"].isdigit():
        file_size = int(fmt["size"])
    duration = None
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except Exception:
        duration = None

    # bitrate geral do container (às vezes vem)
    total_br = None
    if isinstance(fmt.get("bit_rate"), str) and fmt["bit_rate"].isdigit():
        total_br = int(fmt["bit_rate"])

    print("\n== ORIGINAL ==")
    print(f"Arquivo: {Path(input_path).resolve()}")
    print(f"Tamanho: {human_bytes(file_size)}")
    print(f"Duração: {duration/60:.2f} min" if duration else "Duração: desconhecida")
    print(f"Bitrate total: {human_bps(total_br)}")
    print("Vídeo:", stream_label(v0))

    # Sugerir caps por resolução
    maxrate_def, bufsize_def = guess_caps_by_resolution(v_w, v_h)
    print(f"Sugestão (cap bitrate p/ evitar explosão): maxrate={maxrate_def}, bufsize={bufsize_def}")

    print("\n== AUDIO (escolha a faixa dublada) ==")
    chosen_audio = pick_stream(audio_streams, "Escolha o AUDIO [0..]: ")
    assert chosen_audio is not None

    # Legenda: remover ou queimar
    print("\n== LEGENDA ==")
    burn_sub = False
    chosen_sub = None
    if sub_streams:
        mode = input("Legenda: [0] remover, [1] queimar (burn-in) uma faixa? (0/1): ").strip()
        if mode == "1":
            burn_sub = True
            print("\nEscolha qual faixa de legenda quer QUEIMAR no vídeo:")
            chosen_sub = pick_stream(sub_streams, "Escolha a LEGENDA [0..] (ou vazio/n para não): ", allow_none=True)
            if chosen_sub is None:
                burn_sub = False
    else:
        print("Nenhuma faixa de legenda detectada. (ok)")

    # Segment time
    segment_time = input("\nDuração do segmento (segundos) [padrão 4]: ").strip() or "4"

    # Encode mode
    encode_mode = choose_encode_mode(v_codec)

    # Qualidade defaults otimizados pra tamanho
    if encode_mode == "cpu":
        # CRF mais alto = menor. 25 é um bom “TV”
        q = input("\nQualidade CPU (CRF) [padrão 25]: ").strip() or "25"
    elif encode_mode in ("nvenc", "qsv", "amf"):
        # CQ/GQ mais alto = menor. 30 é bom.
        q = input("\nQualidade GPU (CQ/GQ) [padrão 30]: ").strip() or "30"
    else:
        q = ""

    # Maxrate/bufsize (cap)
    maxrate = input(f"\nmaxrate (ex: 5.5M) [padrão {maxrate_def}]: ").strip() or maxrate_def
    bufsize = input(f"bufsize (ex: 11M) [padrão {bufsize_def}]: ").strip() or bufsize_def

    # Áudio
    a_bitrate = input("\nBitrate do áudio AAC (ex: 128k/192k) [padrão 128k]: ").strip() or "128k"

    # Saídas HLS fMP4
    playlist_path = out_dir / "stream.m3u8"
    init_path = out_dir / "init.mp4"
    seg_pattern = str(out_dir / "seg_%05d.m4s")

    print("\n== SAÍDA HLS ==")
    print("Pasta:", out_dir.resolve())
    print("Playlist:", playlist_path.resolve())
    print("Segmentos:", seg_pattern)
    print("Modo:", encode_mode)
    if encode_mode != "copy":
        print("Qualidade:", q, "| cap:", maxrate, "/", bufsize)

    # Montar comando ffmpeg
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", input_path]

    # map: primeiro vídeo + audio escolhido
    cmd += ["-map", "0:v:0", "-map", f"0:{chosen_audio['index']}"]

    # remover streams de legenda do container (a gente só queima se escolher)
    cmd += ["-sn"]

    # Se queimar legenda, aplica filter (isso roda CPU; ainda dá pra usar NVENC depois)
    if burn_sub and chosen_sub is not None:
        sub_idx = chosen_sub["index"]
        cmd += ["-vf", f"subtitles='{input_path}':si={sub_idx}"]

    # Encoder vídeo
    if encode_mode == "copy":
        # copia o vídeo original (só funciona bem se já for H.264 com keyframes ok)
        cmd += ["-c:v", "copy"]
    elif encode_mode == "cpu":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "nvenc":
        # NVENC: CQ + cap (senão explode fácil)
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "qsv":
        # QSV: global_quality + cap
        cmd += ["-c:v", "h264_qsv", "-global_quality", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "amf":
        # AMF varia por driver; usa CQP simples + cap
        cmd += ["-c:v", "h264_amf", "-quality", "quality"]
        # Nem sempre AMF aceita maxrate/bufsize do mesmo jeito; tentamos mesmo assim:
        cmd += ["-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]

    # Áudio: sempre AAC (compatível)
    cmd += ["-c:a", "aac", "-b:a", a_bitrate]

    # HLS fMP4 (menor overhead que TS)
    cmd += [
        "-f", "hls",
        "-hls_time", str(segment_time),
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", str(init_path.name),
        "-hls_segment_filename", seg_pattern,
        str(playlist_path),
    ]

    print("\nComando FFmpeg:\n" + " ".join(cmd) + "\n")

    p = subprocess.Popen(cmd)
    code = p.wait()
    if code != 0:
        print("\nERRO: ffmpeg falhou.")
        sys.exit(code)

    # estimar tamanho produzido (soma arquivos)
    total_out = 0
    try:
        for f in out_dir.iterdir():
            if f.is_file():
                total_out += f.stat().st_size
    except Exception:
        pass

    print("Pronto!")
    print(f"Tamanho gerado (aprox): {human_bytes(total_out)}")
    print("\nTeste rápido local:")
    print(f"  cd {out_dir}")
    print("  python -m http.server 8080")
    print("Aí seu player HLS aponta pra: http://localhost:8080/stream.m3u8")


if __name__ == "__main__":
    main()
