# Rook Emoji Vocabulary

Rook translates yard activity into a compact emoji vocabulary. No video is transmitted — only symbols. Designed for instant recognition on a phone lock screen.

> **Philosophy:** One symbol per object class. Composite emojis only for critical anomalies (e.g. `🐕⚠️` = loose dog). Counts shown inline: `🚗 x3`.

---

## 1. Vehicles

| Emoji | YOLO Class | Notes |
| :---: | :--- | :--- |
| `🚗` | `car` | Standard passenger vehicle. **Silent** — counted in daily stats but no real-time alert when appearing alone. |
| `🚚` | `truck` | Delivery, utility, or sanitation truck |
| `🚌` | `bus` | School bus or transit |
| `🏍️` | `motorcycle` | Motorcycle or moped |
| `🚲` | `bicycle` | Bicycle. **Silent** when alone. |

> **Suppressed classes:** `train` (🚂) is fully ignored at the detection stage — no rail infrastructure in this scene. YOLO occasionally misclassifies dark, boxy vehicles at distance as trains.

---

## 2. Pedestrians

| Emoji | YOLO Class | Notes |
| :---: | :--- | :--- |
| `🚶` | `person` (1) | Single pedestrian |
| `👥` | `person` (2–3) | Small group |
| `🏟️` | `person` (4+) | Crowd or event |
| `🎒` | `backpack` | Student or hiker |
| `🧳` | `suitcase` | Traveler |
| `☂️` | `umbrella` | Pedestrian in rain |
| `📱` | `cell phone` | Pedestrian lingering on phone |

---

## 3. Yard & Recreation

| Emoji | YOLO Class | Notes |
| :---: | :--- | :--- |
| `🛹` | `skateboard` | Skateboarder |
| `⚽` | `sports ball` | Kids playing in yard |
| `🥏` | `frisbee` | Yard recreation |
| `🪁` | `kite` | Park recreation |

---

## 4. Wildlife & Anomalies

> High-scoring anomalies bypass standard cooldowns and trigger real-time email with attached image.

| Emoji | YOLO Class | Score | Notes |
| :---: | :--- | :---: | :--- |
| `🐕` | `dog` (with person) | 2 | Accompanied dog |
| `🐕⚠️` | `dog` (no person) | 2 | **Anomaly:** Loose, off-leash dog |
| `🐈` | `cat` | 10 | Feline in yard |
| `🦅` | `bird` | 15 | Hawk, owl, crow, etc. |
| `🦌` | `sheep` / `cow` | 50 | **Heuristic:** Suburban large wildlife remapped to Deer |
| `🐻` | `bear` | 100 | **Critical** — immediate email |
| `🐎` | `horse` | 50 | Equestrian activity |

| Emoji | Event | Notes |
| :---: | :--- | :--- |
| `🐕⚠️` | Loose dog (no person in scene) | Triggers real-time alert |
| `🚨🚓` | Emergency responders | Flashing lights in yard |

---

## 5. Weather & Environmental

Populated automatically from [Open-Meteo](https://open-meteo.com) (free, no API key) every 15 minutes, and from frame-level vision heuristics.

| Emoji | Source | Trigger |
| :---: | :--- | :--- |
| `☀️` | Open-Meteo WMO 0 | Clear sky |
| `⛅` | Open-Meteo WMO 2 | Partly cloudy |
| `🌦️` | Open-Meteo WMO 51/80 | Light drizzle / showers |
| `🌧️` | Open-Meteo WMO 61–65 | Rain |
| `❄️` | Open-Meteo WMO 71–77 | Snow |
| `⛈️⚡` | Open-Meteo WMO 95–99 | Thunderstorm |
| `🌫️` | Frame heuristic | High luminance + low contrast std → fog |
| `🌑` | Frame heuristic | Mean luminance < 28 → lens blocked or deep night |

> Weather emojis only appear in alerts when the WMO score bonus > 0 (i.e. notable conditions, not clear sky).

---

## 6. System Events

| Emoji | Event | Notes |
| :---: | :--- | :--- |
| `🌡️` | Thermal warning | Appended to alert at high temp |
| `🔴🔥` | Thermal shutdown | `sudo shutdown -h now` at 80°C |
| `🔋📉` | Under-voltage | ⚡ icon visible on Pi — upgrade to 5V/5A charger |

---

## Alert Scoring

Score determines whether a real-time email fires and ranks the daily digest "top event."

```
score = Σ (base_score × count^1.5) + (unique_classes × 5)
```

| Score | Action |
|---|---|
| ≥ 8  | **Slack** ping (unless silent solo class) |
| ≥ 30 | **Slack + Email** with attached image — notable event |
| ≥ 50 | **Slack + Email** — rare/critical event |

**Silent solo classes:** `car`, `bicycle` — counted in daily traffic totals but no real-time notification when appearing alone. Mixed scenes (e.g. `car + person`) are not suppressed.
