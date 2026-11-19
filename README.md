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

## Instalacja na nowym komputerze

```bash
# Fedora + GNOME: pakiety, repozytoria w /opt, launcher i dostawca wyszukiwania w /usr/share
curl -fsSL https://raw.githubusercontent.com/fbrzostowski/asinux-sync/master/setup.sh | bash

# albo z klona, kopiując dokładny stan ASC z innego komputera (także niewypchnięte panele)
./setup.sh --asc-from uzytkownik@inny-pc:/opt/asinux-system-configuration/

# to samo dla Asinux Sync (niezacommitowane/niewypchnięte zmiany z innego komputera)
./setup.sh --sync-from uzytkownik@inny-pc:/opt/asinux-sync/ \
           --asc-from uzytkownik@inny-pc:/opt/asinux-system-configuration/
```

Skrypt jest idempotentny (ponowne uruchomienie aktualizuje) i ma `--dry-run`, `--no-packages`,
`--no-asc`, `--sync-from`, `--sync-repo`, `--prefix`. Niczego nie nadpisuje bez pytania: katalog docelowy, który nie
jest checkoutem gita i nie jest pusty, kończy się błędem, a `rsync` działa bez `--delete`.

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

# urządzenia na koncie; usunięcie urządzenia (po nazwie albo id)
python3 -m asinux_sync.cli devices
python3 -m asinux_sync.cli remove-device "Laptop"
```

Urządzenie można też usunąć w panelu: wybierz je na liście i kliknij „Remove Device”. Usunięte
urządzenie znika z listy od razu, a samo przestaje synchronizować przy najbliższym cyklu, gdy
będzie online (jego wpis w chmurze zastępuje znacznik `"removed": true`, żeby nie zarejestrowało
się ponownie). Ustawienia na nim zostają bez zmian; można je później skonfigurować od nowa jako
nowe urządzenie.

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
| `fonts` | czcionki: interfejs, dokumenty, monospace, tytuły okien, hinting, antialiasing (skala tekstu jest w Accessibility → Text size) |
| `window` | przyciski paska tytułu, kliknięcia paska tytułu, focus, klawisz modyfikatora okien, nowe okna |
| `multitasking` | wiersze panelu Multitasking: hot corner, zmiana rozmiaru okien przy krawędziach, ponowne otwieranie okien po logowaniu, dynamiczne/stałe obszary robocze i ich liczba, obszary na monitorach, przełączanie aplikacji |
| `dock` | aplikacje przypięte do docka/launchera (`favorite-apps`) |
| `app_grid` | kolejność aplikacji na siatce i foldery aplikacji |
| `extensions` | rozszerzenia GNOME Shell — dla każdego menu: „Extension and prefs” (stan + ustawienia), „Only extension” (tylko stan), „Off” |
| `config_files` | całe pliki konfiguracyjne aplikacji i rozszerzeń spoza GSettings, każdy osobno: `~/.config/custom-menu.json` (rozszerzenie Custom Menu). Kolejny plik to jedna linijka w `FILES` w `modules/config_files.py`. Plik, którego na danym komputerze nie ma, nie jest wysyłany jako pusty ani usuwany na pozostałych |
| `layout` | ustawienia rozszerzeń (dock, panel, menu, efekty) — jeden przełącznik, bez rozwijania; całkowicie niezależne od `extensions` i od asinux-layouts. Ścieżki dconf są czytane ze schematów każdego rozszerzenia (nie zgadywane z nazwy), a zapis jest ograniczony do folderów, które rozszerzenie samo deklaruje |
| `keyboard` | źródła wejścia, przełączanie per okno, klawisz znaków alternatywnych, klawisz Compose, pozostałe opcje układu, powtarzanie klawiszy, wejście Emacs, klawisz przeglądu. Układ wybrany przy zakładaniu profilu jest wartością domyślną: staje się układem pierwszego urządzenia, a na kolejnych urządzeniach uzupełnia chmurę, jeśli nikt jeszcze nie zsynchronizował klawiatury |
| `shortcuts` | skróty okien, shella, kompozytora, klawisze multimediów/systemowe, własne skróty |
| `mouse` | panel Mysz i touchpad: przycisk główny, prędkość i przyspieszenie myszy, kierunek przewijania, wklejanie środkowym przyciskiem; touchpad (włączony, wyłączanie podczas pisania, prędkość, kliknięcie dodatkowe, tapnięcie, metoda i kierunek przewijania) i pointing stick — te dwa tylko na komputerach z takim sprzętem |
| `shell` | domyślna powłoka (sh, bash, zsh, fish; jak panel Default Shell w ASC), prompt Tide dla fish (czy jest zainstalowany + wszystkie zmienne `tide_*`), prompt Powerline dla zsh (czy `~/.zshrc` go ładuje). Zmiana powłoki i instalacja pakietów wymagają hasła, a Tide jest pobierany z GitHuba, więc usługa najpierw pyta w oknie (patrz niżej) |
| `default_apps` | `~/.config/mimeapps.list` |
| `search` | wyszukiwanie: aplikacje jako źródła wyników, ich kolejność i lokalizacje przeszukiwania plików |
| `notifications` | panel Powiadomień: Nie przeszkadzać, powiadomienia na ekranie blokady oraz — osobny element dla każdej aplikacji — jej sześć przełączników (powiadomienia, dźwięk, banery, treść banerów, ekran blokady, treść na blokadzie) |
| `accessibility` | wszystkie wiersze panelu Dostępność: widzenie, słuch, pisanie, wskazywanie i klikanie, powiększenie (25 elementów). Pominięte, bo klucz należy już do innego modułu: rozmiar kursora (Theme, icons and cursor), powtarzanie klawiszy (Keyboard) i aktywacja okna po najechaniu (Window behavior → Focus) |
| `region` | język i formaty konta (część „Your Account” strony Region & Language; stosowane po następnym logowaniu). Ekran logowania jest systemowy, wymaga administratora i nie jest synchronizowany |
| `datetime` | format czasu, pierwszy dzień tygodnia, zegar i kalendarz w górnym pasku, automatyczna strefa czasowa. Data, czas sieciowy i strefa czasowa są systemowe (`timedated`, wymagają administratora) i nie są synchronizowane |
| `night_light` | Nocna zmiana: włączenie, harmonogram, temperatura barwy |
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
brakuje na danym komputerze, nie są instalowane po cichu: usługa otwiera okno z listą rozszerzeń
włączonych w chmurze, a niezainstalowanych tutaj. „Install selected” instaluje zaznaczone przez
GNOME Shell z extensions.gnome.org (Shell sam prosi o potwierdzenie każdego), „No thanks” albo
odznaczenie rozszerzenia zapamiętuje odmowę **na tym komputerze** (`declined_extensions`
w `config.json`) i więcej o nie nie pyta. Zamknięcie okna bez wyboru to „zapytaj później”
(najwcześniej za godzinę). Rozszerzenie niedostępne na extensions.gnome.org też trafia na listę
odmów. Po instalacji jego ustawienia z chmury stosują się w następnym cyklu. Żeby znów być
pytanym: `python3 -m asinux_sync.cli reset-extension-prompts`.

Z powłoką jest podobnie. Jeśli inne komputery mają inną domyślną powłokę, Tide albo Powerline,
a ten nie, usługa otwiera okno „Shell setup from your other computers” z listą kroków:
- zmiana domyślnej powłoki (najpierw instaluje ją, jeśli trzeba),
- instalacja Tide przez Fisher (i fish, jeśli go nie ma),
- instalacja pakietu `powerline` (i zsh, jeśli go nie ma).

Wszystko, co wymaga roota, idzie jednym wywołaniem `pkexec`, czyli jedną prośbą o hasło
(`dnf install`, potem `usermod -s`). Instalowane są wyłącznie pakiety sh, bash, zsh, fish
i powerline. Odmowa jest zapamiętywana na tym komputerze (`declined_shell_setup` w
`config.json`), a `reset-extension-prompts` czyści ją razem z odmowami rozszerzeń. Bez pytania
usługa robi tylko to, co nic nie instaluje: ustawia zmienne Tide, gdy Tide już jest, i
dopisuje do `~/.zshrc` blok ładujący Powerline (oznaczony `# >>> asinux-sync: powerline >>>`),
gdy pakiet już jest. Wyłączenie Powerline w chmurze usuwa tylko ten blok, nigdy własne linie
użytkownika.

### Prośby między urządzeniami

Gdy jednemu komputerowi czegoś brakuje, a inny to ma, komputery proszą się o to przez jeden
wspólny plik w chmurze:

```
asinux-sync/requests.json          wszystkie otwarte prośby, każdego rodzaju (pole "kind")
asinux-sync/requests/<id>.tar.gz   to, co inne urządzenie wysłało w odpowiedzi
```

Każdy wpis ma `id`, `kind`, `key`, `data`, `requester`, `status` (`pending` → `sent` albo
`failed` z `reason`) i informację, kto i kiedy wysłał. W każdym cyklu urządzenie:
1. odbiera to, o co prosiło: instaluje wysłane archiwum, usuwa prośbę i archiwum;
2. odpowiada na cudze prośby, jeśli ma to, o co chodzi;
3. dopisuje prośby o to, czego mu brakuje, i usuwa te, które przestały być potrzebne.

Prośby urządzeń usuniętych z konta i archiwa bez prośby sprząta ten, kto je zobaczy. Wszystkie
urządzenia piszą do tego samego pliku, a dyski w chmurze nie mają blokad, więc dwa zapisy
w jednej chwili mogą się nadpisać. Zmiany są nakładane na świeżo pobraną kopię tuż przed zapisem,
a każdy krok jest powtarzany w następnym cyklu, jeśli przepadł. O tym, że coś wysłano, świadczy
obecność archiwum, a nie sam status. Prośba `failed` nie jest ponawiana; żeby spróbować jeszcze
raz, usuń jej wpis z `requests.json`.

Nowy rodzaj prośby to podklasa `RequestHandler` w `asinux_sync/device_requests.py` (`wanted`,
`satisfied`, `can_send`, `pack`, `receive`, `done`) dodana do listy w `daemon.py`.

#### Rodzaj `theme`: brakujące motywy, ikony i kursory

Motyw zainstalowany w katalogu domowym jedzie razem z profilem. Motyw systemowy
(`/usr/share/icons`, `/usr/share/themes`, np. z pakietu) nie, bo zwykle jest na każdym
komputerze. Gdy go brakuje (motyw aplikacji GTK, ikony, kursor, motyw shella):

1. Komputer A widzi, że profil chce np. ikon „Hatter-Purple”, których nie ma. Nie przełącza się
   na brakujący motyw (zostaje przy swoim) i dodaje prośbę `{"item": "icon_theme", "name":
   "Hatter-Purple"}`.
2. Komputer B znajduje folder w `~/.icons`, `~/.local/share/icons` albo `/usr/share/icons` (dla
   motywów: `~/.themes`, `~/.local/share/themes`, `/usr/share/themes`) i go wysyła. Linki
   symboliczne wskazujące poza folder są zastępowane plikami.
3. Komputer A rozpakowuje go do `~/.local/share/icons` (albo `~/.local/share/themes`) i w
   następnym cyklu przełącza się na motyw z profilu.

Ikony i kursory, które dziedziczą po innych (`Inherits=` w `index.theme`), dociągają brakujących
przodków tak samo. Pobrany motyw dostaje znacznik `.asinux-sync-transfer`, żeby nie był potem
wysyłany do chmury jako „własny” motyw użytkownika. Folder większy niż 1 GB nie jest wysyłany
(`failed`).

#### Rodzaj `wallpaper`: brakujące tapety systemowe

Tapeta z katalogu domowego jedzie z profilem. Tapeta systemowa (`/usr/share/backgrounds/...`)
jest tylko wskazywana. Gdy komputer jej nie ma (tapeta pulpitu, jasna i ciemna, albo ekranu
blokady), zostaje przy swojej i prosi o nią (`{"path": "/usr/share/backgrounds/..."}`). Inne
urządzenie wysyła plik, a tapetę dynamiczną (`.xml`) razem ze wszystkimi obrazami, które XML
wskazuje. Ponieważ do `/usr` nie da się pisać, kopia trafia do
`~/.local/share/asinux-sync/assets/system/<ścieżka systemowa>`, a XML jest przepisywany na te
kopie. Ustawienie wskazuje kopię, ale przy synchronizacji jest zgłaszane jako ścieżka systemowa,
więc kopia nie wygląda na zmianę tapety i nic nie wraca do chmury. Komputer, który sam pobrał
tapetę, może ją przekazać dalej.

Testy modułów używają backendu pamięciowego GSettings, więc nigdy nie zmieniają prawdziwych ustawień.

## Układ danych w chmurze

```
asinux-sync/account.json        username, nazwa wyświetlana, język/region/klawiatura
asinux-sync/devices/<id>.json   urządzenie: nazwa, wyłączone moduły, last_seen
asinux-sync/profile/            zsynchronizowany profil (profile.json + assets/)
asinux-sync/requests.json       prośby między urządzeniami (np. o brakujący motyw)
asinux-sync/requests/           to, co wysłano w odpowiedzi na prośby (.tar.gz)
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
