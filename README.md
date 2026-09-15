# STAR Preparation Engine

A standalone, local command-line tool that converts customer CSV/XLSX defect
exports into a traceable, dashboard-compatible STAR defect CSV. It has no
dashboard, database, Docker, AWS, or LocalStack dependency.

## Quick start

Requires Python 3.10+ (the current implementation uses only the standard
library). The project-local launcher works even on systems where Debian's
optional `python3-venv` package is not installed.

```bash
cd star-preparation-engine
./star-prep profile /path/to/Customer_issues.xlsx
./star-prep prepare /path/to/Customer_issues.xlsx \
  --profile profiles/schneider_v1.json \
  --output runs/schneider-001
```

`prepare` always writes its audit artifacts. It exits with code `2` when
human review is required; that is the expected behavior for the supplied
Schneider profile until an authorized engineer confirms the version-cohort rule.

## Local admin UI

An authorized preparation administrator can manage a profile and prepare a
local file without editing JSON:

```bash
./star-prep serve
```

Open `http://127.0.0.1:8080`. The server is intentionally bound to localhost;
there is no authentication because it is not exposed to a network. It writes
profiles to `profiles/` and artifacts to `runs/`.

## Data-preparation workflow

This section is the operating procedure for the current local prototype. The
preparation administrator confirms the engineering rules; the tool performs
only the configured, traceable transformations.

### 1. Start the local UI

From this project directory, run:

```bash
./star-prep serve
```

Keep that terminal running and open `http://127.0.0.1:8080` in a browser.
No dashboard, LocalStack token, Docker container, virtual environment, or
internet connection is required.

### 2. Review and save a profile

In **Schneider/profile rules**, select `schneider_v1.json`. Review the
source-column mappings before preparing a file:

| STAR field | Schneider source column |
| --- | --- |
| `source_id` | `Issue id` |
| `component` | `Component/s` |
| `severity` | `Priority` |
| `arrival_date` | `Created` |
| `closure_date` | `Resolved` |
| `found_in_version` | `Affects Version/s (Found In)` |
| `status` | `Status` |

Then set the engineering rules:

1. Enter the statuses that are eligible for the STAR cohort.
2. Keep **Version policy** as **Human review required** until the target
   release/version cohort is agreed.
3. After approval, select **Allow-list** and add one accepted canonical
   version per line.
4. Where two source labels are equivalent, add an alias in the form
   `raw source value => canonical value`.
5. Select **Save profile**. A saved profile is the reproducible record of the
   decisions used for preparation.

Do not choose **Ignore version** unless an engineer has explicitly agreed
that the version field is irrelevant to the analysis.

### 3. Prepare the customer file

In **Prepare data**, select the customer's `.xlsx` or `.csv` export and
choose **Prepare and validate**. The source file is copied to that run's
artifact folder without being changed.

The result shows the count of rows that were included, excluded, or require
review. Download and inspect these artifacts:

| Artifact | Use |
| --- | --- |
| `profile-report.json` | Source schema, blanks, and frequent values |
| `validation-report.json` | Overall record counts and approval state |
| `decision-log.csv` | The disposition and rule behind every input row |
| `proposed_star_defects.csv` | Candidate data; not approved for dashboard import |
| `star_defects.csv` | Approved STAR input, produced only when no review is pending |
| `metadata.json` | Input/profile hashes and run timestamp for reproducibility |

### 4. Approve or correct rules

If the result says **Not approved**, inspect `decision-log.csv`, correct the
profile rules or aliases, save the profile, and run again. Do not upload
`proposed_star_defects.csv` to the dashboard.

When the result says **Approved STAR input generated**, retain the artifacts
with the project record and use `star_defects.csv` as the input for the
dashboard's defect upload.

### 5. Preserve the decision trail

For every customer run, retain the source export, the profile used, the
decision log, and validation report. This is what allows an engineer to
explain why a record was included, excluded, or mapped a particular way.

## Output contract

An approved run writes `star_defects.csv`; a run awaiting engineering approval
writes `proposed_star_defects.csv`. Both use the dashboard's existing
defect-upload schema:

```csv
source_id,component,severity,arrival_date,closure_date
```

Each run also produces a profile report, validation report, decision log, and
metadata including hashes of the input and profile. The raw source is never
modified.

## Rule profiles

Profiles are versioned JSON documents. The Schneider starter profile records
the source-field mapping and normalizations, but deliberately leaves release
cohort eligibility in `review_required` mode. After an authorized engineer
approves the rule,
update its `version_policy` to `allow_list` and add the accepted values or
patterns. Every change is then reviewable and reproducible.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
