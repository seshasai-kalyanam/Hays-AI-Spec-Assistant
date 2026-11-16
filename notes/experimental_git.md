# Experimental Git branch workflow

Use a short-lived branch to try a new feature without changing `main`. This project is a Streamlit app (`app.py`); the same `.streamlit/secrets.toml` works on the branch if the experiment does not change database or Auth0 behavior.

## Before you start

- Commit or stash any work-in-progress on your current branch.
- Work from the repo root: `C:\GitHub\hays\hays-meetings`

```powershell
git status
```

## 1. Create an experimental branch

Start from up-to-date `main`:

```powershell
git checkout main
git pull
git checkout -b feature/citation_viewer
```

Use a clear branch name, e.g. `feature/pdf-viewer` or `experiment/report-preview`.

## 2. Develop and test locally

Edit code as usual. Run the app from the repo root:

```powershell
streamlit run app.py
```

If Streamlit is already running, stop it and restart after switching branches so it loads the branch code. To compare with `main` on another port:

```powershell
streamlit run app.py --server.port 8502
```

Run tests before merging:

```powershell
python -m pytest tests/ -q
```

Commit as you go:

```powershell
git add .
git commit -m "WIP: describe what you tried"
```

Push the branch (optional, for backup or a PR):

```powershell
git push -u origin feature/short-description
```

## 3. Bring the experiment back into `main`

### Option A — Merge locally (simple)

```powershell
git checkout main
git pull
git merge feature/short-description
python -m pytest tests/ -q
git push
```

### Option B — Pull request (recommended for review)

```powershell
git push -u origin feature/short-description
gh pr create --title "Add short description" --body "Summary of the experiment and test plan."
```

After the PR is approved and merged on GitHub:

```powershell
git checkout main
git pull
```

### Option C — Only part of the work is worth keeping

Cherry-pick specific commits onto `main`:

```powershell
git checkout main
git pull
git cherry-pick <commit-hash>
```

## 4. Clean up the experimental branch

After the feature is merged (or abandoned):

```powershell
git branch -d feature/short-description
git push origin --delete feature/short-description
```

To keep a snapshot without keeping the branch:

```powershell
git tag archive/short-description feature/short-description
git push origin archive/short-description
```

## 5. Abandon the experiment (do not merge)

```powershell
git checkout main
git branch -D feature/short-description
```

If you pushed the branch, delete it on the remote when you no longer need it:

```powershell
git push origin --delete feature/short-description
```

## Quick reference


| Goal              | Command                                                       |
| ----------------- | ------------------------------------------------------------- |
| Start experiment  | `git checkout -b feature/name`                                |
| Run app on branch | `streamlit run app.py`                                        |
| Test              | `python -m pytest tests/ -q`                                  |
| Fold into main    | `git checkout main` → `git merge feature/name` (or open a PR) |
| Discard branch    | `git checkout main` → `git branch -D feature/name`            |


## Notes for this repo

- Do not commit `.streamlit/secrets.toml` or other secrets.
- If the experiment later touches Neon or Auth0, use a separate database branch or test tenant before merging.
- Project convention: avoid `st.rerun()` where possible; keep chat inputs at the bottom of the UI.

