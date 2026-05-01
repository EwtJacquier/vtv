# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VTV is a web-based TV streaming system that plays HLS videos according to scheduled programming. It consists of:

- **Web frontend** - Pure HTML/CSS/JS video player with channel menu (root)
- **playlist.py** - CLI tool to edit channel schedules
- **conversor.py** - Converts videos to HLS format using FFmpeg

## Running the Application

### Start the web server (static files)
```bash
cd /home/ewerton/projects/vtv
python -m http.server 8080
```
Then access `http://localhost:8080/?channel=paradox`

### Edit channel schedules
```bash
python playlist.py ./movies_hls ./channels/paradox.json
```

### Convert video to HLS
```bash
python conversor.py <video_file> ./movies_hls/<output_name>
```

## Directory Structure

```
vtv/
├── index.html         # Web frontend
├── style.css
├── vtv.js
├── channels/          # Channel JSON files (cycle of X days)
│   └── paradox.json
├── movies_hls/        # HLS video files (subfolders with stream.m3u8)
├── playlist.py        # Schedule editor CLI
└── conversor.py       # Video to HLS converter
```

## Channel JSON Format

```json
{
  "timezone": "America/Sao_Paulo",
  "cycle_start": "2024-01-15",
  "dia_1": [
    { "start": "10:00", "id": "movie_folder_name", "duration": 5605 },
    { "id": "another_movie", "duration": 6000 }
  ],
  "dia_2": [],
  ...
}
```

- **cycle_start**: Date when the cycle started (YYYY-MM-DD), used to calculate current cycle day
- **dia_X**: Programming for day X of the cycle (1-indexed)
- Each day has an array of movies with:
  - `id`: Folder name in `movies_hls/`
  - `duration`: Duration in seconds (from stream.m3u8 EXTINF tags)
  - `start` (optional): Start time (HH:MM). If omitted:
    - First movie of the day starts at 07:00
    - Subsequent movies start immediately after the previous one
- Programming day runs from 07:00 to 03:00 (next calendar day) by default, but **each channel has its own start time** defined by the `start` field on the first item of each `dia_X`. Known starts:
  - `animetv`: 11:00 → 09:00 next day (22h slot)
  - `paradox`: 18:00
  - `afterDark`: 18:30
  - Other channels (`epictv`, `hokkaido`, `marathon`, `neverland`, `rewindtv`, `superHero`): check the `start` on `dia_1[0]`
- When all days in the cycle are complete, it loops back to dia_1

## Frontend Architecture

- Uses query param routing (`?channel=name`) for channel selection
- Loads channel JSON once, calculates schedule client-side
- Calculates current cycle day based on `cycle_start` date
- Uses hls.js for HLS playback with automatic seek to current position
- Real-time updates via setInterval (1s) without server polling
- EPG (Electronic Program Guide) shows 24h of programming

## Dependencies

- Python 3.10+
- FFmpeg/ffprobe (for conversor.py)
- hls.js (loaded via CDN in frontend)

## Programming Logic for Channel Schedules

When designing or restructuring a channel's schedule (the `dia_X` arrays), apply these principles in order:

1. **Size the cycle to the content.** Sum all unique content durations, divide by the daily slot length for that channel (e.g. 22h for animetv 11:00→09:00; paradox starts 18:00; afterDark starts 18:30 — each channel has its own slot length, look at the `start` of `dia_1[0]`). The result is the natural number of days where each title plays roughly 2× per cycle. Don't pad the cycle with more days than the content supports — it forces over-repetition.

2. **One theme per day.** Each `dia_X` should have a clear identity (e.g. "Shounen Aventura", "Dragon Ball Day", "Cinema Autoral"). The audience reads the day, not just the next slot. Avoid days that feel like the previous one with minor swaps.

3. **Max 2 plays per title per cycle.** Anything more and repetition becomes obvious. Long, autoral, or self-contained pieces (Ghibli features, Mononoke series, etc.) can play only 1× — they don't need rotation.

4. **When a title repeats, it must land in a different time slot.** Treat the day as five periods: morning (11:00–15:00), afternoon (15:00–18:00), prime (18:00–22:00), night (22:00–02:00), late-night (02:00–07:00). The same title playing the same slot on multiple days is exactly the "estou repetindo um monte de coisa" failure mode.

5. **Don't open every day with the same block.** The biggest perceived-repetition trap is the opening sequence. If `pokemon_2000` opens day 1 at 11:00, it should appear in a non-morning slot on its second play (e.g. evening prime).

6. **Fill each day close to the slot length.** Aim for ~20–22h on a 22h channel. Small gaps near the end (1–2h) are fine; large gaps mid-day are not (they were the symptom of the old animetv where every day had a 2h gap between the morning block and the 18:00 anchor).

7. **Order by mood arc, not just availability.** Within a day: lighter content earlier, heavier/longer/auteur in prime and night. Madrugada is the natural place for repeats and slow-burn long films.

8. **Classify each title by visibility tier — and respect it.** Before scheduling, tag every title:
   - **Daytime-friendly** — clássicos infantis, comédia leve, nostálgico de família (Pokemon, Digimon, Sakura, FMA shorts, Naruto). Should play at least 1× before 18:00.
   - **Mixed** — shounen action, aventura, cyberpunk acessível, Ghibli aventura (Yu Yu Hakusho, Inu Yasha, Cavaleiros, Street Fighter, Cobra, Steamboy, Cowboy Bebop, Ghost in the Shell, Chihiro). Should play at least 1× in the **visible window 14:00–22:00**.
   - **Night-only** — cyberpunk pesado, horror, drama denso, autoral pesado (Akira, Evangelion End, Mononoke series, Tumulo dos Vagalumes). Should play **only after 22:00**.

9. **No mixed-tier title with both plays buried in 02:00–07:00.** This is the "buried title" failure mode — the title is technically on the schedule but nobody ever sees it. If a title plays 2×, at least one of those plays must start before 22:00. Titles like Steamboy and Space Cobra deserve prime/late-afternoon slots, not just madrugada.

When asked to "fix repetition" on a channel, the diagnosis is almost always: same titles in the same opening slots across multiple days. The fix is principles 2–5 applied together. When asked "why does X never play?", the diagnosis is principle 9 violated — the title only exists at 03:00 AM. The fix is to give it a visible-window slot on at least one day of the cycle.
