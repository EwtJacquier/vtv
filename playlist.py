#!/usr/bin/env python3
"""
playlist.py - Editor de canais VTV (formato ciclo de X dias)

Edita arquivos JSON de canais no formato:
{
  "timezone": "America/Sao_Paulo",
  "cycle_start": "2024-01-15",
  "dia_1": [
    { "start": "10:00", "id": "filme1", "duration": 6383 },
    { "id": "filme2", "duration": 6900 }
  ],
  "dia_2": [],
  ...
}

Uso:
  python playlist.py <pasta_hls> [arquivo_canal.json]
  # Ex: python playlist.py ./movies_hls ./channels/paradox.json
"""

import json
import sys
from pathlib import Path
from datetime import datetime, date

# Horário padrão de início e fim da programação
DEFAULT_START_HOUR = 7   # 07:00
DEFAULT_END_HOUR = 3     # 03:00 (do próximo dia)


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


def time_to_seconds(t: str) -> int:
    """Converte HH:MM para segundos"""
    h, m = int(t[:2]), int(t[3:])
    return h * 3600 + m * 60


def seconds_to_time(secs: int) -> str:
    """Converte segundos para HH:MM (com suporte para horários > 24h)"""
    secs = secs % (24 * 3600)  # normaliza para 24h
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h:02d}:{m:02d}"


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


def get_day_numbers(data: dict) -> list[int]:
    """Retorna lista de números de dias existentes no canal"""
    days = []
    for key in data.keys():
        if key.startswith("dia_"):
            try:
                num = int(key.split("_")[1])
                days.append(num)
            except ValueError:
                pass
    return sorted(days)


def calc_program_times(programs: list[dict]) -> list[tuple[str, str, dict]]:
    """
    Calcula horários de início e fim de cada programa.
    Retorna lista de (start_time, end_time, program)
    """
    result = []
    current_secs = DEFAULT_START_HOUR * 3600  # começa às 07:00

    for i, prog in enumerate(programs):
        # Se tem start definido, usa ele
        if "start" in prog and prog["start"]:
            current_secs = time_to_seconds(prog["start"])
        elif i == 0:
            # Primeiro programa sem start: começa às 07:00
            current_secs = DEFAULT_START_HOUR * 3600
        # Senão: começa após o anterior (current_secs já está correto)

        start_time = seconds_to_time(current_secs)
        end_secs = current_secs + prog.get("duration", 0)
        end_time = seconds_to_time(end_secs)

        result.append((start_time, end_time, prog))
        current_secs = end_secs

    return result


def print_day_programs(programs: list[dict]):
    """Mostra programas do dia com horários calculados"""
    print("\n--- Programas do Dia ---")
    if not programs:
        print("(vazio)")
        return

    timed = calc_program_times(programs)
    total_duration = 0

    for i, (start, end, prog) in enumerate(timed):
        dur = prog.get("duration", 0)
        total_duration += dur
        has_custom_start = "start" in prog and prog["start"]
        start_marker = "*" if has_custom_start else " "
        print(f"  {i+1}.{start_marker} {start} - {end} | {prog['id']} ({fmt_duration(dur)})")

    # Calcula horário de término
    if timed:
        last_end_secs = time_to_seconds(timed[-1][1])
        print(f"\n  Total: {fmt_duration(total_duration)}")
        print(f"  Término: {timed[-1][1]}")

        # Aviso se passar das 03:00
        end_limit = (DEFAULT_END_HOUR + 24) * 3600  # 03:00 = 27:00 em segundos relativos às 07:00
        first_start = time_to_seconds(timed[0][0])
        if first_start < DEFAULT_START_HOUR * 3600:
            first_start += 24 * 3600
        relative_end = last_end_secs
        if relative_end < first_start:
            relative_end += 24 * 3600

        if relative_end > end_limit:
            print("  ⚠️  Programação passa das 03:00!")

    print("\n  * = horário de início definido manualmente")


def edit_day_programs(movies: list[dict], programs: list[dict]) -> list[dict]:
    """Editor de programas de um dia"""
    while True:
        print_movies(movies)
        print_day_programs(programs)
        print("\nComandos:")
        print("  <num>        = adicionar filme por índice")
        print("  <num> HH:MM  = adicionar filme com horário específico")
        print("  u            = remover último")
        print("  r <pos>      = remover programa na posição")
        print("  t <pos> HH:MM= alterar horário de início")
        print("  c            = limpar tudo")
        print("  b            = voltar")

        cmd = input("> ").strip()

        if cmd.lower() == "b":
            return programs

        if cmd.lower() == "u":
            if programs:
                removed = programs.pop()
                print(f"Removido: {removed['id']}")
            else:
                print("Já está vazio.")
            continue

        if cmd.lower() == "c":
            programs.clear()
            print("Programação limpa.")
            continue

        # Remover por posição: r <pos>
        if cmd.lower().startswith("r "):
            try:
                pos = int(cmd.split()[1]) - 1
                if 0 <= pos < len(programs):
                    removed = programs.pop(pos)
                    print(f"Removido: {removed['id']}")
                else:
                    print("Posição inválida.")
            except (ValueError, IndexError):
                print("Use: r <posição>")
            continue

        # Alterar horário: t <pos> HH:MM
        if cmd.lower().startswith("t "):
            parts = cmd.split()
            if len(parts) >= 3:
                try:
                    pos = int(parts[1]) - 1
                    time_str = parts[2]
                    if 0 <= pos < len(programs):
                        if validate_time(time_str):
                            programs[pos]["start"] = time_str
                            print(f"Horário alterado para {time_str}")
                        else:
                            print("Horário inválido. Use HH:MM")
                    else:
                        print("Posição inválida.")
                except (ValueError, IndexError):
                    print("Use: t <posição> HH:MM")
            else:
                print("Use: t <posição> HH:MM")
            continue

        # Adicionar filme: <num> [HH:MM]
        parts = cmd.split()
        if parts and parts[0].isdigit():
            idx = int(parts[0])
            if 0 <= idx < len(movies):
                m = movies[idx]
                if m.get("error") or m.get("duration", 0) <= 0:
                    print("Filme com erro, não pode adicionar.")
                    continue

                new_prog = {"id": m["id"], "duration": m["duration"]}

                # Verifica se tem horário específico
                if len(parts) >= 2:
                    time_str = parts[1]
                    if validate_time(time_str):
                        new_prog["start"] = time_str
                    else:
                        print("Horário inválido, ignorando.")

                programs.append(new_prog)
                print(f"Adicionado: {m['id']}")
            else:
                print("Índice inválido.")
            continue

        print("Comando inválido.")


def print_all_days(data: dict):
    """Mostra resumo de todos os dias"""
    day_nums = get_day_numbers(data)

    print("\n=== DIAS DO CICLO ===")
    if not day_nums:
        print("(nenhum dia criado)")
        return

    for num in day_nums:
        key = f"dia_{num}"
        programs = data.get(key, [])

        if programs:
            timed = calc_program_times(programs)
            total = sum(p.get("duration", 0) for p in programs)
            first_start = timed[0][0] if timed else "07:00"
            last_end = timed[-1][1] if timed else "07:00"
            print(f"\n[{num}] Dia {num} | {len(programs)} filmes | {first_start}-{last_end} | {fmt_duration(total)}")

            for start, end, prog in timed:
                print(f"      {start} - {prog['id']} ({fmt_duration(prog['duration'])})")
        else:
            print(f"\n[{num}] Dia {num} | (vazio)")


def main():
    if len(sys.argv) < 2:
        print("Uso: python playlist.py <pasta_hls> [arquivo_canal.json]")
        print("  Ex: python playlist.py ./movies_hls ./channels/paradox.json")
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
        # Novo canal com estrutura de ciclo
        data = {
            "timezone": "America/Sao_Paulo",
            "cycle_start": date.today().isoformat(),
        }
        print(f"Novo canal: {channel_file}")

    # Escaneia filmes
    movies = scan_movies(hls_dir)
    print(f"Filmes encontrados: {len(movies)}")

    while True:
        print(f"\n{'='*50}")
        print(f"Canal: {channel_file.name}")
        print(f"Timezone: {data.get('timezone', 'America/Sao_Paulo')}")
        print(f"Início do ciclo: {data.get('cycle_start', 'não definido')}")
        print(f"HLS: {hls_dir}")

        day_nums = get_day_numbers(data)
        total_days = len(day_nums)
        print(f"Total de dias no ciclo: {total_days}")

        print_all_days(data)

        print("\n" + "="*50)
        print("Menu:")
        print("  n         = novo dia")
        print("  d <num>   = editar dia")
        print("  x <num>   = deletar dia")
        print("  cp <de> <para> = copiar dia")
        print("  cs        = definir cycle_start")
        print("  t         = alterar timezone")
        print("  r         = refresh filmes")
        print("  s         = salvar")
        print("  q         = sair")

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

        elif cmd == "cs":
            current = data.get("cycle_start", date.today().isoformat())
            new_date = input(f"Data início do ciclo [{current}] (YYYY-MM-DD): ").strip()
            if new_date:
                try:
                    # Valida formato
                    datetime.strptime(new_date, "%Y-%m-%d")
                    data["cycle_start"] = new_date
                    print(f"Cycle start: {new_date}")
                except ValueError:
                    print("Formato inválido. Use YYYY-MM-DD")

        elif cmd == "n":
            # Encontra próximo número disponível
            existing = get_day_numbers(data)
            next_num = 1
            if existing:
                next_num = max(existing) + 1

            key = f"dia_{next_num}"
            data[key] = []
            print(f"Criado: Dia {next_num}")

            # Já entra no editor
            data[key] = edit_day_programs(movies, data[key])

        elif cmd.startswith("d "):
            try:
                num = int(cmd.split()[1])
                key = f"dia_{num}"
                if key in data:
                    print(f"\n=== EDITANDO DIA {num} ===")
                    data[key] = edit_day_programs(movies, data.get(key, []))
                else:
                    print(f"Dia {num} não existe.")
            except (ValueError, IndexError):
                print("Use: d <número do dia>")

        elif cmd.startswith("x "):
            try:
                num = int(cmd.split()[1])
                key = f"dia_{num}"
                if key in data:
                    confirm = input(f"Deletar dia {num}? (s/n): ").strip().lower()
                    if confirm == "s":
                        del data[key]
                        print(f"Dia {num} deletado.")
                else:
                    print(f"Dia {num} não existe.")
            except (ValueError, IndexError):
                print("Use: x <número do dia>")

        elif cmd.startswith("cp "):
            parts = cmd.split()
            if len(parts) >= 3:
                try:
                    from_num = int(parts[1])
                    to_num = int(parts[2])
                    from_key = f"dia_{from_num}"
                    to_key = f"dia_{to_num}"

                    if from_key not in data:
                        print(f"Dia {from_num} não existe.")
                        continue

                    import copy
                    data[to_key] = copy.deepcopy(data[from_key])
                    print(f"Copiado dia {from_num} para dia {to_num}")
                except (ValueError, IndexError):
                    print("Use: cp <dia_origem> <dia_destino>")
            else:
                print("Use: cp <dia_origem> <dia_destino>")

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
