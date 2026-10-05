# BetStorm automation

Mantiene gli updater esistenti e aggiunge quattro consumer dei dati Supabase: Telegram, newsletter Resend, Results Checker e SEO Content Engine.

## Secrets GitHub richiesti
Già esistenti: `FOOTBALL_DATA_API_KEY`, `GEMINI_API_KEY`, `ODDS_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

Nuovi: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `NEWSLETTER_RECIPIENTS` (lista separata da virgole).

## File prodotti in Supabase
- `matches.json`, `predictions_history.json` — updater esistente
- `odds.json`, `tennis.json`, `basketball.json` — odds updater esistente
- `articles.json` — SEO Content Engine

## Note
- Nessuna chiave va committata nel repository.
- Il motore SEO usa solo i dati BetStorm forniti nel prompt e salva bozze strutturate; non pubblica automaticamente su WordPress.
- I cron GitHub Actions sono UTC e non seguono automaticamente ora legale/solare italiana.
