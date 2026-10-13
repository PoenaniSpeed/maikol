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

Op een iPhone verschijnt na het laden **"▶ Tik om te spelen"**: Safari start geluid pas na
een tik, en de emulator staat stil zolang het geluid stilstaat.

## Als het crasht of wit blijft

- **Wit scherm zonder geluid:** tik op het scherm (of op "Tik om te spelen").
- **FPS tonen** aanvinken laat zien of de emulator draait (60 = volle snelheid). Draait hij
  op 60 en blijft het scherm wit, dan hangt het spel zelf: probeer de andere core.

Sluit iOS het tabblad af ("Er heeft zich herhaaldelijk een probleem voorgedaan"), dan
toont de startpagina de volgende keer wanneer het misging (laden, opstarten of spelen),
welk spel en hoe groot. Wat je kunt proberen:

- **Compatibiliteitsmodus** aanvinken (WebGL 1 in plaats van WebGL 2).
- **DeSmuME** kiezen in plaats van melonDS.
- De site in **Safari zelf** openen, niet in de browser binnen een andere app.
- Andere apps sluiten: grote spellen (128–512 MB) vragen veel geheugen.

Gemeten geheugengebruik met een ROM van 512 MB (zoals Pokémon Black 2 / White 2):

| Core | Piek tijdens laden | Tijdens spelen |
|---|---|---|
| melonDS | ~1,3 GB | ~1,2 GB |
| DeSmuME | ~1,3 GB | ~0,8 GB |

melonDS (via RetroArch) houdt de ROM twee keer vast. Voor ROM's boven 256 MB schrijft de
site het bestand daarom in stukjes direct in het emulatorgeheugen en geeft dat blok vrij
zodra het is ingelezen, zodat melonDS de ruimte hergebruikt (eerst was de piek ~1,8 GB).

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

- `tools/update-emulatorjs.sh [versie]` — haalt EmulatorJS en de cores opnieuw op van npm
  en bouwt daarna de bundle.
- `tools/build-emulatorjs-bundle.sh` — maakt `emulator/emulator.min.js` uit de bronbestanden
  (npm levert geen geminificeerde versie; zonder bundle draait EmulatorJS in debug-modus,
  wat merkbaar trager is).
- `tools/make-test-rom.py` — bouwt een homebrew-test-ROM: frameteller (snelheid),
  ruwe touchscreen-X/Y (klopt de vingerpositie?), een cartridge-leestest die elke frame
  draait, en een achtergrond die groen wordt bij A en blauw bij aanraken.

## Licentie

EmulatorJS, melonDS en DeSmuME zijn GPL-3.0 (zie `emulator/LICENSE-EmulatorJS`).
