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

# jeden cykl synchronizacji w obie strony (to samo robi usługa w tle)
python3 -m asinux_sync.cli sync

# usługa systemd użytkownika (domyślnie włącza ją panel)
python3 -m asinux_sync.cli service enable    # albo: disable

# ręczny upload / download do debugowania
python3 -m asinux_sync.cli push
python3 -m asinux_sync.cli pull

# pokaż bieżącą konfigurację lokalną
python3 -m asinux_sync.cli status
```

Konfiguracja lokalna (nazwa remote'a rclone, id i nazwa urządzenia, lista
wyłączonych modułów) żyje w `~/.config/asinux-sync/config.json` — to
okablowanie, nie jest częścią synchronizowanego profilu.

## Synchronizacja w tle

`systemd/asinux-sync.service` to usługa **użytkownika** (nie systemowa — synchronizowane
ustawienia żyją w dconf i katalogu domowym użytkownika). Panel instaluje ją do
`~/.config/systemd/user/` i włącza; interwał (domyślnie 1 min, minimum 1 min) zmienia się
w panelu i jest lokalny dla urządzenia. Cykl: lokalna zmiana → upload, zmiana z chmury →
zastosowanie; gdy zmieniły się obie strony, wygrywa to urządzenie.

## Co się synchronizuje

Każda kategoria to moduł (`asinux_sync/modules/`) z jawną listą kluczy — nie zrzut całego dconf.
W panelu każde urządzenie ma własne przełączniki, pogrupowane tak samo.

| Moduł | Zawartość |
|---|---|
| `background` | tapeta pulpitu/blokady, własne dynamiczne tapety (XML razem z obrazami), Hanabi (ustawienia + wideo), Damask |
| `appearance` | motyw GTK/shell/ikon/kursora, schemat kolorów, akcent; motywy zainstalowane przez użytkownika jadą jako archiwa |
| `window` | przyciski i akcje paska tytułu, focus, pozycja nowych okien |
| `dock` | aplikacje przypięte do docka/launchera (`favorite-apps`) |
| `layout` | włączone rozszerzenia GNOME Shell + ich ustawienia (przez `/opt/asinux-layouts`) |
| `shortcuts` | skróty systemowe i własne |
| `default_apps` | `~/.config/mimeapps.list` |
| `profile_picture` | zdjęcie konta (AccountsService) |

Zsynchronizowane są tylko wartości ustawione przez użytkownika; klucz zostawiony domyślnie na
jednym urządzeniu jest resetowany na pozostałych. Nazwa użytkownika nie jest modułem — powstaje
raz, przy pierwszej konfiguracji (`account.json`). Nie synchronizujemy ustawień specyficznych
dla sprzętu (monitory, hasła Wi-Fi, opcje GPU Hanabi). Rozszerzenia GNOME Shell, których
brakuje na danym komputerze, nie są instalowane automatycznie — są pomijane.

Testy modułów używają backendu pamięciowego GSettings, więc nigdy nie zmieniają prawdziwych ustawień.

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

## Licencja

MIT — zobacz plik [LICENSE](LICENSE).
