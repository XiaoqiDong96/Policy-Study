# China Industry Policy Workflow

An auditable workflow for identifying industry-related Chinese policy documents, determining whether they are industrial policies, classifying policy instruments, and building monthly policy panels.

The repository contains reusable code and methodological documentation. It does not contain the source legal-regulation corpus, row-level research data, model responses, panel outputs, credentials, or machine-specific configuration.

## Supported domains

- New energy vehicles
- Artificial intelligence
- Six future-industry groups
- Low-altitude economy
- Cultural and related industries

The domain layer is configurable, so the same pipeline can be extended to another industry without rewriting the classification engine.

## Workflow

```text
Raw policy corpus
  -> deterministic HTML/text cleaning
  -> high-recall domain screening
  -> evidence-pack construction for long documents
  -> Stage 1 industrial-policy classification
  -> Stage 2 review of boundary cases
  -> policy-instrument classification
  -> central/provincial/prefecture monthly panels
  -> disagreement and audit samples
```

Screening is deterministic and does not consume model quota. LLM stages are resumable JSONL jobs, and completed records are preserved across restarts.

## Repository layout

- `scripts/`: screening, classification, review, tool coding, panel construction, and limit protection
- `runbooks/`: shell entry points for local or remote execution
- `docs/`: methodology and extension notes
- `requirements.txt`: Python dependencies

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

For model-backed stages, install Ollama separately, sign in if cloud models are used, and verify that the configured model is available:

```bash
ollama serve
ollama list
```

No API key or token should be written into this repository. Use the model provider's login mechanism or environment variables on the execution machine.

## Minimal examples

Set paths outside the repository:

```bash
export POLICY_CORPUS="${DATA_ROOT}/policy_corpus.json"
export POLICY_OUTPUT="${OUTPUT_ROOT}/policy_workflow"
```

Screen future-industry and low-altitude candidates without an LLM:

```bash
python scripts/screen_future_low_altitude_policy_packages.py \
  --input "${POLICY_CORPUS}" \
  --output-root "${POLICY_OUTPUT}/candidates"
```

Screen cultural-industry candidates:

```bash
python scripts/screen_culture_industry_policy_packages.py \
  --input "${POLICY_CORPUS}" \
  --output-root "${POLICY_OUTPUT}/culture_candidates"
```

Run the generic industrial-policy classifier for a configured domain:

```bash
python scripts/domain_policy_pipeline.py classify \
  --domain-key future_industries \
  --input dummy \
  --existing-candidates "${POLICY_OUTPUT}/candidates/future_industries/candidates.jsonl" \
  --output-dir "${POLICY_OUTPUT}/future_industries/stage1" \
  --model minimax-m2.5:cloud \
  --prompt-mode standard \
  --parallel-docs 4 \
  --resume
```

Runbook defaults are examples. Review model names, concurrency, context length, and output roots before a full run.

## Output contract

The pipeline writes generated files outside version control. Depending on the domain and stage, outputs include:

- candidate JSONL with screening evidence
- one classification record per document
- boundary-case and disagreement subsets
- policy-instrument labels and supporting excerpts
- document-level audit tables
- monthly panels by administrative level and industry category

National, provincial, and prefecture policies are kept as separate panels. A national policy is not mechanically duplicated into every city unless a downstream research design explicitly requests that transformation.

## Extending the workflow

Start with [Extending to a New Industry](docs/EXTENDING_TO_A_NEW_INDUSTRY.md). Define the domain taxonomy and exclusion boundary first, audit a sample of deterministic screening results, then configure the shared LLM and panel stages.

## Public repository boundary

Only code, small synthetic fixtures, and public documentation belong here. Keep all original documents, candidate packages, classifications, panels, logs, model caches, credentials, and execution-specific paths outside the repository.

