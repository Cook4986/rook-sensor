# Rook Emoji Vocabulary

Rook translates real-world activity into a compact, low-bandwidth emoji vocabulary. This ensures privacy (no video transmission) and rapid visual parsing.

*Note: In v1.1, the engine was refactored to prioritize pure, single-symbol emojis. Composite emojis (e.g. Dog + Walker) are broken apart into their base components (`🐕 x1 🚶 x1`) for at-a-glance legibility. Secondary symbols are only used for critical anomaly clarification.*

## 1. Core Logistics & Vehicles
| Emoji | YOLO Class | Translation / Context |
| :---: | :--- | :--- |
| `🚗` | `car` | Standard passenger vehicle |
| `🚚` | `truck` | Delivery, utility, or sanitation truck |
| `🚌` | `bus` | School bus or public transit |
| `🏍️` | `motorcycle` | Motorcycle or moped |
| `🚲` | `bicycle` | Bicycle passing by |

## 2. Pedestrian Patterns
| Emoji | YOLO Class | Translation / Context |
| :---: | :--- | :--- |
| `🚶` | `person` | Single pedestrian |
| `👥` | `person` (>1) | Small group or couple passing by |
| `🏟️` | `person` (>3) | Large crowd or heavy foot traffic |
| `🎒` | `backpack` | Student or hiker |
| `🧳` | `suitcase` | Traveler / someone moving |
| `☂️` | `umbrella` | Pedestrian in rain |
| `📱` | `cell phone`| Pedestrian lingering on their phone |

## 3. Park & Recreation (COCO Ecosystem Additions)
| Emoji | YOLO Class | Translation / Context |
| :---: | :--- | :--- |
| `🛹` | `skateboard` | Skateboarder on sidewalk |
| `⚽` | `sports ball`| Kids playing in the street/yard |
| `🥏` | `frisbee` | Yard recreation |
| `🪁` | `kite` | Park recreation |

## 4. Wildlife & Anomalies
*Note: High-scoring anomalies automatically bypass standard email rate limits.*

| Emoji | YOLO Class | Translation / Context |
| :---: | :--- | :--- |
| `🐕` | `dog` | Dog (accompanied by person) |
| `🐕⚠️` | `dog` (no person)| **Anomaly:** Loose, off-leash dog |
| `🐈` | `cat` | Feline roaming |
| `🦅` | `bird` | Large bird (hawk, owl, crow) |
| `🦌` | `sheep`/`cow` | **Heuristic Map:** Suburban large wildlife (Deer) |
| `🐻` | `bear` | **Critical Anomaly:** Bear in perimeter |
| `🐎` | `horse` | Equestrian activity |

| Emoji | Event | Translation |
| :---: | :--- | :--- |
| `🚨🚓` | Emergency Responders | "Flashing lights detected (Police/Fire/Ambulance)." |
| `🚧🚗` | Spatial Violation | "A vehicle has breached the sidewalk or park boundary." |
| `⚠️🌡️` | Thermal Warning | "System temperature reached 75°C. Monitor closely." |
| `🔴🔥` | Thermal Shutdown | "System temperature reached 85°C. Inference suspended to prevent damage." |
| `🔋📉` | Low Voltage | "Under-voltage detected from the power supply (⚡ icon)." |

## 5. Weather & Environmental Conditions
*Note: These require future integration with either visual heuristics (e.g., detecting snow accumulation) or a local weather API.*

| Emoji | Event | Translation |
| :---: | :--- | :--- |
| `🌧️☔` | Heavy Rain | "Heavy rainfall or downpour detected." |
| `❄️⛄` | Snow/Blizzard | "Snowfall or significant snow accumulation." |
| `🌫️👀` | Dense Fog | "Visibility is severely reduced due to fog." |
| `💨🍃` | High Winds | "Strong winds (detected via severe branch movement)." |
| `⛈️⚡` | Thunderstorm | "Lightning flashes or severe storm conditions." |
| `🌅` / `🌇` | Sunrise/Sunset | "Transition between day/night vision modes." |
| `🌈` | Rainbow | "A rainbow is visible in the sky." |
| `✨🌌` | Clear Night | "High visibility night sky (stars visible)." |

---
*Note: Emergency alerts (`🚨`, `🚧`) include a secure, time-limited link to an anonymized bounding-box frame for visual verification.*
