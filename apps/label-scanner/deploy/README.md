# Running it in the warehouse

## The app as a service

```bash
sudo cp deploy/label-scanner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now label-scanner
sudo systemctl status label-scanner
```

Adjust `User` and the paths in the unit if the checkout does not live in
`/home/pi/tiltil-data`.

## The touch screen in kiosk mode

Full screen, no address bar, no screen blanking. On a Pi with Chromium:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/label-scanner.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Label scanner
Exec=chromium-browser --kiosk --incognito --noerrdialogs --disable-infobars \
  --disable-pinch --overscroll-history-navigation=0 http://localhost:8000
X-GNOME-Autostart-enabled=true
EOF
```

Stop the screen from going to sleep:

```bash
sudo apt install -y xscreensaver   # then disable blanking in its settings
# or, on a bare X session:
xset s off; xset -dpms; xset s noblank
```

`--overscroll-history-navigation=0` matters on a touch screen: without it a
horizontal swipe navigates back mid-scan.

## The barcode scanner

Any USB scanner in keyboard-wedge mode works — that is the default on most.
The only requirement is that it sends a carriage return after the code; the scan
screen submits on Enter. If your packing slips carry the order number as a
Code128 barcode, nothing else needs configuring.

## Checks after install

```bash
curl localhost:8000/api/health     # credentials + print backend
curl localhost:8000/api/printers   # printer ids from the Print Client
```
