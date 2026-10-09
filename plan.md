co?
zbudować ekosystem synchronizacji desktopu miedzy roznymi komputerami asinux.
informacje maja byc zapisywane w chmurze na start w onedrive w katalogu asinux pozniej pomyslimy o innych chmurach.
nalezy synchronizowac
username
profilowe
tapete (lub kwestie live wallpaper dynamic wallpaper etc. - partrz /opt/asinux-system-configuration)
dodatkowe rrozszerzenia
ulubiony layout (patrz /opt/asinux-layouts)
preferowane ikony
preferowane schemas
preferowany wskaznik myszy
przypiete do launcherry do dock
skroty klawiszowe
czesc ustawien (np. czy pokazywac makssymalizuj, z ktorej strony window decorations - kwestia otwarta co)
domsyslne programy uzytkownika

dlaczego?
koniecznosc personalizacji kompow za kazdym razem gdy nowa instalacja lub nowe urzadzenie

po co?
stworzenie podobnego ekosystemu do mac os - wygoda

jak?
panel w gtk4 do asinux-system-configuration
backend w python
jak podlaczyc onedrive? - kwestia otwarta

dodatkowo:
ma to byc rozwiazanie specjalnie dla sytrybucji linuxa ktora tworze - asinux - patrzy inne instancje claude code
do asinux-system-configuration bedzie treba dodac panel do zarzadzania uzytkownikami na urzadzeniu. chcialbym aby przy tworzeniu usera mozna bylo zalogowac sie przez micfsorsoft do onedive a username do useadd zostanie pobeany z chmury. jesli ktos nigdy nie mial konta w asinux to zostanie stworzeone.
przy tworzeniu nowego uzytkownika, po stworzeniu nowego uzytkownika bedzie ekran starotowy gdzie bedzie mozna ustawic pewne preferencje ale to projekt na pozniej.

powyzsze sytuacja komplikuje jednak sprawe. tzn jak na uzytkowniku filip bede chcial zrobic nowe konto dla uzytkownika kuba to musi on sie zalogowac do onedrive jeszce na mojej sesji (aby pobrac linuxowe username) co prawdopopodobnie uniemzoliwoa podlaczenie przez gnome oline accoutns

kwestie otwarte
ktore ustawienia/dconf jeszce mozna synchronizwoac a ktore bez sensu (np. displays - no jak to zsynchronizoac)
jak podpaic onedrinve
