# app/

Software that runs on the Raspberry Pi 5.

## Scripts

| File | Purpose | Where to Run |
|------|---------|--------------|
| `setup_pi.sh` | One-shot Pi setup — OS hardening, Arducam driver, Python venv, Tailscale | On the Pi |
| `frame_test.py` | FRAME viewfinder + YOLO benchmark + optional SMS | On the Pi |
| `deploy_to_pi.sh` | SCP helper to push scripts to the Pi | From your Mac |

## Deployment

```bash
# From your Mac
bash deploy_to_pi.sh rook@rook.local

# SSH in
ssh rook@rook.local
bash ~/setup_pi.sh
sudo reboot

# After reboot
source ~/rook-env/bin/activate
python3 ~/frame_test.py --benchmark --sms
```

## Environment Variables

Create `~/rook-env/.env` on the Pi:

```
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1XXXXXXXXXX
NOTIFY_TO_NUMBER=+1XXXXXXXXXX
```

> ⚠️ Never commit `.env` to version control.
