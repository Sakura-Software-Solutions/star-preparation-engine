# STAR Preparation Workspace

An internal dashboard and reusable preparation engine for legacy customer CSV
and Excel defect exports. Inspect a source, map columns, define release cohorts,
resolve record issues, and download traceable data ready for STAR.

## Start locally

Python 3.10+ is sufficient for the CLI and built-in dashboard; no Python
dependencies, database service, or internet connection are needed.

```bash
./star-prep serve
```

Open **http://127.0.0.1:8080**. Choose **Try a synthetic example** to walk
through the complete flow without customer data. Local mode is passwordless
and restricted to loopback. For shared team access, follow [DEPLOYMENT.md](DEPLOYMENT.md).

For a machine on your office/home network, see [LAN.md](LAN.md). The new
`serve --lan --host <private-IP> --tls-cert <certificate> --tls-key <key>`
mode provides direct HTTPS and sign-in on port 8443 without Docker.

The dashboard stores private files and metadata in ignored `data/`.
Use `--data-dir /private/persistent/path` or `STAR_DATA_DIR` to choose another
location. Profiles in the repository are starter templates, imported on first
use. Saving a profile in the dashboard creates a new database revision; it does
not modify the repository.

## Guided workflow

1. **Source data:** upload CSV/XLSX; select worksheet, header row, encoding and
   separator. Preview the source and inspect field completeness. Excel
   relationships, shared/inline strings and 1900/1904 date systems are supported.
   Repeated headers receive positional names instead of overwriting columns.
2. **Map fields:** choose source columns for issue ID, reported date, optional
   closure date, component, severity, status and found-in version. Reuse a saved
   customer profile or start with suggested mappings.
3. **Preparation rules:** select included statuses, map version labels to
   releases, explicitly exclude unwanted versions, choose duplicate handling,
   and optionally enter release milestone dates. Confirm the meaning of the
   four rule groups. Save a reusable, versioned profile.
4. **Review & export:** inspect counts and a cumulative chart, click a release
   or decision count to see its records, and filter/search the full decision
   log. Stage corrections or exclusions with reasons, then create a new run.
   The prior run and source remain intact. Export each release to STAR when
   validation passes.

A **Ready for STAR** result means the configured rules are confirmed, no rows
remain in review, and at least one row is included. It is not an independent
engineering sign-off. A preparer is responsible for confirming business meaning;
uncertainty remains visible instead of being silently resolved.

## Counting and validation

- The internal model preserves the original issue ID, source row, status,
  source version, normalized release, component, severity and dates.
- Numeric dates use the workbook's epoch. ISO dates/timestamps are accepted;
  slash-formatted dates require an explicit day/month or month/day choice.
  Missing reported dates and invalid chronology enter review. Blank closure
  dates stay blank and are supported by STAR.
- Version policies support explicit aliases, an allow-list of normalized
  releases, single-cohort analysis, and deferred review. Under **map every
  version**, unknown/blank versions stay in review. Under **allow-list**,
  releases outside the list are excluded. Exclusions are logged.
- Duplicate policies: review, one issue per release, one issue overall, or
  every row. Collapse policies retain the entire earliest reported record;
  source-row order breaks ties. Conflicting records with validation errors
  stay in review. Metadata is not merged silently.
- Cumulative series count validated, included records by reported date and
  release. Same-day arrivals are summed. The chart includes optional release
  milestones and can be downloaded as SVG.
- Spreadsheet formulas are never evaluated or accepted as authoritative
  cached values. Formula-like export fields enter review. Audit CSVs escape
  spreadsheet formula prefixes.
- Each correction identifies a real source row, carries a reason, and is
  validated again. Revisions retain their parent run ID, actor, source/profile/
  correction hashes and exact profile snapshot.
- Limits: 25 MiB input, 100,000 source rows, 300 columns and 200 MiB expanded
  XLSX XML. The source preview displays 20 rows; the version editor displays
  up to 1,000 distinct labels. Additional labels remain reviewable through
  record corrections. History lists the latest 500 runs.

## Schneider POC

Use `schneider_poc_v2` for the provided workbook layout. The legacy
`schneider_v1` remains available for its original export schema.

The POC has **two columns named Issue**. They are retained as `Issue [3]`
(column C) and `Issue [4]` (column D). The starter profile proposes C as
reported and D as resolved; that interpretation is **unconfirmed**. The
status cohort, release grouping and duplicate policy are also unconfirmed.
The reference chart is a visual target, not an approved mapping specification.

Version-name suggestions are editable conveniences, not approved rules.
Deprecated labels are not automatically mapped. No proprietary source,
record IDs, customer-specific alias inventory, or generated output is
included in the repository or test fixtures.

## STAR export contract

The actual STAR uploaders expect:

```csv
defect_id,component,severity,arrival_date,closure_date
```

The internal field remains `source_id`; export maps it to `defect_id`.
This corrects the draft engine's incompatible export header.

Approved runs contain:

| Artifact | Purpose |
| --- | --- |
| `release-NNN-star.csv` | STAR input for one release |
| `release-manifest.json` | Maps filenames to release names and row counts |
| `star_defects.csv` | Combined five-column export; use only when intentionally importing a combined cohort |
| `prepared-defects.csv` | Rich internal data, including release and source row |
| `release-timeline.csv`, `release-totals.csv` | Reconciled cumulative series and counts |
| `decision-log.csv` | Every input row and its disposition, reason and correction |
| `profile.json`, `resolutions.json` | Exact transformation configuration |
| `profile-report.json`, `validation-report.json`, `metadata.json` | Quality summary, validation and provenance |
| `checksums.json` | SHA-256 hashes for individual artifacts |
| `preparation-bundle.zip` | All portable evidence, including the original upload for dashboard runs |

Unresolved runs generate `proposed_star_defects.csv`, never approved STAR
files. Candidate exports can contain invalid values and must not be imported.
For multiple releases, use the per-release files; the combined STAR CSV
does not carry a release column.

## CLI

```bash
./star-prep profile /path/to/customer.xlsx
./star-prep prepare /path/to/customer.xlsx \
  --profile profiles/schneider_poc_v2.json --output runs/customer-001
```

The CLI exits with code 2 for any result that is not approved (including an
empty cohort). Use a new output directory for every run. The CLI reads the
original in place and records its hash; the dashboard also retains a private
copy. CLI profiles accept `source_options` for `sheet`, `header_row`,
`encoding`, and `delimiter`.

## Development and verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/star_preparation/web_assets/app.js
```

The suite covers duplicated spreadsheet headers, worksheet selection,
encoding, date ambiguity, formula handling, release aggregation, duplicate
policies, correction provenance, STAR headers, immutable run history,
profile revision conflicts, protected artifacts, CSRF, account/session
behavior, retention and HTTP upload completion.

ASGI integration tests run when FastAPI and HTTPX are installed. The opt-in
browser test uses only synthetic data:

```bash
pip install playwright
playwright install chromium
STAR_BROWSER_TESTS=1 PYTHONPATH=src python3 -m unittest discover -s tests -p test_browser.py -v
```

It exercises mapping, rules, saved profiles, per-release download, chart,
corrections, history, and desktop/mobile rendering. Screenshots are saved to
`/tmp/star-preparation-*.png`.

## Scope

This version supports tabular CSV/XLSX exports and one trusted internal team.
It does not perform PDF/OCR extraction, execute workbook formulas/macros,
connect to issue trackers, predict reliability, or automatically upload into
STAR. Separate organizations needing isolated data should use separate
deployments and private volumes. The shared service uses provisioned internal
accounts; corporate SSO and remote object storage are future integrations.
