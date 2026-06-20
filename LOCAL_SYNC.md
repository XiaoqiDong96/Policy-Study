# Local Sync Workflow

This note documents the local workflow used in the current workspace.

The active research workspace is:

```text
/Users/xiaoqidong/Documents/法律法规处理
```

The clean GitHub package is:

```text
/Users/xiaoqidong/Documents/法律法规处理/github_core/nev-policy-workflow
```

## Sync From The Active Workspace

After editing scripts or docs in the main workspace, run:

```bash
cd "/Users/xiaoqidong/Documents/法律法规处理"
./sync_core_to_github_package.sh
```

The sync script copies only the selected core files into the GitHub package, compiles Python scripts, removes `__pycache__`, and scans for obvious secrets.

## Commit And Push

```bash
cd "/Users/xiaoqidong/Documents/法律法规处理/github_core/nev-policy-workflow"
git status
git add .
git commit -m "Update NEV policy workflow"
git push
```

If `git push` asks for authentication, complete GitHub login locally once. After that, future pushes should normally work without repeating the full login.

## First Full Push To Policy-Study

The remote repository may already contain a README-only test commit. The first full upload from this local package can safely replace it:

```bash
cd "/Users/xiaoqidong/Documents/法律法规处理/github_core/nev-policy-workflow"
git push --force-with-lease -u origin main
```

Use `--force-with-lease` only for this first full upload, because the remote repository has no real work besides the test README. After the full package is on GitHub, use normal `git push`.

## Files Synced

- `scripts/build_fulltext_candidates.py`
- `scripts/materialize_nev_audit_outputs.py`
- `scripts/nev_policy_pipeline.py`
- `scripts/ollama_cloud_adaptive_runner.py`
- `scripts/screen_related_policy_packages.py`
- `docs/新能源汽车产业政策LLM判断逻辑说明.md`
- `docs/新能源汽车产业政策完整工作流流程细节报告.md`
- `docs/新能源汽车候选筛选逻辑说明.md`
- `outputs/cloud_runbooks/start_stage1_minimax_adaptive.sh`

Raw data, model outputs, SSH keys, and generated Excel/JSONL files are intentionally not synced.
