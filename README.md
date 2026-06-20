# NEV Policy Workflow

Core code for screening Chinese policy documents related to new energy vehicles (NEV), classifying whether candidates are industrial policies with LLMs, and producing auditable panel-style outputs.

This repository intentionally excludes raw legal-regulation corpora, model outputs, cloud keys, API tokens, generated Excel files, and large JSONL packages.

## What Is Included

- `scripts/screen_related_policy_packages.py`  
  BeautifulSoup + keyword/rule-based screening for NEV and AI policy candidates.
- `scripts/build_fulltext_candidates.py`  
  Backfills full text for previously screened candidate IDs from the original large corpus.
- `scripts/nev_policy_pipeline.py`  
  Main pipeline for deterministic evidence packs, LLM classification, adversarial prompts, tool classification, administrative-level panel construction, and resumable output.
- `scripts/ollama_cloud_adaptive_runner.py`  
  Adaptive cloud runner for long Ollama jobs. It resumes JSONL output, handles 429/session/weekly limit signals, and adjusts concurrency.
- `scripts/materialize_nev_audit_outputs.py`  
  Converts JSONL classification results into per-document text files, Excel review files, and folders for disagreement/boundary samples.
- `docs/`  
  Chinese documentation explaining keyword screening, LLM judgment logic, and the full workflow.
- `runbooks/`  
  Minimal shell entry point for the current MiniMax first-stage cloud run.

## Data Not Included

The pipeline expects local files such as:

- Raw corpus JSON, for example `法律法规文件库.json`
- Candidate packages such as `outputs/policy_packages/new_energy_vehicle_evidence_pack/candidates_evidence_pack.jsonl`
- Classification outputs under `outputs/`

These are excluded because they are too large and may contain sensitive or licensed source material.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Optional but recommended for LLM classification:

```bash
ollama serve
```

For Ollama Cloud, sign in on the machine that will run classification:

```bash
ollama signin
```

## Typical Workflow

1. Screen candidate policies from the raw corpus:

```bash
python scripts/screen_related_policy_packages.py \
  --input /path/to/法律法规文件库.json \
  --output-dir outputs/policy_packages
```

2. Build or backfill full-text/evidence-pack candidates:

```bash
python scripts/build_fulltext_candidates.py \
  --corpus /path/to/法律法规文件库.json \
  --candidates outputs/policy_packages/new_energy_vehicle/candidates.jsonl \
  --output outputs/policy_packages/new_energy_vehicle_fulltext/candidates_fulltext.jsonl
```

3. Run classification with resume support:

```bash
python scripts/nev_policy_pipeline.py classify \
  --input dummy \
  --existing-candidates outputs/policy_packages/new_energy_vehicle_evidence_pack/candidates_evidence_pack.jsonl \
  --output-dir outputs/nev_policy_panel/stage1_minimax_adaptive \
  --candidates-name nev_stage1_candidates_norm.jsonl \
  --classified-name nev_stage1_minimax.jsonl \
  --model minimax-m2.5:cloud \
  --prompt-mode standard \
  --parallel-docs 8 \
  --ollama-format auto \
  --long-doc-mode evidence_pack \
  --num-ctx 16384 \
  --max-body-chars 8000 \
  --resume
```

4. For long cloud runs, use the adaptive runner:

```bash
bash runbooks/start_stage1_minimax_adaptive.sh
```

5. Materialize audit outputs:

```bash
python scripts/materialize_nev_audit_outputs.py \
  --classified outputs/nev_policy_panel/stage1_minimax_adaptive/nev_stage1_minimax.jsonl \
  --output-dir outputs/nev_policy_panel/audit_materials
```

## Notes

- The current first-stage strategy is fast single-model MiniMax screening, followed by multi-model review only for boundary, low-confidence, or non-unanimous cases.
- `--proactive-session-break-minutes` defaults to `0`; the runner waits only when real rate/session/weekly limit signals appear.
- Panel construction separates central, provincial, and prefecture-level policy panels rather than spreading national policies mechanically into every city panel.

