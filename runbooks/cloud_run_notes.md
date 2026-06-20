# Cloud Run Notes

This project can be run on a remote Linux server with Ollama installed and signed in.

## Minimal Setup

```bash
sudo apt update
sudo apt install -y python3-venv tmux
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
ollama signin
```

Upload the repository code plus the required local candidate JSONL files, then start a resumable tmux job:

```bash
tmux new-session -d -s nev_stage1_minimax_adaptive \
  "bash -lc 'cd ~/nev_policy_project && bash runbooks/start_stage1_minimax_adaptive.sh 2>&1 | tee -a logs/nev_stage1_minimax_adaptive.tmux.log'"
```

Check progress:

```bash
wc -l outputs/nev_policy_panel/stage1_minimax_adaptive/nev_stage1_minimax.jsonl
tail -n 80 outputs/nev_policy_panel/stage1_minimax_adaptive/adaptive_runner.log
cat outputs/nev_policy_panel/stage1_minimax_adaptive/adaptive_state.json
tmux ls
```

Stop safely:

```bash
tmux kill-session -t nev_stage1_minimax_adaptive
```

Restarting the same command is safe when `--resume` is enabled; completed JSONL rows are preserved.

