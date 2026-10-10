# Maikol DS

Nintendo DS-emulator die in je browser draait. Werkt op desktop, tablet en telefoon.

Onder de motorkap: [EmulatorJS](https://emulatorjs.org) 4.2.3 met de libretro-cores
**melonDS** (standaard) en **DeSmuME**, gecompileerd naar WebAssembly. Alles staat in deze
repo (`emulator/`), dus er is geen externe CDN nodig.

## Spelen

De pagina moet via een webserver geopend worden (dubbelklikken op `index.html` werkt niet,
browsers blokkeren dan het laden van de emulator-cores).

```bash
python3 -m http.server 8000
```

Open daarna <http://localhost:8000> en kies of sleep een `.nds`-bestand (ook `.zip`/`.7z`).

## Op je telefoon

1. Zet GitHub Pages aan: repo → **Settings → Pages** → *Source: Deploy from a branch* →
   branch `claude/nds-emulator-d9rdm7`, map `/ (root)` → **Save**. Na een minuut of twee staat
   de site op <https://poenanispeed.github.io/maikol/>.
2. Open die link op je telefoon en kies **Toevoegen aan beginscherm** (Android: menu ⋮,
   iPhone: deelknop). Dan start hij als app op volledig scherm, en bewaart iOS je saves
   betrouwbaarder dan in een gewoon Safari-tabblad.
3. Zet je ROM-bestanden op je telefoon (bijv. in Downloads/Bestanden) en kies ze in de app.

Je ROM wordt nooit geüpload; die blijft lokaal in je browser.

Op de telefoon tik je met je vinger op het onderste scherm; de knoppen staan eronder
(L/R naast Select/Start, zodat ze het touchscreen niet afdekken).

## ROMs en BIOS

- Er zitten **geen spellen** bij. Gebruik dumps van je eigen cartridges of homebrew.
- Een BIOS/firmware is niet nodig: melonDS start spellen direct op met zijn ingebouwde
  vrije BIOS. Een paar spellen die op echte firmware leunen kunnen daardoor afwijken;
  probeer dan de andere core.

## Besturing

| DS | Toetsenbord |
|---|---|
| D-pad | pijltjestoetsen |
| A / B | `Z` / `X` |
| X / Y | `A` / `S` |
| L / R | `Q` / `E` |
| Start / Select | `Enter` / `V` |
| Touchscreen | muis / vinger op onderste scherm |

De ronde knop linksboven in het spel wisselt de snelheid: **1x → 2x → 4x → 1x**. Dat is een
maximum: haalt je apparaat het niet, dan loopt het spel zo snel als het kan. Vooral melonDS
op een telefoon haalt 4x meestal niet; DeSmuME is lichter.

Toetsen aanpassen, gamepad, savestates, fullscreen en schermindeling zitten in het menu
onderin het spelscherm.

## Opslaan

In-game saves en savestates staan in de IndexedDB van je browser, per spel. Sitegegevens
wissen = saves kwijt. Exporteer ze via het menu als backup.

## Onderhoud

- `tools/update-emulatorjs.sh [versie]` — haalt EmulatorJS en de cores opnieuw op van npm.
- `tools/make-test-rom.py` — bouwt een mini homebrew-ROM om emulatie te testen: rood
  scherm, groen bij A, blauw bij aanraken van het touchscreen, plus een frameteller
  bovenin om de snelheid te meten.

## Licentie

EmulatorJS, melonDS en DeSmuME zijn GPL-3.0 (zie `emulator/LICENSE-EmulatorJS`).
