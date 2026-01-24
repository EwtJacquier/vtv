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

# Importar função de thumbnail
from thumbnail import generate_thumbnail


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


def parse_timestamp(ts: str) -> float | None:
    """
    Converte timestamp HH:MM:SS ou MM:SS para segundos.
    Ex: "01:30:00" -> 5400.0, "10:30" -> 630.0
    """
    ts = ts.strip()
    if not ts:
        return None
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return float(h) * 3600 + float(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return float(m) * 60 + float(s)
        else:
            return float(ts)
    except ValueError:
        return None


def is_image_subtitle(codec_name: str | None) -> bool:
    """
    Retorna True se o codec de legenda é baseado em imagem (PGS, DVD, DVB).
    Esses tipos precisam do filtro overlay ao invés de subtitles.
    """
    if not codec_name:
        return False
    codec_lower = codec_name.lower()
    image_codecs = {
        "hdmv_pgs_subtitle",  # Blu-ray PGS
        "pgssub",
        "pgs",
        "dvd_subtitle",       # DVD VOBSub
        "dvdsub",
        "dvb_subtitle",       # DVB
        "dvbsub",
        "xsub",               # DivX XSUB
    }
    # Checa exato ou se contém "pgs" ou "dvd_sub" ou "dvb_sub"
    if codec_lower in image_codecs:
        return True
    if "pgs" in codec_lower or "dvdsub" in codec_lower or "dvbsub" in codec_lower:
        return True
    return False


def get_stream_start_time(s: dict) -> float:
    """
    Retorna o start_time da stream em segundos.
    Se não existir, retorna 0.0.
    """
    st = s.get("start_time")
    if st is None:
        return 0.0
    try:
        return float(st)
    except (ValueError, TypeError):
        return 0.0


def get_first_packet_time(input_path: str, stream_index: int) -> float | None:
    """
    Obtém o timestamp (dts_time) do primeiro packet de uma stream específica.
    Isso é mais confiável que start_time para detectar quando a stream realmente começa.
    """
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-v", "error",
        "-select_streams", str(stream_index),
        "-show_packets",
        "-read_intervals", "%+#1",  # Só o primeiro packet
        "-print_format", "json",
        input_path,
    ]
    p = run(cmd)
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
        packets = data.get("packets", [])
        if packets:
            # Preferir dts_time, fallback para pts_time
            dts = packets[0].get("dts_time")
            if dts is not None:
                return float(dts)
            pts = packets[0].get("pts_time")
            if pts is not None:
                return float(pts)
    except (json.JSONDecodeError, ValueError, KeyError, IndexError):
        pass
    return None


def stream_label(s: dict) -> str:
    idx = s.get("index")
    codec = s.get("codec_name")
    ctype = s.get("codec_type")
    tags = s.get("tags", {}) or {}
    lang = tags.get("language", "und")
    title = tags.get("title", "")
    br = s.get("bit_rate")
    br_i = int(br) if isinstance(br, str) and br.isdigit() else (br if isinstance(br, int) else None)
    start_time = get_stream_start_time(s)

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
    # Mostrar start_time se não for zero
    if start_time > 0.1:
        info.append(f"start={start_time:.2f}s")
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
        return ("5M", "12M")
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

    # Detectar diferença de start_time entre vídeo e áudio usando timestamps reais dos packets
    print("\nAnalisando timestamps das streams...")
    video_start = get_first_packet_time(input_path, v0["index"])
    audio_start = get_first_packet_time(input_path, chosen_audio["index"])

    # Fallback para start_time da stream se não conseguir ler packets
    if video_start is None:
        video_start = get_stream_start_time(v0)
    if audio_start is None:
        audio_start = get_stream_start_time(chosen_audio)

    cut_start_sec = 0.0  # Se > 0, corta o início do vídeo

    if audio_start > video_start + 0.1:  # Áudio começa depois do vídeo (margem de 100ms)
        delay_sec = audio_start - video_start
        print(f"\nℹ INFO: O áudio escolhido começa {delay_sec:.2f}s depois do vídeo.")
        print(f"        Video start: {video_start:.3f}s | Audio start: {audio_start:.3f}s")
        print("        O resultado manterá o mesmo comportamento (áudio atrasado).")
        print("  [0] Manter assim (padrão)")
        print("  [1] Cortar o vídeo para sincronizar (perde início do vídeo)")
        sync_choice = input("Escolha [0/1] (padrão 0): ").strip() or "0"

        if sync_choice == "1":
            cut_start_sec = delay_sec
            print(f"→ O vídeo será cortado em {delay_sec:.2f}s para sincronizar")

    # Legenda: remover ou queimar
    print("\n== LEGENDA ==")
    burn_sub = False
    chosen_sub = None
    sub_start_sec = None
    sub_end_sec = None
    if sub_streams:
        mode = input("Legenda: [0] remover, [1] queimar (burn-in) uma faixa? (0/1): ").strip()
        if mode == "1":
            burn_sub = True
            print("\nEscolha qual faixa de legenda quer QUEIMAR no vídeo:")
            chosen_sub = pick_stream(sub_streams, "Escolha a LEGENDA [0..] (ou vazio/n para não): ", allow_none=True)
            if chosen_sub is None:
                burn_sub = False
            else:
                # Detectar tipo de legenda
                sub_codec = chosen_sub.get("codec_name", "")
                if is_image_subtitle(sub_codec):
                    print(f"→ Legenda de IMAGEM detectada ({sub_codec}) - será usada overlay")
                else:
                    print(f"→ Legenda de TEXTO detectada ({sub_codec}) - será usada subtitles")

                # Perguntar se quer burn-in completo ou parcial
                print("\nModo de burn-in:")
                print("[0] Completo (vídeo inteiro)")
                print("[1] Parcial (de XX:XX:XX até XX:XX:XX)")
                burn_mode = input("Escolha [0/1] (padrão 0): ").strip() or "0"
                if burn_mode == "1":
                    print("\nDigite os timestamps no formato HH:MM:SS ou MM:SS")
                    start_input = input("Início (ex: 00:05:00): ").strip()
                    end_input = input("Fim (ex: 01:30:00): ").strip()
                    sub_start_sec = parse_timestamp(start_input)
                    sub_end_sec = parse_timestamp(end_input)
                    if sub_start_sec is None or sub_end_sec is None:
                        print("Timestamp inválido, usando burn-in completo.")
                        sub_start_sec = None
                        sub_end_sec = None
                    elif sub_start_sec >= sub_end_sec:
                        print("Início deve ser menor que fim, usando burn-in completo.")
                        sub_start_sec = None
                        sub_end_sec = None
                    else:
                        print(f"→ Legenda será queimada de {start_input} até {end_input} ({sub_end_sec - sub_start_sec:.0f}s)")
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
        q = input("\nQualidade GPU (CQ/GQ) [padrão 27]: ").strip() or "27"
    else:
        q = ""

    # Maxrate/bufsize (cap)
    maxrate = input(f"\nmaxrate (ex: 5.5M) [padrão {maxrate_def}]: ").strip() or maxrate_def
    bufsize = input(f"bufsize (ex: 11M) [padrão {bufsize_def}]: ").strip() or bufsize_def

    # Áudio
    a_bitrate = input("\nBitrate do áudio AAC (ex: 128k/192k) [padrão 128k]: ").strip() or "128k"

    # Ajuste de volume (dB)
    volume_db = input("\nAjuste de volume em dB (ex: 3 para +3dB, -5 para -5dB) [padrão 0]: ").strip() or "0"
    try:
        volume_db_val = float(volume_db)
    except ValueError:
        print("Valor inválido, usando 0dB")
        volume_db_val = 0.0

    # Forçar 8-bit (útil para vídeos 10-bit com encoders que não suportam)
    force_8bit = False
    if encode_mode in ("nvenc", "qsv", "amf"):
        force_8bit_input = input("\nForçar conversão para 8-bit? (necessário se vídeo 10-bit) [s/N]: ").strip().lower()
        force_8bit = force_8bit_input in ("s", "sim", "y", "yes")

    # Upscaling
    upscale_res = None
    if encode_mode != "copy":
        print(f"\n== UPSCALING (resolução atual: {v_w}x{v_h}) ==")
        print("[0] Manter resolução original")
        print("[1] 1280x720 (720p)")
        print("[2] 1920x1080 (1080p)")
        print("[3] 2560x1440 (1440p)")
        print("[4] 3840x2160 (4K)")
        print("[5] Personalizado")
        upscale_choice = input("Escolha [0-5] (padrão 0): ").strip() or "0"

        resolutions = {
            "1": (1280, 720),
            "2": (1920, 1080),
            "3": (2560, 1440),
            "4": (3840, 2160),
        }

        if upscale_choice in resolutions:
            upscale_res = resolutions[upscale_choice]
        elif upscale_choice == "5":
            custom = input("Digite a resolução (ex: 1920x1080): ").strip()
            if "x" in custom:
                try:
                    cw, ch = custom.split("x")
                    upscale_res = (int(cw), int(ch))
                except ValueError:
                    print("Formato inválido, mantendo original.")

        if upscale_res:
            # Verificar se é realmente upscale ou downscale
            if upscale_res[0] * upscale_res[1] > v_w * v_h:
                print(f"→ Upscale: {v_w}x{v_h} → {upscale_res[0]}x{upscale_res[1]}")
            elif upscale_res[0] * upscale_res[1] < v_w * v_h:
                print(f"→ Downscale: {v_w}x{v_h} → {upscale_res[0]}x{upscale_res[1]}")
            else:
                print("→ Mesma resolução, ignorando.")
                upscale_res = None

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
    if upscale_res:
        print(f"Resolução: {v_w}x{v_h} → {upscale_res[0]}x{upscale_res[1]} (lanczos)")
    if volume_db_val != 0.0:
        print(f"Volume: {'+' if volume_db_val > 0 else ''}{volume_db_val}dB")

    # Montar comando ffmpeg
    cmd = ["ffmpeg", "-hide_banner", "-y"]

    # Aumentar analyzeduration e probesize para legendas PGS
    cmd += ["-analyzeduration", "100M", "-probesize", "100M"]

    # Se opção de corte foi escolhida, adicionar -ss antes do input
    if cut_start_sec > 0:
        cmd += ["-ss", str(cut_start_sec)]

    cmd += ["-i", input_path]

    # Monta filtros de vídeo (scale + legendas se necessário)
    vf_filters = []
    use_filter_complex = False
    filter_complex_str = ""

    # Detectar se legenda é de imagem (PGS, DVD, etc.)
    sub_is_image = burn_sub and chosen_sub is not None and is_image_subtitle(chosen_sub.get("codec_name"))

    # Upscaling/downscaling
    if upscale_res:
        target_w, target_h = upscale_res
        # Usa lanczos para melhor qualidade em upscale
        vf_filters.append(f"scale={target_w}:{target_h}:flags=lanczos")

    # Queimar legenda
    if burn_sub and chosen_sub is not None:
        sub_abs_idx = chosen_sub["index"]  # índice absoluto no arquivo
        # Calcular índice relativo dentro das streams de legenda
        sub_rel_idx = 0
        for s in sub_streams:
            if s["index"] < sub_abs_idx:
                sub_rel_idx += 1

        if sub_is_image:
            # Legendas de imagem (PGS, DVD): usar filter_complex com overlay
            use_filter_complex = True

            # Montar enable se for parcial
            enable_str = ""
            if sub_start_sec is not None and sub_end_sec is not None:
                enable_str = f"=enable='between(t\\,{sub_start_sec}\\,{sub_end_sec})'"

            # Montar filter_complex
            if vf_filters:
                # Com scale: [0:v]scale[v];[v][0:s:X]overlay[out]
                scale_filter = ",".join(vf_filters)
                filter_complex_str = f"[0:v]{scale_filter}[v];[v][0:s:{sub_rel_idx}]overlay{enable_str}[out]"
            else:
                # Sem scale: [0:v][0:s:X]overlay[out]
                filter_complex_str = f"[0:v][0:s:{sub_rel_idx}]overlay{enable_str}[out]"
        else:
            # Legendas de texto (SRT, ASS, etc.): usar filtro subtitles
            # Escapar caracteres especiais no path para o filtro subtitles
            escaped_path = input_path.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
            if sub_start_sec is not None and sub_end_sec is not None:
                # Burn-in parcial: usa enable para ativar apenas no trecho
                vf_filters.append(f"subtitles='{escaped_path}':si={sub_rel_idx}:enable='between(t,{sub_start_sec},{sub_end_sec})'")
            else:
                # Burn-in completo
                vf_filters.append(f"subtitles='{escaped_path}':si={sub_rel_idx}")

    # Aplica filtros e mapeamento
    if use_filter_complex:
        cmd += ["-filter_complex", filter_complex_str]
        cmd += ["-map", "[out]", "-map", f"0:{chosen_audio['index']}"]
    else:
        # Mapeamento normal: vídeo + áudio
        cmd += ["-map", "0:v:0", "-map", f"0:{chosen_audio['index']}"]
        # Só adiciona -vf se houver filtros (scale, subtitles)
        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]

    # Filtros de áudio (apenas volume se necessário)
    if volume_db_val != 0.0:
        cmd += ["-af", f"volume={volume_db_val}dB"]

    # remover streams de legenda do container (a gente só queima se escolher)
    cmd += ["-sn"]

    # Encoder vídeo
    if encode_mode == "copy":
        # copia o vídeo original (só funciona bem se já for H.264 com keyframes ok)
        cmd += ["-c:v", "copy"]
    elif encode_mode == "cpu":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "nvenc":
        # NVENC: CQ + cap (senão explode fácil)
        if force_8bit:
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "qsv":
        # QSV: global_quality + cap
        if force_8bit:
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += ["-c:v", "h264_qsv", "-global_quality", str(q),
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    elif encode_mode == "amf":
        # AMF varia por driver; usa CQP simples + cap
        if force_8bit:
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += ["-c:v", "h264_amf", "-quality", "quality"]
        # Nem sempre AMF aceita maxrate/bufsize do mesmo jeito; tentamos mesmo assim:
        cmd += ["-maxrate", str(maxrate), "-bufsize", str(bufsize)]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-maxrate", str(maxrate), "-bufsize", str(bufsize)]

    # Áudio: sempre AAC estéreo (compatível com browsers/MSE)
    cmd += ["-c:a", "aac", "-ac", "2", "-b:a", a_bitrate]

    # HLS fMP4 (menor overhead que TS)
    cmd += [
        "-movflags", "+delay_moov",
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

    # Gerar thumbnail automaticamente
    print("\nGerando thumbnail (minuto 10)...")
    if generate_thumbnail(out_dir):
        print("Thumbnail gerado: thumb.jpg")
    else:
        print("Aviso: Não foi possível gerar thumbnail")

    # Adicionar ao catalog.json se não existir
    catalog_path = out_dir.parent / "catalog.json"
    movie_id = out_dir.name
    try:
        if catalog_path.exists():
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        else:
            catalog = []

        # Verificar se já existe
        existing_ids = {m["id"] for m in catalog if isinstance(m, dict) and "id" in m}
        if movie_id not in existing_ids:
            catalog.append({"id": movie_id})
            catalog.sort(key=lambda m: m.get("id", ""))
            with open(catalog_path, "w", encoding="utf-8") as f:
                json.dump(catalog, f, indent=2, ensure_ascii=False)
            print(f"Adicionado ao catálogo: {movie_id}")
        else:
            print(f"Já existe no catálogo: {movie_id}")
    except Exception as e:
        print(f"Aviso: Não foi possível atualizar catalog.json: {e}")

    print("\nTeste rápido local:")
    print(f"  cd {out_dir}")
    print("  python -m http.server 8080")
    print("Aí seu player HLS aponta pra: http://localhost:8080/stream.m3u8")


if __name__ == "__main__":
    main()
