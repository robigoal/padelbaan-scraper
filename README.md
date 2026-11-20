# Padelbaan-checker rond Soest

Checkt elke 10 minuten bij **De Geeren Baarn** (KNLTB Meet & Play) en **Peakz Amersfoort**
(Foys) of er een **indoor dubbelbaan**
(geen single court) vrij is op **woensdag of donderdag** met begintijd **20:00, 20:30 of 21:00**,
binnen het boekingsvenster van 10 dagen. Nieuwe vrije banen worden in Slack gemeld; dezelfde
baan wordt pas opnieuw gemeld als hij tussendoor bezet is geweest.

## Hoe het draait
GitHub Actions (`.github/workflows/padel-check.yml`) draait het script elke 10 minuten.
De Slack Incoming Webhook staat als repository secret `SLACK_WEBHOOK_URL`.
`state.json` onthoudt welke banen al gemeld zijn en wordt door de workflow teruggecommit.

- Handmatig draaien: tab **Actions** → *Padelbaan-check De Geeren* → **Run workflow**,
  of `gh workflow run padel-check.yml`.
- Pauzeren: `gh workflow disable padel-check.yml` (weer aan: `enable`).

## Pushmeldingen (ntfy)
Naast Slack stuurt het script bij nieuwe vrije banen een push via [ntfy](https://ntfy.sh).
Installeer de ntfy-app en abonneer je op het geheime topic uit `.env` (`NTFY_TOPIC`, ook
repository secret). Test: `python3 padel_check.py --test-ntfy`.

## App op je telefoon (PWA)
https://robigoal.github.io/padelbaan-scraper/ — toont de vrije banen uit `docs/slots.json`
met per baan een **Boek**-knop naar de boekpagina van die club op de juiste datum.
Plaza Padel Amersfoort en Laren (Playtomic) staan er alleen als snelkoppeling in:
Playtomic blokkeert geautomatiseerde toegang, dus die worden niet gecheckt.
Installeren: open de link in Safari → Deel → *Zet op beginscherm* (Android/Chrome: *App installeren*).
De app staat in `docs/` en wordt via GitHub Pages gehost.

## Lokaal
Zet `SLACK_WEBHOOK_URL=...` in `.env` (staat in `.gitignore`).
- `python3 padel_check.py --dry-run` — toont wat er vrij is, stuurt niets.
- `python3 padel_check.py --test-slack` — stuurt een testbericht.

Instellingen (dagen, tijden, boekingsvenster) staan bovenaan `padel_check.py`.
