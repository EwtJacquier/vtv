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
- Programming day runs from 07:00 to 03:00 (next calendar day)
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
