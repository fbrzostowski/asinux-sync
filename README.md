# asinux-sync

Synchronizacja personalizacji desktopu (tapeta, motywy, skróty, layout
GNOME Shell, domyślne programy, zdjęcie profilowe) między maszynami tego
samego użytkownika w ASinux, przez dowolną chmurę obsługiwaną przez
[rclone](https://rclone.org/) (na start: OneDrive).

Pełna architektura i uzasadnienie decyzji: zobacz plan w historii tej sesji
Claude Code / `plan.md`.

## Status

Faza 0 (spike): szkielet repo, warstwa transportowa (`asinux_sync/cloud/rclone.py`),
silnik synchronizacji (`asinux_sync/engine.py`) i jeden moduł — `background`
(tapeta pulpitu/blokady ekranu) — jako dowód koncepcji end-to-end.

## Wymagania

- Python 3.11+
- PyGObject (`python3-gi`, dla modułów czytających GSettings)
- [`rclone`](https://rclone.org/) zainstalowany i w `$PATH`

## Użycie

```bash
# panel GTK4 (logowanie, urządzenia, przełączniki synchronizacji)
python3 main.py

# połącz konto chmurowe (domyślnie OneDrive) przez interaktywny OAuth
python3 -m asinux_sync.cli connect

# wypchnij bieżący stan włączonych modułów do chmury
python3 -m asinux_sync.cli push

# pobierz i zastosuj stan z chmury na tej maszynie
python3 -m asinux_sync.cli pull

# pokaż bieżącą konfigurację lokalną
python3 -m asinux_sync.cli status
```

Konfiguracja lokalna (nazwa remote'a rclone, id i nazwa urządzenia, lista
wyłączonych modułów) żyje w `~/.config/asinux-sync/config.json` — to
okablowanie, nie jest częścią synchronizowanego profilu.

## Układ danych w chmurze

```
asinux-sync/account.json        username, nazwa wyświetlana, język/region/klawiatura
asinux-sync/devices/<id>.json   urządzenie: nazwa, wyłączone moduły, last_seen
asinux-sync/profile/            zsynchronizowany profil (profile.json + assets/)
```

## Struktura

```
asinux_sync/
  cloud/rclone.py     # wrapper subprocess nad rclone — jedyne miejsce, które wie o transporcie
  modules/            # capture()/apply() per kategoria personalizacji (allowlist, nie dump dconf)
  engine.py           # orkiestracja push/pull, wykrywanie konfliktów (last-write-wins + ostrzeżenie)
  config.py           # lokalna konfiguracja (~/.config/asinux-sync/config.json)
  cli.py              # asinux-sync-cli connect|push|pull|status
tests/
```

## Testy

```bash
python3 -m pytest tests/ -q
```

Testy modułów używają fałszywego `Gio.Settings` w pamięci — nigdy nie
dotykają prawdziwego dconf uruchamiającej je maszyny.
