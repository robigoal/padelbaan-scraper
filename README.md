# Padelbaan-checker De Geeren (Baarn)

Checkt elke 10 minuten via KNLTB Meet & Play (club 88192) of er een **indoor dubbelbaan**
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

## Lokaal
Zet `SLACK_WEBHOOK_URL=...` in `.env` (staat in `.gitignore`).
- `python3 padel_check.py --dry-run` — toont wat er vrij is, stuurt niets.
- `python3 padel_check.py --test-slack` — stuurt een testbericht.

Instellingen (dagen, tijden, boekingsvenster) staan bovenaan `padel_check.py`.
