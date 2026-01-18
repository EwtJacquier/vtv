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
Then access `http://localhost:8080/#anos90`

### Edit channel schedules
```bash
python playlist.py ./movies_hls ./channels/anos90.json
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
├── app.js
├── channels/          # Channel JSON files (schedule per weekday)
│   └── anos90.json
├── movies_hls/        # HLS video files (subfolders with stream.m3u8)
├── playlist.py        # Schedule editor CLI
└── conversor.py       # Video to HLS converter
```

## Channel JSON Format

```json
{
  "timezone": "America/Sao_Paulo",
  "monday": [
    {
      "name": "filmes",
      "start": "07:00",
      "end": "16:00",
      "playlist": [
        { "id": "movie_folder_name", "duration": 5605 }
      ]
    }
  ],
  "tuesday": [],
  ...
}
```

- Each day of the week has an array of time windows
- Windows have start/end times (HH:MM) and a playlist of video IDs
- Video IDs correspond to folder names in `movies_hls/`
- Duration is in seconds (calculated from stream.m3u8 EXTINF tags)

## Frontend Architecture

- Uses hash routing (`/#channel_name`) for channel selection
- Loads channel JSON once, calculates schedule client-side
- Uses hls.js for HLS playback with automatic seek to current position
- Real-time updates via setInterval (1s) without server polling
- Playlists loop within their time windows

## Dependencies

- Python 3.10+
- FFmpeg/ffprobe (for conversor.py)
- hls.js (loaded via CDN in frontend)
