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

| Moduł | Elementy (każdy do wyłączenia osobno) |
|---|---|
| `background` | tapeta pulpitu (jasna/ciemna, dopasowanie, własne dynamiczne tapety z obrazami), obraz ekranu blokady, live wallpaper Hanabi, Damask |
| `appearance` | motyw aplikacji (GTK), ikony, motyw kursora, rozmiar kursora, jasny/ciemny, kolor akcentu, motyw shella; motywy zainstalowane przez użytkownika jadą jako archiwa |
| `window` | przyciski paska tytułu, kliknięcia paska tytułu, focus, klawisz modyfikatora okien, nowe okna |
| `multitasking` | wiersze panelu Multitasking: hot corner, zmiana rozmiaru okien przy krawędziach, ponowne otwieranie okien po logowaniu, dynamiczne/stałe obszary robocze i ich liczba, obszary na monitorach, przełączanie aplikacji |
| `dock` | aplikacje przypięte do docka/launchera (`favorite-apps`) |
| `extensions` | rozszerzenia GNOME Shell — dla każdego menu: „Extension and prefs” (stan + ustawienia), „Only extension” (tylko stan), „Off” |
| `layout` | ustawienia rozszerzeń (dock, panel, menu, efekty) — jeden przełącznik, bez rozwijania; całkowicie niezależne od `extensions` i od asinux-layouts |
| `keyboard` | źródła wejścia, przełączanie per okno, klawisz znaków alternatywnych, klawisz Compose, pozostałe opcje układu, powtarzanie klawiszy, wejście Emacs, klawisz przeglądu. Układ wybrany przy zakładaniu profilu jest wartością domyślną: staje się układem pierwszego urządzenia, a na kolejnych urządzeniach uzupełnia chmurę, jeśli nikt jeszcze nie zsynchronizował klawiatury |
| `shortcuts` | skróty okien, shella, kompozytora, klawisze multimediów/systemowe, własne skróty |
| `mouse` | panel Mysz i touchpad: przycisk główny, prędkość i przyspieszenie myszy, kierunek przewijania, wklejanie środkowym przyciskiem; touchpad (włączony, wyłączanie podczas pisania, prędkość, kliknięcie dodatkowe, tapnięcie, metoda i kierunek przewijania) i pointing stick — te dwa tylko na komputerach z takim sprzętem |
| `default_apps` | `~/.config/mimeapps.list` |
| `notifications` | panel Powiadomień: Nie przeszkadzać, powiadomienia na ekranie blokady oraz — osobny element dla każdej aplikacji — jej sześć przełączników (powiadomienia, dźwięk, banery, treść banerów, ekran blokady, treść na blokadzie) |
| `accessibility` | wszystkie wiersze panelu Dostępność: widzenie, słuch, pisanie, wskazywanie i klikanie, powiększenie (25 elementów). Pominięte, bo klucz należy już do innego modułu: rozmiar kursora (Theme, icons and cursor), powtarzanie klawiszy (Keyboard) i aktywacja okna po najechaniu (Window behavior → Focus) |
| `region` | język i formaty konta (część „Your Account” strony Region & Language; stosowane po następnym logowaniu). Ekran logowania jest systemowy, wymaga administratora i nie jest synchronizowany |
| `datetime` | format czasu, pierwszy dzień tygodnia, zegar i kalendarz w górnym pasku, automatyczna strefa czasowa. Data, czas sieciowy i strefa czasowa są systemowe (`timedated`, wymagają administratora) i nie są synchronizowane |
| `power` | wiersze panelu zasilania: przycisk zasilania, procent baterii, przyciemnianie, jasność automatyczna, oszczędzanie baterii, wygaszanie ekranu, usypianie na baterii i przy zasilaniu |
| `profile_picture` | zdjęcie konta (AccountsService) |

`layout` i `power` są domyślnie wyłączone (opt-in): każde urządzenie dostaje je wyłączone raz,
przy pierwszej rejestracji po wprowadzeniu tej zasady, a ręczne włączenie później nie jest cofane.

Przełączniki są zapisane w lokalnej liście urządzenia jako `<moduł>` (cała kategoria) lub
`<moduł>.<element>`. Profil w chmurze jest podzielony na elementy (`{"items": {...}}`), więc
element wyłączony na jednym urządzeniu zostaje w chmurze nietknięty dla pozostałych. Każde
urządzenie publikuje własną listę elementów (`catalog`), dzięki czemu panel pokazuje u innego
komputera jego rozszerzenia, a nie te z bieżącego.

Format profilu ma wersję 2. Starszy profil w chmurze zostaje przejęty przez pierwsze urządzenie,
które już się synchronizowało; zupełnie nowe urządzenie czeka i niczego nie nadpisuje. Wszystkie
komputery warto zaktualizować (`git pull` + `systemctl --user restart asinux-sync`).

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
