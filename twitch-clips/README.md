# twitch-clips

Neemt een Twitch-kanaal op, logt tegelijk de chat, zoekt de momenten waarop de chat
ontploft, knipt daar clips van en maakt er 9:16-video's van met ingebrande ondertitels.

```
streamlink ─► ffmpeg ─► segments/seg_*.ts + segments.csv (ijkpunt per segment)
Twitch IRC (anoniem) ─► chat.csv + chat_counts.csv
                              │
                    spikes ─► series.csv, spikes.csv
                              │
                       cut ─► clips/clip_*.mp4
                              │
                  captions ─► final/clip_*_9x16.mp4   (Whisper + ffmpeg/libass)
```

## Lees dit eerst

- **Alleen kanalen met actieve chat.** Onder pakweg een paar honderd kijkers is er geen
  signaal: de mediaan is 0 en elke "piek" is drie mensen die hallo zeggen.
- **Rechten.** Clips van andermans stream herposten mag niet zomaar (auteursrecht,
  Twitch-voorwaarden). Vraag de streamer om toestemming of gebruik je eigen kanaal.
- **De timing is op een paar seconden na.** Chat reageert met vertraging, en kijkers
  hebben verschillende latency. Kalibreer één keer met `--offset` (zie onder).

## Installatie

Vereist: Python 3.9+ en ffmpeg met libass (standaard in de meeste builds; controleer
met `ffmpeg -filters | grep ass`).

```bash
cd twitch-clips
pip install -e .          # installeert streamlink en faster-whisper
```

Het Whisper-model wordt bij het eerste gebruik gedownload (`small` ≈ 500 MB).

## Gebruik

Eén command doet alles:

```bash
twitch-clips run kanaalnaam
```

Het neemt op en logt chat tot de stream stopt, of tot je **Ctrl+C** drukt. Daarna
zoekt het pieken, knipt, transcribeert en rendert het. Het resultaat staat in
`sessions/kanaalnaam-JJJJMMDD-UUMMSS/final/`.

Handige opties:

```bash
twitch-clips run kanaal --wait                  # wacht tot het kanaal live gaat
twitch-clips run kanaal --duration 3600         # maximaal een uur opnemen
twitch-clips run kanaal --language nl --model medium
twitch-clips run kanaal --layout crop --crop-x 0.3
twitch-clips run kanaal --no-captions           # alleen 9:16, geen Whisper
```

Alle opties: `twitch-clips run --help`. Zonder installatie werkt ook
`python -m twitchclips ...`.

### Opnieuw verwerken zonder opnieuw op te nemen

Opname en chatlog blijven bewaard, dus je kunt drempels en venster aanpassen en
opnieuw draaien:

```bash
twitch-clips process sessions/kanaal-20261116-210000 --ratio 4 --top 5 --pre 25
```

Of per stap: `spikes`, `cut`, `captions` (elk met een sessiemap als argument).
Transcripties worden per clip gecachet (`clips/*.words.json`). Alleen de
ondertitelstijl aanpassen (`twitch-clips captions <sessie> --font-size 96`) kost dus
geen nieuwe Whisper-run. Opnieuw knippen gooit de cache van die clip weg. Wil je een
ander model of een andere taal, verwijder dan de `.words.json`-bestanden.

### Opnemen en chat apart

```bash
twitch-clips record kanaal --session sessions/vanavond
twitch-clips chat   kanaal --session sessions/vanavond      # in een tweede terminal
twitch-clips process sessions/vanavond
```

## Wat er in een sessiemap staat

| Bestand | Inhoud |
|---|---|
| `segments/seg_000000.ts …` | De opname in stukken van 60 s (MPEG-TS, ongewijzigd) |
| `segments.csv` | Per segment: run, en de wall-clock-tijd waarop het binnenkwam |
| `chat.csv` | Elk bericht: ontvangsttijd, Twitch-servertijd, gebruiker, tekst |
| `chat_counts.csv` | Live telling per 10 s: berichten en unieke chatters |
| `series.csv` | Wat `spikes` zag: telling, mediaan, drempel, heet ja/nee per bucket |
| `spikes.csv` | De gekozen pieken met sterkte (piek ÷ drempel) |
| `clips/` | Ruwe clips in het originele formaat (+ `.ass` en `.words.json`) |
| `final/` | De 9:16-video's |

## Hoe het werkt, en waar het afwijkt van het simpele plan

### 1. Opnemen (`record.py`): ijkpunt per segment, niet één starttijd

Eén starttijd voor één groot bestand gaat mis. Streamlink filtert reclameblokken eruit
(in versie 8 altijd) en bij een reconnect mist er een stuk. Na de eerste mid-roll loopt
de video 30-180 s achter op de klok, en elke chatpiek daarna wijst naar het verkeerde
moment.

Daarom schrijft ffmpeg de stream in segmenten van 60 s, en noteert `record.py` per
segment de wall-clock-tijd waarop het bestand ontstond. Elke segmentgrens is een nieuw
ijkpunt; een gat verstoort hooguit één segment. Valt de stream weg, dan probeert
`record` het opnieuw (`--retries`) en begint een nieuwe *run*. Clips worden nooit over
een run-grens heen geplakt.

Het eerste segment van een run ontstaat pas nadat ffmpeg de stream heeft geanalyseerd,
en streamlink begint met een salvo oudere segmenten. `cut.py` rekent dat eerste
ijkpunt daarom terug vanaf het tweede.

### 2. Chat (`chat.py`)

Anoniem via `irc.chat.twitch.tv:6697` (TLS) als `justinfanNNNNN`, zonder token.
Reageert op PING en RECONNECT, verbindt zelf opnieuw. Tijdstempels komen van de
lokale klok, net als de ijkpunten van de opname, zodat er geen klokverschil tussen zit.
Twitch' eigen `tmi-sent-ts` wordt ook bewaard.

### 3. Pieken (`spikes.py`)

Een bucket van 10 s is "heet" als

    waarde > 3 × mediaan(voorgaande 5 minuten)   én   waarde ≥ 8

Die ondergrens (`--min-count`) is nodig. In stille chat is de mediaan 0 of 1, en dan
haalt elke drie berichten de 3× al. De mediaan komt uit de buckets *vóór* de huidige,
zodat een piek zijn eigen drempel niet optilt.

Verder:
- Bots (Nightbot, StreamElements, …) en `!commands` tellen niet mee. Ze posten in
  salvo's en maken nep-pieken. Zet terug met `--keep-bots` / `--keep-commands`.
- Aaneengesloten hete buckets zijn één gebeurtenis. Het ankerpunt is het **begin** van
  de golf, niet de top: de top ligt nog verder na het eigenlijke moment.
- Pieken binnen 30 s van een sterkere vallen af (`--min-gap`); de top 10 blijft
  over (`--top`).
- `--metric chatters` telt unieke chatters i.p.v. berichten. Dat is robuuster tegen
  één persoon die spamt.

### 4. Knippen (`cut.py`): 20 s ervoor, 10 s erna

Chat reageert *ná* het moment: kijker-latency plus reactietijd, al snel 5-15 s. Een
venster van 15 s ervoor en 15 s erna knipt dan vaak de aanloop weg. Standaard is
het daarom `--pre 20 --post 10`.

Seeken in MPEG-TS is in ffmpeg onbetrouwbaar: het landt soms op het keyframe *ná* het
doel. `cut.py` seekt daarom grof tot 10 s ervoor en decodeert de rest. Dat is
framenauwkeurig. `--copy` slaat het her-encoderen over, maar begint dan op een keyframe.

### 5. Ondertitels en 9:16 (`captions.py`)

- **faster-whisper**: dezelfde Whisper-modellen als `openai-whisper`, maar ~4× sneller
  op CPU en zonder PyTorch. VAD-filter staat aan tegen verzonnen tekst tijdens
  game-geluid.
- Woord-tijdstempels → stukjes van max. 3 woorden, het actieve woord geel
  (`--no-highlight` om dat uit te zetten).
- Layout `blur` (standaard) zet het hele 16:9-beeld in het midden op een vervaagde
  achtergrond. `crop` snijdt een 9:16-strook uit (`--crop-x` 0 = links, 1 = rechts).
- Ondertitels staan 560 px boven de onderrand (`--margin-v`), boven de TikTok/Shorts-UI.

## Kalibreren

Doe dit één keer per kanaal:

1. Draai een sessie en bekijk de clips.
2. Begint het moment telkens net vóór de clip? Verhoog `--pre` of geef een negatieve
   `--offset`. Zit er veel dode tijd vóór het moment? Geef een positieve `--offset`.
3. Draai `twitch-clips process <sessie> --offset -4` en vergelijk.

Streamlink loopt standaard een paar seconden achter op de webspeler (die in
low-latency-modus speelt). Met `--streamlink-arg=--twitch-low-latency` komt de opname
dichter bij wat de chat zag.

## Afstellen

`series.csv` laat per bucket de telling, mediaan en drempel zien. Plot hem of open hem
in een spreadsheet:
- **Te veel rommel-clips:** verhoog `--min-count` of `--ratio`, of gebruik `--metric chatters`.
- **Niets gevonden:** verlaag `--min-count`. Bij kleine chats is dit echter vaak een
  signaal- en geen afstelprobleem.
- **Raids, subtrains, giveaways** geven echte chatgolven zonder clipwaardig beeld. Die
  filtert dit niet.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

De tests draaien zonder internet. Er is een nep-IRC-server voor de chat-logica
(login, PING/PONG, RECONNECT), een nep-`streamlink` die realtime MPEG-TS uitspuugt, en
een testvideo waarvan de helderheid de seconde codeert. Daarmee wordt gecontroleerd dat
een clip op de juiste seconde begint, ook over een gat van 90 s heen. Ook een volledige
`run` wordt end-to-end gedraaid. Whisper zelf wordt niet getest: de tests gebruiken een
gecachet transcript.

**Niet getest tegen echte Twitch.** De ontwikkelomgeving kon Twitch niet bereiken.
Draai dus eerst een korte proef (`--duration 600`) voordat je er een hele avond op
vertrouwt.
