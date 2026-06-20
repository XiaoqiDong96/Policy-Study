# GitHub Upload Checklist

This directory is the sanitized core-code package prepared for GitHub upload.

## User Actions Needed

1. Create a new GitHub repository.
   - Recommended visibility: private while the project is still in active research.
   - Recommended repository name: `nev-policy-workflow` or `china-policy-llm-workflow`.
   - Empty repository is easiest. Do not initialize with README if you want a clean first upload.

2. Send the repository full name in this format:

```text
your-github-username/your-repository-name
```

Example:

```text
xiaoqidong/nev-policy-workflow
```

3. Tell the uploader whether to use:

```text
main
```

or another branch name.

## What Will Be Uploaded

- `README.md`
- `requirements.txt`
- `.gitignore`
- `scripts/*.py`
- `docs/*.md`
- `runbooks/*.sh`
- `runbooks/*.md`

## What Will Not Be Uploaded

- Raw corpus files such as `法律法规文件库.json`
- Candidate/output JSONL files
- Excel outputs
- `.pem` SSH keys
- `.env` files
- Model files
- `.codex_deps/`
- `outputs/`

## Safety Check Already Passed

- No file larger than 5 MB
- No private key material
- No GitHub token
- No cloud server IP
- Python scripts compile successfully

