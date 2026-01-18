#!/usr/bin/env python3
"""
playlist.py - Editor de canais VTV

Edita arquivos JSON de canais no formato:
{
  "timezone": "America/Sao_Paulo",
  "monday": [ { "name": "...", "start": "HH:MM", "end": "HH:MM", "playlist": [...] } ],
  "tuesday": [],
  ...
}

Uso:
  python playlist.py <pasta_hls> [arquivo_canal.json]
  # Ex: python playlist.py ./movies_hls ./channels/anos90.json

Comandos principais:
  l  listar dias da semana
  d  selecionar dia para editar
  s  salvar
  q  sair sem salvar
"""

import json
import sys
from pathlib import Path

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYS_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


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


def scan_movies(hls_dir: Path) -> list[dict]:
    """Escaneia subpastas com stream.m3u8 e retorna lista de filmes"""
    movies = []
    if not hls_dir.exists():
        return movies
    for sub in sorted(hls_dir.iterdir()):
        if not sub.is_dir():
            continue
        m3u8 = sub / "stream.m3u8"
        if m3u8.exists():
            try:
                dur = parse_m3u8_duration(m3u8)
                movies.append({
                    "id": sub.name,
                    "duration": int(round(dur)),
                })
            except Exception as e:
                movies.append({
                    "id": sub.name,
                    "duration": 0,
                    "error": str(e),
                })
    return movies


def fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def validate_time(t: str) -> bool:
    if len(t) != 5 or t[2] != ":":
        return False
    try:
        h, m = int(t[:2]), int(t[3:])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False


def print_movies(movies: list[dict]):
    print("\n=== FILMES DISPONÍVEIS ===")
    if not movies:
        print("(nenhum encontrado)")
        return
    for i, m in enumerate(movies):
        if m.get("error"):
            print(f"[{i}] {m['id']} (ERRO: {m['error']})")
        else:
            print(f"[{i}] {m['id']} ({fmt_duration(m['duration'])})")


def print_windows(windows: list[dict]):
    print("\n=== JANELAS DO DIA ===")
    if not windows:
        print("(nenhuma)")
        return
    for i, w in enumerate(windows):
        name = w.get("name", "sem nome")
        start = w.get("start", "??:??")
        end = w.get("end", "??:??")
        pl = w.get("playlist", [])
        total = sum(p.get("duration", 0) for p in pl)
        print(f"[{i}] {name} | {start}-{end} | {len(pl)} itens | {fmt_duration(total)}")


def print_playlist(playlist: list[dict]):
    print("\n--- Playlist ---")
    if not playlist:
        print("(vazia)")
        return
    total = 0
    for i, p in enumerate(playlist):
        dur = p.get("duration", 0)
        total += dur
        print(f"  {i+1}. {p['id']} ({fmt_duration(dur)})")
    print(f"  Total: {fmt_duration(total)}")


def edit_playlist(movies: list[dict], playlist: list[dict]) -> list[dict]:
    """Editor interativo de playlist"""
    while True:
        print_movies(movies)
        print_playlist(playlist)
        print("\nComandos: <num>=adicionar | u=remover último | c=limpar | b=voltar")
        cmd = input("> ").strip().lower()

        if cmd == "b":
            return playlist
        if cmd == "u":
            if playlist:
                removed = playlist.pop()
                print(f"Removido: {removed['id']}")
            else:
                print("Já está vazia.")
        elif cmd == "c":
            playlist.clear()
            print("Playlist limpa.")
        elif cmd.isdigit():
            idx = int(cmd)
            if 0 <= idx < len(movies):
                m = movies[idx]
                if m.get("error") or m.get("duration", 0) <= 0:
                    print("Filme com erro, não pode adicionar.")
                else:
                    playlist.append({"id": m["id"], "duration": m["duration"]})
                    print(f"Adicionado: {m['id']}")
            else:
                print("Índice inválido.")
        else:
            print("Comando inválido.")


def edit_day(movies: list[dict], windows: list[dict]) -> list[dict]:
    """Editor de janelas de um dia"""
    while True:
        print_windows(windows)
        print("\nComandos: n=nova janela | e=editar playlist | d=deletar | b=voltar")
        cmd = input("> ").strip().lower()

        if cmd == "b":
            return windows

        if cmd == "n":
            name = input("Nome da janela: ").strip() or "programa"
            start = input("Início (HH:MM): ").strip()
            if not validate_time(start):
                print("Horário inválido.")
                continue
            end = input("Fim (HH:MM): ").strip()
            if not validate_time(end):
                print("Horário inválido.")
                continue
            windows.append({
                "name": name,
                "start": start,
                "end": end,
                "playlist": []
            })
            print("Janela criada.")

        elif cmd == "e":
            if not windows:
                print("Não há janelas.")
                continue
            print_windows(windows)
            try:
                idx = int(input("Qual janela? ").strip())
                if 0 <= idx < len(windows):
                    windows[idx]["playlist"] = edit_playlist(
                        movies,
                        windows[idx].get("playlist", [])
                    )
                else:
                    print("Índice inválido.")
            except ValueError:
                print("Entrada inválida.")

        elif cmd == "d":
            if not windows:
                print("Não há janelas.")
                continue
            print_windows(windows)
            try:
                idx = int(input("Qual deletar? ").strip())
                if 0 <= idx < len(windows):
                    removed = windows.pop(idx)
                    print(f"Deletado: {removed.get('name')}")
                else:
                    print("Índice inválido.")
            except ValueError:
                print("Entrada inválida.")

        else:
            print("Comando inválido.")


def copy_day(data: dict):
    """Copia programação de um dia para outros"""
    print("\n=== COPIAR DIA ===")
    print("Dias disponíveis:")
    for i, d in enumerate(DAYS):
        windows = data.get(d, [])
        print(f"[{i}] {DAYS_PT[i]} ({d}) - {len(windows)} janelas")

    try:
        src_idx = int(input("\nCopiar DE qual dia? [0-6]: ").strip())
        if not 0 <= src_idx <= 6:
            print("Índice inválido.")
            return

        dest_input = input("Copiar PARA quais dias? (ex: 1,2,3 ou 'todos'): ").strip().lower()

        if dest_input == "todos":
            dest_indices = [i for i in range(7) if i != src_idx]
        else:
            dest_indices = [int(x.strip()) for x in dest_input.split(",") if x.strip().isdigit()]
            dest_indices = [i for i in dest_indices if 0 <= i <= 6 and i != src_idx]

        if not dest_indices:
            print("Nenhum destino válido.")
            return

        src_day = DAYS[src_idx]
        src_windows = data.get(src_day, [])

        # Deep copy
        import copy
        for idx in dest_indices:
            dest_day = DAYS[idx]
            data[dest_day] = copy.deepcopy(src_windows)
            print(f"Copiado para {DAYS_PT[idx]}")

        print("Cópia concluída.")

    except ValueError:
        print("Entrada inválida.")


def main():
    if len(sys.argv) < 2:
        print("Uso: python playlist.py <pasta_hls> [arquivo_canal.json]")
        print("  Ex: python playlist.py ./movies_hls ./channels/anos90.json")
        sys.exit(1)

    hls_dir = Path(sys.argv[1]).resolve()
    if not hls_dir.exists():
        print(f"ERRO: pasta não existe: {hls_dir}")
        sys.exit(1)

    # Arquivo do canal
    if len(sys.argv) >= 3:
        channel_file = Path(sys.argv[2]).resolve()
    else:
        channel_file = Path("./channels/canal.json").resolve()

    # Carrega ou cria dados do canal
    if channel_file.exists():
        data = json.loads(channel_file.read_text(encoding="utf-8"))
        print(f"Carregado: {channel_file}")
    else:
        data = {
            "timezone": "America/Sao_Paulo",
            "monday": [],
            "tuesday": [],
            "wednesday": [],
            "thursday": [],
            "friday": [],
            "saturday": [],
            "sunday": []
        }
        print(f"Novo canal: {channel_file}")

    # Escaneia filmes
    movies = scan_movies(hls_dir)
    print(f"Filmes encontrados: {len(movies)}")

    while True:
        print(f"\n{'='*40}")
        print(f"Canal: {channel_file.name}")
        print(f"Timezone: {data.get('timezone', 'America/Sao_Paulo')}")
        print(f"HLS: {hls_dir}")

        # Resumo dos dias
        print("\nDias:")
        for i, d in enumerate(DAYS):
            windows = data.get(d, [])
            total_items = sum(len(w.get("playlist", [])) for w in windows)
            print(f"  [{i}] {DAYS_PT[i]:10} - {len(windows)} janelas, {total_items} itens")

        print("\nMenu: d=editar dia | c=copiar dia | t=timezone | r=refresh | s=salvar | q=sair")
        cmd = input("> ").strip().lower()

        if cmd == "q":
            print("Saindo sem salvar.")
            return

        if cmd == "r":
            movies = scan_movies(hls_dir)
            print(f"Refresh: {len(movies)} filmes")

        elif cmd == "t":
            tz = input(f"Timezone [{data.get('timezone')}]: ").strip()
            if tz:
                data["timezone"] = tz
                print(f"Timezone: {tz}")

        elif cmd == "c":
            copy_day(data)

        elif cmd == "d":
            try:
                idx = int(input("Qual dia? [0-6]: ").strip())
                if 0 <= idx <= 6:
                    day = DAYS[idx]
                    print(f"\n=== {DAYS_PT[idx].upper()} ({day}) ===")
                    data[day] = edit_day(movies, data.get(day, []))
                else:
                    print("Índice inválido.")
            except ValueError:
                print("Entrada inválida.")

        elif cmd == "s":
            # Garante que o diretório existe
            channel_file.parent.mkdir(parents=True, exist_ok=True)
            channel_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"Salvo: {channel_file}")

        else:
            print("Comando inválido.")


if __name__ == "__main__":
    main()
