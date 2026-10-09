po uruhcoieniu:
jesli uzytokonik zalogowany goto #widok-glowny
jesli nie zalogowany do onedrive goto #widok-logowania

#widok-logowania
zaurtoryzuj onedrive
zapytaj jaka nazwe dac urzadzeniu (defoultowe wartosc w polu ankiety -  wez z hostname)
jesli istnieje juz katalog asinux-sync goto #widok-glowny i zacznij synchronizacje w tle
jesli nie zapytaj o username i prettyname jakie zapisac na przyszlosc (defoultowe wartosci w polu pobierz z GNOME i $USERNAME)
wybierz defoultowy jezyk/region/klawiature (rowniez pobierz z systemu)


#widok-glowny
lista urzadzen podlaczonych
mozna kliknac na urzadzenie
i tam do ustawnienia wszystko co mozna synchronizowac toggle on/off
