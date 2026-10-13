# Python automobile scraper

Python replacement for the Laravel scraper in `../scrapper`. It collects AutoEvolution brands, automobiles, descriptions, press releases, gallery URLs, and engine specifications stored in scalar columns. The published archives remain available.

## Setup

Requires Python 3.11+; Python 3.12 is the tested runtime. From this directory:

```bash
uv sync --extra dev
cp .env.example .env
uv run playwright install chromium
uv run automobile-scraper init-db
uv run automobile-scraper scrape --limit 2
uv run automobile-scraper validate
```

On Linux, `uv run playwright install --with-deps chromium` installs required system libraries (requires sufficient system permissions). Without uv, create a virtual environment and run `pip install -e '.[dev]'`, then use `automobile-scraper` directly.

SQLite works immediately. For PostgreSQL use `uv sync --extra postgres` and set `SCRAPER_DATABASE_URL=postgresql+psycopg://user:password@host/database`. For MySQL use `uv sync --extra mysql` and `mysql+pymysql://user:password@host/database?charset=utf8mb4`. Use a **new dedicated database**: this schema adds constraints and SHA-256 URL hashes and is not an in-place Laravel migration. `init-db` creates missing tables. To upgrade the original **Python SQLite JSON schema**, run `uv run automobile-scraper preprocess` before other commands. This migration does not target the PHP/Laravel database.

## Preprocessing and SQLite columns

```bash
# Upgrade an existing Python JSON database, or recompute derived values.
uv run automobile-scraper preprocess
uv run automobile-scraper validate
```

The command creates `data/automobiles.before-columns-<id>.db` before upgrading the old schema, takes the same process lock as scraping, and runs DDL/backfills inside one SQLite transaction. Failed parsing rolls back the schema and data. It preserves IDs, timestamps, and checkpoint/run history. Rerunning preprocessing does not duplicate engines or photos. SQLite 3.35+ is required to remove the old columns; no network access is needed.

Every scrape and JSON import also runs the same preprocessing automatically before storage. All 71 observed source specification fields, plus an optional source engine code, have dedicated engine columns; normalized numeric columns support comparisons and filters. `engines.specs`, `automobiles.photos`, and `scrape_runs.summary` are removed. Photos are separate ordered rows in `automobile_photos`; run counts are integers. There are no JSON columns in the database schema.

Year is stored on `automobiles` as `year`, `year_end`, and `year_source`. A named year/range takes precedence; the final year in the source URL is a fallback. `Present` has a NULL end year. Country is stored on `brands` and describes the marque's country of origin, not assembly location/current ownership. The offline lookup follows the [country-of-origin reference](https://en.wikipedia.org/wiki/List_of_car_brands); unknown brands stay NULL and supplied import country values take precedence. Historical origins are used for relocated/acquired marques, including [Bufori](https://en.wikipedia.org/wiki/Bufori).

`vehicle_specs` joins engines, models, and brands into one flat row per engine option:

```sql
SELECT automobile_name, year, year_end, country,
       power_kw, torque_nm, cylinders, engine_type, fuel,
       displacement_cc, fuel_capacity_l, top_speed_kph,
       T_drive_type, T_gearbox
FROM vehicle_specs
WHERE cylinders = 6 AND power_kw >= 200
ORDER BY year DESC;
```

Source values remain verbatim in text columns (`power`, `torque`, `size`, `fuel_capacity`, `top_speed`, etc.). Numeric companions use explicit unit suffixes; missing/ambiguous measurements stay NULL. Metric values are preferred over alternate units. Fallbacks convert mph, lb-ft, inches, pounds, displacement liters, and miles. Gallons are not converted without an unambiguous liter value. Source HP/PS is treated as metric horsepower only when deriving kW without a source kW/BHP value; BHP uses mechanical horsepower. `power_hp` preserves the source HP/PS value. Cylinder layouts normalize L4/Inline 4 to I4, preserve V6/VR6/H4/etc., and keep rotary rotor counts separate from cylinders. Pure electric engines have NULL cylinder counts. Hybrids retain their combustion layout.

See [COLUMNS.md](COLUMNS.md) for the full source-field and numeric-column reference. Unmapped new source fields raise an error and keep the model transaction unchanged; add them to the registry rather than dropping their values silently.

## Full dataset and vehicle classification

`vehicle_category` groups vehicles as `car`, `truck`, `van`, `bus`, `motorcycle`, or `unknown`. `vehicle_type` retains the specific type: sedan, hatchback, wagon, coupe, convertible, roadster, limousine, SUV, crossover, minivan, MPV, pickup, truck, van, bus, or motorcycle. SUV/crossover/minivan/MPV belong to the car category; pickup belongs to truck. These are product filters, not legal weight or registration classes.

Classification uses explicit body style first, then a recognized segment. When body metadata is absent, the opening two description sentences can supply an explicit body type if they name the target model and give one unambiguous type. Comparisons, alternative bodies and styling descriptions such as "coupe-like" are excluded. `classification_source` records `page_table`, `structured_data`, `model_introduction`, `title`, `body_type`, `segment`, or `insufficient_evidence`. A model name alone does not establish a body type. Unsupported source types remain unknown. Category/type columns are indexed and included in every export and the `vehicle_specs` view.

```bash
# Import the entire existing archive; no --limit and no network needed.
uv run automobile-scraper import-json ../automobiles.json.zip
uv run automobile-scraper enrich
uv run automobile-scraper coverage
# Refresh every existing model to collect metadata absent from the archive.
uv run automobile-scraper scrape --mode update
# Discover newly listed vehicles as well.
uv run automobile-scraper scrape --mode new
# Continue interrupted work, then retry any individual failures.
uv run automobile-scraper scrape --mode resume
uv run automobile-scraper scrape --mode retry-failed
uv run automobile-scraper validate
uv run automobile-scraper export
```

The archive provides the full historical dataset shipped in this repository, not every vehicle ever built or the latest online inventory. Many archived rows lack body metadata; a full refresh fills what the source publishes. `coverage` reports unknown classifications and populated fields so missing data remains visible. The AutoEvolution crawler discovers its car database; the additional NHTSA collector below supplies truck, bus, and motorcycle model listings. Neither catalog claims to list every vehicle worldwide or fill fields a source does not publish. See the source's [cars database](https://www.autoevolution.com/cars/) and separate [motorcycle database](https://www.autoevolution.com/moto/).

```sql
SELECT vehicle_category, vehicle_type, COUNT(*) AS models
FROM automobiles GROUP BY vehicle_category, vehicle_type;
SELECT model_name, year, country, vehicle_type, fuel_type, power_kw
FROM vehicle_specs WHERE vehicle_category = 'truck';
```

## Trucks, buses, and motorcycles: NHTSA catalog

The official [NHTSA vPIC API](https://vpic.nhtsa.dot.gov/api/) provides type-filtered makes and models submitted by manufacturers. The collector requests each make using its exact ID and the requested vehicle type, preventing a mixed manufacturer such as Honda or Ford from having every model mislabeled as a motorcycle or truck.

```bash
# Bounded live smoke runs, one make per category.
uv run automobile-scraper catalog --categories bus --limit 1
uv run automobile-scraper catalog --categories truck --limit 1
uv run automobile-scraper catalog --categories motorcycle --limit 1
# Collect all unfinished makes for all three categories.
uv run automobile-scraper catalog
# Repeating resumes queued/started/failed makes; completed makes are skipped.
# Explicitly refresh completed makes too:
uv run automobile-scraper catalog --refresh
# Read progress across all source databases while collectors are running:
uv run automobile-scraper status
# Export a combined model catalog at any point (a snapshot during collection is partial):
uv run automobile-scraper export-catalog
```

NHTSA listings live in `data/catalogs/nhtsa/vehicles.db`, with scalar `vehicles`, `work`, and `runs` tables. The source catalog describes make/model/type identity; the main database describes year-specific models and engine variants. Each has its own writer lock. Raw API responses are captured under the catalog directory; requests are sequential, paced, and retry transient failures. Saved data and completed checkpoint commit atomically per make/type. A failed refresh preserves the previous model list. Authorization failures stop the collector.

NHTSA's `Truck` type includes light and heavy trucks. These API responses do not establish weight class, generation, model year, engine, seats, or dimensions. The motorcycle collector below supplies year-specific specifications, and the manufacturer adapters capture technical tables for the supported truck and bus families. Other families still need additional specification sources or VIN input.

`export-catalog` produces `exports/vehicle-catalog.zip` with flat JSON and CSV listings from the source databases. Rows include source, source ID, make/model, category/type, nullable year bounds, source URL, and detail level (`model_listing` or `model_and_variant_specs`). Names shared by sources remain separate records; they are not automatically merged. Existing `export` continues to produce the full car model/engine/photo tables. Combined JSON/CSV use the same captured rows, even if the NHTSA collector is running.

```sql
-- In data/catalogs/nhtsa/vehicles.db:
SELECT make_name, model_name, vehicle_category, source_url
FROM vehicles WHERE vehicle_category IN ('truck', 'bus', 'motorcycle');
SELECT status, COUNT(*) FROM work GROUP BY status;
```

## Motorcycle and powersports technical specifications

```bash
# Counts detail/discovery pages, not makes; start with a small sample.
uv run automobile-scraper motorcycles --limit 5
# Resume all unfinished discovery and detail pages.
uv run automobile-scraper motorcycles
# Wait for a running car crawl to finish, then collect the entire moto database.
uv run automobile-scraper motorcycles --wait
# Rediscover and refresh completed pages too.
uv run automobile-scraper motorcycles --refresh
uv run automobile-scraper export-motorcycles
uv run automobile-scraper export-catalog
```

This source follows the actual directory → brand → model family → year-specific generation links. It stores model metadata and technical specifications in scalar columns in `data/motorcycles/vehicles.db`, with separate ordered photo rows and URL checkpoints. Because the source has one technical specification set per generation page, each vehicle row includes that set directly. Conflicting variants fail explicitly rather than silently replacing one another.

All source specification labels are registered in `spec_columns`; raw values receive dedicated `spec_<section>_<field>` TEXT columns. Known power, displacement, gearbox, weight, dimension, fuel-capacity and performance fields also populate the shared normalized columns. New source fields are retained; conflicting column names fail and roll back both schema and data. Missing fields are NULL. Refreshes remove old source values no longer present and replace photos atomically.

The engine `Type`/`Engine type`/`Cylinders` labels also populate `cylinders_raw`. Explicit descriptions such as `single-cylinder` or `twin cylinder` supply counts, while `V-twin` and `parallel twin` supply V2/I2 layouts. Stroke and valve counts do not establish cylinder counts; conflicting alternatives remain unknown. Original descriptions remain in their registered source columns.

The moto database also contains powersports vehicles. Recognized ATV, UTV and snowmobile segments keep those categories; unsupported segments remain unknown. It does not classify every entry as a motorcycle simply because its URL is under `/moto/`.

Car and motorcycle bulk crawls share one source lock. `--wait` queues the motorcycle process behind the car crawl; NHTSA uses a separate domain and lock. Partial page failures stay retryable; three consecutive failures or an authorization failure stop the motorcycle run. The next invocation resumes unfinished work without duplicating models/photos.

`export-motorcycles` produces `exports/motorcycles.zip` with flat JSON/CSV for vehicles, photos and the source-column registry. The combined `vehicle-catalog.zip` now includes `autoevolution_moto` model listings as well as cars and NHTSA listings. Exports read the source databases and use their own output locks; they can run while collection continues. A snapshot taken during collection is partial, and names shared across sources are not automatically merged.

## Official heavy-truck and bus specification tables

```bash
uv run automobile-scraper manufacturers
uv run automobile-scraper manufacturers --refresh
uv run automobile-scraper export-manufacturers
uv run automobile-scraper export-catalog
```

The official manufacturer adapters capture fifteen model families:

- Scania R-, S-, G-, P- and L-series, using the UK specification pages, including [R-series](https://www.scania.com/uk/en/home/products/trucks/r-series/r-series-specifications.html) and [P-series](https://www.scania.com/uk/en/home/products/trucks/p-series/p-series-specifications.html).
- Mercedes-Benz Citaro, eCitaro, Tourismo, Intouro, Conecto and CapaCity, using the international specification pages, including [Citaro](https://www.mercedes-benz-bus.com/int/en/models/citaro/facts-citaro.html) and [Tourismo](https://www.mercedes-benz-bus.com/int/en/models/tourismo-rhd/facts-tourismo-rhd.html).
- Setra ComfortClass 500 HD, TopClass 500 HDH, TopClass S 531 DT and MultiClass 500, using the international specification pages, including [ComfortClass](https://www.setra-bus.com/int/en/models/cc-hd-models/facts-cc-hd-models.html) and [MultiClass](https://www.setra-bus.com/int/en/models/mc-le-models/facts-mc-le-models.html).

These families do not cover all truck and bus brands or markets. Add reviewed sources to `sources/manufacturers.py` as coverage grows. Classification describes the family product type, not the weight class of every configuration; P-/L-series retain the general `truck` type.

`data/manufacturers/specifications.db` contains model identity and complete source tables in scalar relational rows. `specification_tables` identifies each table and heading; `specification_cells` stores row/column positions, spans, header status and original value. This preserves alternative engines, chassis, seating layouts and equipment matrices without inventing a single configuration. Values remain source text; these matrices are not yet normalized into individual engine/chassis configurations.

The `powertrain_specs` view exposes Scania's row-oriented powertrain options in named scalar columns: engine code, output, torque, fuel, emission technology, engine PTO, propulsion/regeneration power, gearbox, installed battery capacity, charging power and PTO options. It preserves source values and alternative capacities without unit conversion or choosing a configuration. Source URL, table position, row position, heading and `source_tab` identify the original evidence. Tab labels such as `Euro 6` or `Electric` come from the page's accessible tab references and are stored in `specification_context`; absent labels stay NULL. Unit/context rows remain in `specification_cells`.

Running `manufacturers` installs/upgrades the specification views and backfills missing table context in older databases. Backfill accepts only a successful GET capture whose HTML SHA-256 matches the saved model, preserving source matrices and fetch timestamps. Corrupt captures are reported; a missing matching capture leaves context unknown. Completed source pages remain skipped unless `--refresh` is supplied.

For Mercedes-Benz and Setra, `bus_variant_fields` associates each column header with its published variant and all labeled source cells. It records source section, table/row/column positions and the variant column position, including values shared through column spans. `bus_variant_specs` exposes named columns for dimensions, gross weight, seating/passenger capacity, up to four explicitly labeled engine/output/displacement/transmission options, torque, emissions, electric drive, battery options and charging technology. Values remain source text: `12.135` under `[mm]` is not converted, and `265 / 220` is not split into guessed configurations. Conflicting source values leave the summary column NULL; inspect `bus_variant_fields` for every original value. Variant names, rather than column positions, link tables whose columns are reordered. The views cover reviewed single-row labels and single-column variant headers; original matrices retain other layouts and footnotes. View definitions update transactionally when the manufacturer store opens, preserving captured records.

The collector checks the requested model title and requires tables, rejecting generic error pages even when they return HTTP 200. It shares one HTTP session, pacing gate and robots cache per host, with bounded retries and raw capture. Data replacement and completed checkpoint commit atomically per page; failures preserve the last successful tables. Repeating skips completed pages unless `--refresh` is supplied. All source jobs are registered before requests, and authorization failures stop collection. Reports include skipped pages, failures with source URLs, request/retry counts and remaining checkpoint states. These manufacturer domains have their own source lock.

`exports/manufacturers.zip` contains JSON/CSV for models, tables, cells, table context, powertrain options, bus variants and variant fields from one database snapshot. `vehicle-catalog.zip` includes these model families with `detail_level=specification_tables`; no model years are inferred. NHTSA identities are kept separate until a reviewed cross-source mapping exists. `status` reports manufacturer model, table, cell, powertrain-option and bus-variant counts alongside collection checkpoints while collectors run.

```sql
-- In data/manufacturers/specifications.db:
SELECT m.make_name, m.model_name, t.heading,
       c.row_position, c.column_position, c.row_span, c.column_span, c.value
FROM models m JOIN specification_tables t USING(source_url)
JOIN specification_cells c USING(source_url, table_position)
WHERE m.vehicle_category = 'truck'
ORDER BY m.source_url, t.table_position, c.row_position, c.column_position;

SELECT make_name, model_name, engine_code, output, torque, fuel, source_heading,source_tab
FROM powertrain_specs WHERE output IS NOT NULL;

SELECT variant_name,length_mm_text,seats_standard_text,engine,output_kw_text,
       engine_2,output_2_kw_text,electric_drive,battery_capacity_options_text
FROM bus_variant_specs;
```

## Vehicle and engine details

Vehicle rows now include clean model name, model family, body style and normalized body type, segment, production years text, assembly location, infotainment, autonomy level, main image URL, and extraction provenance. Doors, seats, generation, trim, and vehicle configuration are stored when explicitly supplied. Configuration preserves the general source description separately from trim.

Engine rows include powertrain type (combustion, electric, hybrid variants, fuel cell), fuel type, aspiration, injection type, transmission type, drive layout, explicit electric motor count, and optional engine code alongside the existing technical specifications.

```bash
# Upgrade/backfill scalar columns and classify existing engines without network requests.
uv run automobile-scraper preprocess
# Fill vehicle metadata from the latest successful cached pages for existing models.
uv run automobile-scraper enrich
# Wait for a running car/motorcycle collector to release its lock first.
uv run automobile-scraper enrich --wait
# Fetch fresh details for existing models, with a bounded sample.
uv run automobile-scraper scrape --mode update --limit 2
```

`enrich` reads `data/raw` by default; use `--raw-dir /path/to/raw` for another capture directory. It preserves engine specs, photos, and checkpoints. Missing cached metadata preserves existing values, and classification is recomputed from that merged evidence so a page without body metadata does not erase a known category. New scrapes extract these fields automatically, and flat exports/imports retain them. Only cached models can be enriched offline. Missing source information remains NULL: an absent turbo label does not establish natural aspiration, and a generic two-door body-style label does not establish the actual door count. Infotainment support is true only when explicitly named; missing text is unknown.

```sql
SELECT model_name, model_family, body_type, segment, year, country,
       powertrain_type, fuel_type, aspiration, injection_type,
       transmission_type, drive_layout, power_kw, torque_nm
FROM vehicle_specs
ORDER BY year DESC;
```

## Operations

```bash
# Discover brands/models, skip models already saved (default).
uv run automobile-scraper scrape --mode new
# Refresh all discovered models without deleting the database.
uv run automobile-scraper scrape --mode all
# Refresh only models already in the database; no discovery requests.
uv run automobile-scraper scrape --mode update
# Continue queued, interrupted, or failed work without rediscovering URLs.
uv run automobile-scraper scrape --mode resume
uv run automobile-scraper scrape --mode retry-failed
uv run automobile-scraper scrape --mode brands
# Limit the number of model detail jobs; discovery still runs for new/all.
uv run automobile-scraper scrape --limit 5
```

Run one process per database, using the same `SCRAPER_DATA_DIR`. A file lock prevents overlapping commands using that directory; this is a single-host pipeline, not a distributed worker service. `SCRAPER_CONCURRENCY` bounds model workers and browser pages. A shared request gate enforces `SCRAPER_REQUEST_INTERVAL` between page requests. Robots rules and crawl delay are checked by default. Requests retry transient failures with bounded exponential backoff and jitter; authorization errors fail. Browser navigation has its own subresource traffic, so pacing applies to page requests, not each image/script.

A model's data, engines, and completed checkpoint commit together. Failed updates preserve previous data. Successful updates remove engine variants absent from the new page. Restart `started` jobs using `resume`. Final JSON reports attempted/succeeded/skipped/failed counts, requests and retries; partial failures exit with status 1. Logs go to stderr as JSON. Run history is in `scrape_runs`, work state in `scrape_work`.

Raw HTML and request metadata are captured under `data/raw/<run-id>/` before parsing, including HTTP error responses. These files are retained for diagnosis; delete older run directories when no longer needed. Press releases are requested only when a source ID is present. Missing critical selectors or malformed gallery data fail explicitly. Specification values keep their source capitalization and units; section/field labels retain the PHP title-case convention. Description/press-release attributes are stripped; treat source HTML as untrusted when rendering it downstream.

## Import the existing data

```bash
uv run automobile-scraper import-json ../automobiles.json.zip
uv run automobile-scraper validate
```

The importer streams JSON and stages engines in temporary SQLite. It remaps foreign keys, recomputes URL hashes, validates records, and commits the whole archive atomically. Repeating an import updates rows without duplication. IDs and timestamps are assigned by the new database; they are not preserved from Laravel. Importing does not create scrape checkpoints because imported models already count as saved. No live requests are needed.

## Export

```bash
uv run automobile-scraper export
uv run automobile-scraper export --formats json,csv --output ./exports
```

Produces `automobiles.{json,csv,xml,sql}.zip`, each containing `brands`, `automobiles`, `engines`, and `automobile_photos` files. All exported database fields are scalar; JSON exports contain arrays of flat rows with no nested specification/photo payloads. The importer accepts both original nested archives and these new flat archives. CSV columns match the actual model schema, fixing the old automobile headings. Exports stream rows and atomically replace each finished archive. Defaults write into `exports/`, keeping the repository's published archives intact.

SQL is a **data-only** dump for an empty Python SQLite/PostgreSQL schema, with standard quoted identifiers/literals. It is not `mysqldump`, does not include schema or checkpoints, and is not a Laravel/MySQL dump replacement. After a PostgreSQL SQL import, reset serial sequences before writing new rows. Prefer JSON import for portable transfer, including MySQL.

## Architecture

```text
src/automobile_scraper/
  cli.py          Typer commands, process lock, JSON logging
  config.py       validated environment settings
  domain.py       Pydantic extraction contracts
  fetching.py     Playwright/HTTPX, robots, pacing, retries, raw capture
  parsers.py      pure BeautifulSoup/lxml source parsers
  pipeline.py     discovery, bounded workers, run/checkpoint transitions
  preprocessing.py scalar specifications, metric values, cylinder layouts, years
  spec_fields.py  explicit source-field/column registry
  vehicle_information.py vehicle metadata and conservative engine classifiers
  enrichment.py   replay cached vehicle metadata
  reporting.py    category and metadata coverage reports
  catalog_export.py cross-source model catalog export
  sources/nhtsa.py paced vPIC collector and independent source catalog
  sources/motorcycles.py motorcycle discovery, detail parsing and scalar source storage
  sources/manufacturers.py official commercial-vehicle specification matrices
  countries.py    offline country-of-origin enrichment
  migrations.py   transactional SQLite migration and backfill
  storage.py      SQLAlchemy schema, transactions, idempotent writes
  importing.py    streaming archive migration
  exporting.py    streaming ZIP exporters
```

This is an ETL pipeline: capture raw HTML, parse/validate domain records, preprocess them into scalar columns, then transactionally store relational data. Async I/O handles network/browser concurrency; short synchronous database transactions keep SQLite simple. SQLite is the local default; PostgreSQL/MySQL are optional drivers. Playwright matches the original Chromium workflow; set `SCRAPER_TRANSPORT=http` for sites/pages that work without JavaScript. There is no web server or task broker to operate.

The source selectors come from the existing PHP implementation. Automated tests use representative local HTML and mocked HTTP requests; they do not certify that the live website still exposes the same markup or grants access. Start with a bounded sample before a full scrape.

## Development

```bash
uv run pytest
# Include the local real-browser smoke test after installing Chromium.
RUN_BROWSER_TESTS=1 uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests cover parser contracts/drift, retries, POST preservation, robots denial, interrupted work, atomic rollback, failure recovery, idempotency, update-only selection, legacy schema rejection, orphan rollback, and JSON/CSV/XML/SQL round trips. The opt-in browser test sends POST requests to a local server using real Chromium.

The original archive was imported idempotently. Column migration preserves existing IDs, timestamps, record counts, source field values, photos, and run history. Cached enrichment changes model metadata and updates the model timestamp when values change. A previous HTTP smoke request returned 403; the default browser transport remains available. Current cached pages were replayed successfully for vehicle metadata enrichment. PostgreSQL/MySQL connections and Docker execution have not been exercised in this workspace.

## Docker

```bash
docker build -t automobile-scraper .
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/exports:/app/exports" automobile-scraper scrape --limit 2
```

The image includes Chromium and runs as an unprivileged user. Ensure bind-mounted directories are writable by that user. Pass configuration through `--env-file .env` or `-e SCRAPER_DATABASE_URL=...`; external database drivers require adding the relevant project extra to the image install step. Do not add a database service solely to run the SQLite default.
