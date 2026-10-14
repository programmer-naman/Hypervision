# Team split and GitHub workflow

One repository, three people, no overlapping files — so nobody ever has a
merge conflict. Every commit is made by the person who owns that file.

## Who owns what

| | Person 1 — **Database engine** | Person 2 — **Monitoring & analysis** | Person 3 — **Experiments, terminal UI, docs** |
|---|---|---|---|
| Folders / files | `backend/mini_dbms/` (storage, parser, **operators**, **optimizer**, **settings**, **executor**, transactions) · `backend/data/sample_data_generator.py` · `tests/test_engine.py` | `backend/monitor/` (resource monitor, VMI adapter) · `backend/analysis/` (correlator, classifier, **recommender**, **comparator**) · `backend/vm/` (**VM controller**) · `tests/test_analysis.py`, `tests/test_vm.py` | `backend/experiments/experiment_manager.py` · `backend/cli/` (console, render, ui) · `run.py` · `tests/test_experiments.py` · `README.md`, `TEAM_SPLIT.md`, `scripts/` |
| Main job for the new feature | The optimisations the recommendations switch on: pushdown, compiled predicates, streaming aggregation, smaller-side join, and the `ExecutionSettings` object | Deciding *which* actions to suggest for each bottleneck (CPU / RAM / latency), ranking them, the before/after maths, and the VM controller that really resizes vCPUs / memory | Applying a recommendation, rerunning, A/B comparison with medians + memory probe, the `apply` / `undo` / `compare` / `set` / `vm` commands |
| Can explain in the viva | How a SQL query is parsed, optimised and executed; why each optimisation returns the same result; deadlock detection | How CPU/RAM are sampled, how a bottleneck is classified, how recommendations are chosen and ranked, how `xl` / `virsh` commands resize a VM | How one experiment runs end to end, how fair comparisons are made (median of repeated runs, CPU time, heap peak) |

Interfaces between the three parts (agree on these and you can work in parallel):

* `ExecutionSettings` (Person 1) — read by the recommender (Person 2) and the experiment manager (Person 3).
* `recommend_actions(classification, context)` and `build_context(...)` (Person 2) — called by the experiment manager (Person 3).
* `VMController` (Person 2) — created and called by the console (Person 3); its `snapshot()` feeds the recommender.
* `comparator.compare / summarize_runs` (Person 2) — called by the experiment manager and rendered by the CLI (Person 3).

## One-time setup (Person 1 only)

1. On GitHub: **New repository** → name `hypervision` → leave it empty (no README) → Create.
2. *Settings → Collaborators* → add Person 2 and Person 3.
3. Tell Person 2 and 3 to unzip the project folder; everyone keeps an unzipped copy
   (below it is called `~/hypervision-src`).

## Every person: configure git once

```bash
git config --global user.name  "Your Name"
git config --global user.email "the-email-on-your-github-account@example.com"
```

## Step 1 — Person 1 pushes the skeleton to `main`

```bash
git clone https://github.com/<owner>/hypervision.git hypervision-repo
cd hypervision-repo
bash ~/hypervision-src/scripts/commit_part.sh 0 ~/hypervision-src
git branch -M main
git push -u origin main
```

## Step 2 — each person makes their own commits on their own branch

Person 2 and 3 first clone (`git clone https://github.com/<owner>/hypervision.git hypervision-repo`
and `cd hypervision-repo`). Then:

| Person | Commands |
|---|---|
| **1** | `git checkout -b feature/engine` → `bash ~/hypervision-src/scripts/commit_part.sh 1 ~/hypervision-src` → `git push -u origin feature/engine` |
| **2** | `git checkout -b feature/analysis` → `bash ~/hypervision-src/scripts/commit_part.sh 2 ~/hypervision-src` → `git push -u origin feature/analysis` |
| **3** | `git checkout -b feature/experiments-cli` → `bash ~/hypervision-src/scripts/commit_part.sh 3 ~/hypervision-src` → `git push -u origin feature/experiments-cli` |

The script copies *your* files from the full project into your clone and makes
one commit per logical piece, with your name on each. (On Windows, run it from
**Git Bash**, which comes with Git for Windows.)

Prefer to commit by hand? The same steps are in `scripts/commit_part.sh`
— each `commit_step "message" file ...` line is one `git add` + `git commit`.

## Step 3 — pull requests and merging

1. On GitHub open a **Pull request** for each branch into `main`.
2. Another team member reviews it (read the code, ask one question, approve).
3. Merge with **"Create a merge commit"** (keeps every individual commit visible in the history).
   Merge order doesn't matter because no file is shared; the app is complete once all three are in.

## Step 4 — check the finished project

```bash
git checkout main && git pull
pip install -r requirements.txt
python -m unittest discover -s tests      # 72 tests should pass
python run.py
```

## Commits each person ends up with

**Person 1** — `feat(engine)` storage + parser · operators · optimizer · settings · executor · lock manager · `feat(data)` generator · `test(engine)`

**Person 2** — `feat(monitor)` resource monitor · VMI adapter · `feat(analysis)` correlator · classifier · recommender · comparator · `feat(vm)` VM controller · `test(analysis)` · `test(vm)`

**Person 3** — `feat(experiments)` manager · `feat(cli)` ui + render · console · entry points · `test(experiments)` · `docs`

## Later changes

Keep working in your own area, in a branch named `feature/<what-you-do>`, and open a pull request.
If you need a change in someone else's file, ask them (or open an issue) instead of editing it —
that's what keeps the history conflict-free.
