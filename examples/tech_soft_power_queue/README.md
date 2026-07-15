# Prefecture-level technology soft-power queue

This example is a resumable, gate-driven queue for a 297-city-by-15-year
candidate panel (2012–2026).  It separates task execution from acceptance: a
successful process exit is not enough; every task must also pass the file,
JSON, and panel-shape gates declared in
`scripts/orchestrator/task_manifest.json`.

The repository intentionally includes code and documentation only.  It does
not include downloaded documents, city panels, model output, logs, caches,
credentials, or machine-specific paths.

## What the queue covers

The runnable workers cover official science-communication and inclusive-tech
lists, cultural parks and industrial heritage, innovation and industry
designations, Ministry of Education university–enterprise project edges,
conference guides and verified conference networks, green/humanistic archives,
recognition lifecycles, six-topic policy quality, and source-availability
audits.  A separate policy workflow can feed the cultural-industry panel.

The queue preserves missing values when a city–year cannot be observed.  It
does not spread national or provincial totals across cities, and it keeps
candidate, proposed, reviewed, and formally designated records distinct.

## Required project inputs

Place this example's `scripts/` directory inside a research project with this
minimum contract:

```text
PROJECT_ROOT/
  01_source_register/download_events.csv
  03_external_raw/
  04_crosswalk/city_master_297_snapshot.csv
  05_intermediate/
  06_panel/
  10_qc/
```

`city_master_297_snapshot.csv` must contain the stable prefecture identifiers
and names used by the workers.  `download_events.csv` is the provenance
registry for downloaded official files.  Some final merge inputs are produced
by upstream research pipelines and are listed in
`scripts/02_clean/build_full_candidate_panel.py`.

The cultural-policy import expects a separate `POLICY_PROJECT` that has already
finished the public workflow documented in the repository's main README.  The
import refuses to proceed until that workflow's completion flag exists.

## Runtime requirements

- Python 3.11 or newer; the queue and workers use the standard library.
- Linux utilities used when the relevant legacy formats are present:
  `pdftotext`, `antiword`, and LibreOffice.
- systemd user services are optional but recommended for unattended recovery.

No credential belongs in the manifest or service file.  Configure access on
the execution host and keep data outside version control.

## Validate and run

```bash
export PROJECT_ROOT=/path/to/tech_soft_power_project
export POLICY_PROJECT=/path/to/policy_workflow
export PYTHON="${PROJECT_ROOT}/.venv/bin/python"

cd "${PROJECT_ROOT}"
"${PYTHON}" scripts/orchestrator/cloud_queue_runner.py \
  --manifest scripts/orchestrator/task_manifest.json \
  --validate-manifest
"${PYTHON}" scripts/orchestrator/cloud_queue_runner.py --once
"${PYTHON}" scripts/orchestrator/cloud_queue_runner.py --status
```

For unattended execution, copy
`systemd/tech-soft-power-orchestrator.service` to
`~/.config/systemd/user/`, adapt only the generic project-directory names when
needed, then enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now tech-soft-power-orchestrator.service
```

The service is single-worker by design.  This keeps memory use predictable on
small cloud instances while the external policy pipeline can continue
independently.

Run the synthetic queue tests with:

```bash
python -m unittest discover -s tests -v
```

## Acceptance outputs

Runtime state and logs are written under `10_qc/orchestrator/`.  The queue is
accepted only when every task is `COMPLETE` or `SKIPPED_WITH_EVIDENCE` and the
finalizer writes:

- `all_tasks_complete.flag`
- `final_acceptance.json`
- `final_output_inventory.csv`

The final candidate panel remains a staging dataset.  Scaling, weighting, hard
versus soft indicator decisions, and construction of the composite index are
separate research-design steps.
