# AAX → M4B / MP3 Konverter

Minimale Web-App zum Konvertieren von Audible-AAX-Dateien in M4B oder MP3 über ffmpeg.

## Features

- **Format-Wahl** — M4B (verlustfreier Remux) oder MP3 (re-encode)
- **Ziel-Wahl** — direkt auf den Server-Mount oder als Browser-Download
- **Live-Log** — ffmpeg-Output in Echtzeit im Terminal-Widget

## Schnellstart

```bash
cp .env.example .env          # ACTIVATION_BYTES eintragen
docker compose up -d
# Browser: http://localhost:8080
```

## Konfiguration (.env)

| Variable           | Pflicht | Beschreibung                                        |
|--------------------|---------|-----------------------------------------------------|
| `ACTIVATION_BYTES` | –       | Hex-Wert    |
| `OUTPUT_PATH`      | –       | Lokaler Ausgabepfad (Standard: `./output`)          |
| `PORT`             | –       | Host-Port (Standard: `8080`)                        |

## Ausgabe-Struktur (Server-Modus)

```
./output/
└── Autor - Titel/
    └── Buchtitel.m4b   (oder .mp3)
```

## Image-Größe

`python:3.12-alpine` + `ffmpeg` via apk. Nur Flask als Python-Dependency.
