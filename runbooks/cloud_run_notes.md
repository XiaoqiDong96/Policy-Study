# Remote execution

The same scripts can run on a persistent Linux host. Keep connection details, SSH keys, source documents, and generated outputs outside the repository.

## Prepare the host

```bash
sudo apt update
sudo apt install -y python3-venv tmux
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Install Ollama separately and sign in on the execution host when using cloud models.

## Start a resumable job

From the repository root:

```bash
tmux new-session -d -s policy_stage1 \
  "bash -lc '. .venv/bin/activate && bash runbooks/start_stage1_minimax_adaptive.sh'"
```

Use environment variables or command-line arguments to point to candidate and output locations. Do not edit private paths or credentials into runbooks.

## Inspect and resume

```bash
tmux ls
tail -n 80 "${RUN_LOG}"
wc -l "${CLASSIFIED_JSONL}"
```

Stopping the tmux session does not remove completed JSONL rows. Restart the same command with resume enabled to continue from the existing output.

