## INIT
python3 server.py
cloudflared tunnel run vtv

## SERVICES
sudo systemctl start vtv-server vtv-cloudflared
sudo systemctl stop vtv-server vtv-cloudflared

## LOG
journalctl -u vtv-server -f

## TO CONVERT VIDEO
python3 conversor.py /home/ewerton/Downloads/

## TO EXTRACT SUBS
python3 extract_sub.py /home/ewerton/Downloads/

## TO FIX BROKEN DRIVES
sudo ntfsfix /dev/sdb1
sudo mount -t ntfs-3g /dev/sdb1 /media/ewerton/Animes

## MY HDD
python3 conversor.py /media/ewerton/HD\ EWERTON\ -\ 01/Animes/Fullmetal\ Alchemist/02\ -\ Corpos\ condenado.mkv movies_hls/full_metal_alchemist_

## Remove SRT HTML REGEX
<[^>]+>