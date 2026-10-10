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

Online zetten kan ook: zet GitHub Pages aan op deze branch (Settings → Pages), dan speel je
vanaf elk apparaat via `https://<gebruiker>.github.io/<repo>/`. Je ROM wordt nooit geüpload;
die blijft lokaal in je browser.

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

Toetsen aanpassen, gamepad, savestates, fullscreen en schermindeling zitten in het menu
onderin het spelscherm.

## Opslaan

In-game saves en savestates staan in de IndexedDB van je browser, per spel. Sitegegevens
wissen = saves kwijt. Exporteer ze via het menu als backup.

## Onderhoud

- `tools/update-emulatorjs.sh [versie]` — haalt EmulatorJS en de cores opnieuw op van npm.
- `tools/make-test-rom.py` — bouwt een mini homebrew-ROM (rood scherm, groen als je A
  ingedrukt houdt) om te testen of emulatie en input werken.

## Licentie

EmulatorJS, melonDS en DeSmuME zijn GPL-3.0 (zie `emulator/LICENSE-EmulatorJS`).
