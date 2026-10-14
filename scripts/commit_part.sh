#!/usr/bin/env bash
# commit_part.sh -- makes YOUR share of the HyperVision commits, one logical
# commit at a time, using YOUR git identity (git config user.name / user.email).
#
# Run it from inside your clone of the shared GitHub repository:
#
#     bash /path/to/hypervision/scripts/commit_part.sh <0|1|2|3> /path/to/hypervision
#
#   0  repository skeleton (Person 1 runs this once, on main)
#   1  Person 1 - database engine
#   2  Person 2 - monitoring and analysis
#   3  Person 3 - experiments, terminal UI, tests and docs
#
# The second argument is the folder that holds the complete project (the
# unzipped copy). Files are copied from there into your clone, then committed.
# See TEAM_SPLIT.md for the full workflow.

set -euo pipefail

PERSON="${1:-}"
SRC="${2:-}"

if [[ ! "$PERSON" =~ ^[0-3]$ || -z "$SRC" ]]; then
  echo "Usage: bash commit_part.sh <0|1|2|3> <path-to-complete-project>" >&2
  exit 1
fi
if [[ ! -d "$SRC/backend" ]]; then
  echo "Error: '$SRC' does not look like the HyperVision project folder." >&2
  exit 1
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: run this from inside your clone of the repository." >&2
  exit 1
fi
if [[ -z "$(git config user.name || true)" || -z "$(git config user.email || true)" ]]; then
  echo "Error: set your identity first:" >&2
  echo "  git config user.name  \"Your Name\"" >&2
  echo "  git config user.email \"you@example.com\"" >&2
  exit 1
fi

SRC="$(cd "$SRC" && pwd)"
HERE="$(pwd)"

# commit_step "<message>" file1 file2 ...
commit_step() {
  local message="$1"; shift
  local f
  for f in "$@"; do
    if [[ ! -f "$SRC/$f" ]]; then
      echo "Error: missing file $SRC/$f" >&2
      exit 1
    fi
    if [[ "$SRC" != "$HERE" ]]; then
      mkdir -p "$(dirname "$f")"
      cp "$SRC/$f" "$f"
    fi
  done
  git add -- "$@"
  if git diff --cached --quiet; then
    echo "  (skipped, nothing new)  $message"
  else
    git commit -q -m "$message"
    echo "  committed: $message"
  fi
}

case "$PERSON" in
0)
  echo "Skeleton commit (identity: $(git config user.name))"
  commit_step "chore: add project skeleton (gitignore, requirements, package markers, test bootstrap)" \
    .gitignore requirements.txt sample_data/.gitkeep logs/.gitkeep tests/_bootstrap.py \
    backend/__init__.py backend/mini_dbms/__init__.py backend/data/__init__.py \
    backend/monitor/__init__.py backend/analysis/__init__.py backend/experiments/__init__.py \
    backend/vm/__init__.py
  ;;
1)
  echo "Person 1 - database engine (identity: $(git config user.name))"
  commit_step "feat(engine): add CSV storage and SQL parser" \
    backend/mini_dbms/storage.py backend/mini_dbms/parser.py
  commit_step "feat(engine): add relational operators with streaming aggregation and join build-side option" \
    backend/mini_dbms/operators.py
  commit_step "feat(engine): add optimizer with predicate pushdown and plan builder" \
    backend/mini_dbms/optimizer.py
  commit_step "feat(engine): add ExecutionSettings tuning knobs" \
    backend/mini_dbms/settings.py
  commit_step "feat(engine): add executor with threaded filter and optimisation switches" \
    backend/mini_dbms/executor.py
  commit_step "feat(engine): add lock manager with deadlock detection" \
    backend/mini_dbms/transactions.py
  commit_step "feat(data): add sample data generator" \
    backend/data/sample_data_generator.py
  commit_step "test(engine): verify every optimisation combination returns identical results" \
    tests/test_engine.py
  ;;
2)
  echo "Person 2 - monitoring and analysis (identity: $(git config user.name))"
  commit_step "feat(monitor): add resource monitor" \
    backend/monitor/resource_monitor.py
  commit_step "feat(monitor): add simulated VMI adapter" \
    backend/monitor/vmi_adapter.py
  commit_step "feat(analysis): add event correlator" \
    backend/analysis/correlator.py
  commit_step "feat(analysis): add rule-based bottleneck classifier" \
    backend/analysis/classifier.py
  commit_step "feat(analysis): add action-based recommender for CPU, RAM and latency" \
    backend/analysis/recommender.py
  commit_step "feat(analysis): add before/after comparator" \
    backend/analysis/comparator.py
  commit_step "feat(vm): add VM controller to resize vCPUs and memory (Xen, KVM, dry-run, ssh)" \
    backend/vm/controller.py
  commit_step "test(analysis): cover recommender rules, ranking and comparator maths" \
    tests/test_analysis.py
  commit_step "test(vm): cover VM commands, ssh wrapping, dry-run, failures and VM recommendations" \
    tests/test_vm.py
  ;;
3)
  echo "Person 3 - experiments, terminal UI, tests, docs (identity: $(git config user.name))"
  commit_step "feat(experiments): add experiment manager with settings, A/B comparison and result fingerprint" \
    backend/experiments/experiment_manager.py
  commit_step "feat(cli): add terminal UI helpers and result renderer" \
    backend/cli/ui.py backend/cli/render.py
  commit_step "feat(cli): add interactive console with apply, compare, undo and settings commands" \
    backend/cli/console.py
  commit_step "feat(cli): add command-line entry points" \
    backend/cli/__init__.py run.py
  commit_step "test(experiments): cover experiment manager and console commands" \
    tests/test_experiments.py
  commit_step "docs: add README, team split guide and commit helper script" \
    README.md TEAM_SPLIT.md scripts/commit_part.sh
  ;;
esac

echo
git log --oneline -n 12
