# LifePositioningSystem
LPS - Determine where you are in life

# Setup

## Libraries

```
sudo apt install -y ffmpeg python3-gi gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav gstreamer1.0-gtk3
```

### Extras for Raspberry Pi 4

```
sudo apt install gstreamer1.0-tools
sudo apt install gstreamer1.0-gl gstreamer1.0-gtk3
```

## Keyboard modes

Press **V** while `c18/lps-rpi4.py` is running to toggle Voyage mode. While
enabled, scheduled actions and regular videos are suspended. The application
cycles through the PNG files in
`/home/tme520/Videos/LPS/lps-basepack/slideshow/mystery/` every 10 seconds and
plays the MP3 files in `/home/tme520/Music/LPS/` sequentially. Both lists loop
back to their first item after reaching the end.
