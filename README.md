# Nybolig-monitor Akershus

Daglig overvåkning av nyboligprosjekter i Asker, Bærum, Nordre Follo og Ås. Henter data fra Finn.no, lagrer historikk i SQLite, og viser et dashbord med antall til salgs, pris/m² og estimert salg siste uke/måned/12 måneder.

## Slik virker det

1. GitHub Actions kjører hver morgen kl. 06:00 norsk tid
2. Scraperen henter Finn-søkeresultatene for hver kommune, finner alle "project"-annonser (de med enhetsliste)
3. For hvert prosjekt parses tabellen med enheter (enhetsnummer, BRA, pris, soverom)
4. Snapshot lagres i `nybolig.db`
5. `build_dashboard.py` genererer `dashboard/index.html`
6. Workflow committer database og dashboard tilbake til repoet

## Salg-estimering

"Solgt siste uke" beregnes ved å sammenligne dagens snapshot med snapshot fra ca 7 dager tilbake. En enhet som var listet til salgs da, men er borte eller markert solgt nå, regnes som solgt i perioden. Tilsvarende for måned og 12 måneder.

Dette er en estimering — ikke en eksakt tall. Begrensninger:

- Enheter som forsvinner fordi annonsen trekkes (f.eks. omstrukturering) regnes feilaktig som solgte
- Nye enheter som lanseres mellom snapshots regnes ikke
- Hvis prosjektet er nytt i overvåkningen og det ikke finnes baseline, vises 0

## Komme i gang

### 1. Sett opp repo

Last opp filene til et GitHub-repo. Settings → Actions → General → "Read and write permissions".

### 2. Verifiser at det kjører

Actions-fanen → "Daglig nyboligscraping" → "Run workflow". Forventet kjøretid: 5–15 minutter avhengig av antall prosjekter.

### 3. Se dashbordet

Dashboard-fila ligger i `dashboard/index.html` etter hver kjøring. Du kan:

- Last den ned manuelt fra repoet og åpne i nettleseren
- Aktivere GitHub Pages (krever public repo eller Enterprise) for permanent URL

## Konfigurasjon

Rediger `scraper/config.py`:

- `MUNICIPALITIES` — legg til/fjern kommuner. Finn-koden finner du ved å gå til finn.no/realestate/newbuildings, velge kommunen, og kopiere `location`-parameteren fra URL.
- `DELAY_BETWEEN_REQUESTS_S` — pause mellom requests. Vær snill mot Finn (4 sekunder er forsiktig).

## Begrensninger og forbehold

**Solgte enheter er skjult som standard.** Finn skjuler solgte enheter bak en "Vis solgte enheter"-knapp. Vi ser bare usolgte enheter, men siden vi sammenligner snapshots blir det riktig for salgs-tellingen.

**Finns ToS forbyr systematisk innsamling.** Verktøyet er greit for personlig bruk med moderat rate. Vurder om det går for kommersielt bruk i bedrift.

**Strukturendringer på Finn vil bryte parseren.** Hvis dashbordet plutselig viser 0 enheter for alle prosjekter, har Finn endret HTML-struktur og parseren må oppdateres.

**Enkelte prosjekter har flere salgstrinn på ulike Finn-annonser.** Disse vises som separate prosjekter i dashbordet (en rad per annonse). Det er ikke sammen-summert. Du kan filtrere på tittel for å gruppere.

## Utvikling og testing

Lokalt:

```
pip install -r requirements.txt
python -m scraper.run --limit 3 --municipality Bærum  # Begrenset test
python build_dashboard.py
open dashboard/index.html
```

Tester:

```
python tests/test_parser.py
```
