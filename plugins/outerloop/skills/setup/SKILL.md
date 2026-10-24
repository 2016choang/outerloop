---
name: setup
description: Set up Outerloop when the user asks to use it on a repository, with local forge and a choice of Slurm or local compute.
---

1. Work in the target repository, never the Outerloop kernel repository. Read its
   guidance and check `git status --short`, `git --version` (2.38+),
   `python --version` (3.12+), and `uv --version`. If prerequisites are missing,
   stop and explain what to install. Follow the
   [install guide](https://github.com/outerloop-science/outerloop/blob/main/docs/install.md),
   especially "Deployment shapes" and "Placement and lanes". Check
   `command -v sbatch`; if present, ask whether to use Slurm (recommended for
   GPU benchmarks) or local compute. Without Slurm, use local compute; for GPU
   benchmarks on a cluster, have the user SSH to its login node and run the
   coding agent there. Never run benchmarks or evaluations on a login node.
2. On a login node, ask whether site policy permits package installation before
   installing anything, including prerequisites or author harnesses. If forbidden,
   write a short CPU batch script with `#!/bin/bash`, `set -euo pipefail`, the
   site's environment activation, and the install command below. Use the user's
   shared Python environment and confirmed CPU account/partition, request no GPU,
   submit with `sbatch --account <account> --partition <partition>
   --output /shared/path/install-%j.log /shared/path/install.sh`, then check the
   job's log and successful completion before continuing. Apply this rule to
   kernel environment setup too; use `init --no-install-harness` to prevent
   login-node harness installs and install missing harnesses in a CPU job.
   Install the PyPI package in the user's Python environment with
   `pip install "outerloop-science>=0.5"` (the local forge needs 0.5 or later),
   then check `outerloop --version` and
   `outerloop init --help`. Use an existing virtual environment; if none is
   suitable, ask which environment to use. Do not change project dependencies
   or use sudo to work around an installation error.
3. Use the sibling **contract** skill to prepare and validate `.outerloop.yaml`.
   Before seeding, check `git diff HEAD -- .outerloop.yaml` and
   `git ls-files --error-unmatch .outerloop.yaml`. Seeding copies commits, not
   working-tree changes: if the contract or required benchmark changes are
   uncommitted, stop and ask the user to commit them. Do not commit for them.
4. Choose the target label in `owner/repo` form (a local label needs no GitHub
   account). Replace the placeholders below with that label and the absolute
   path from `git rev-parse --show-toplevel`, quoting the path:

   ```bash
   outerloop init --forge local --compute local --target owner/repo \
     --forge-source /path/to/repo
   ```

   For Slurm, keep `--forge local` for filesystem-backed hosting, or use
   `--forge github` for a GitHub target (omit `--forge-source` and follow the
   install guide's identity setup). Ask for a shared state directory and the
   CPU account and partition; do not guess site defaults. Replace `--compute local`
   above with `--compute slurm --root /shared/path/outerloop --account my-account
   --partition <cpu-partition>`, substituting the user's answers. These flags set
   `OUTERLOOP_ROOT`, `OUTERLOOP_ACCOUNT` and `OUTERLOOP_PARTITION`; the root and
   local forge must be accessible from compute nodes.
   Ask separately for the GPU lane: init does not collect it. Set the agreed
   `OUTERLOOP_GPU_PARTITION` (example: `gpu-large`) and optional
   `OUTERLOOP_GPU_ACCOUNT` in `~/.config/outerloop/.env`; an omitted GPU account
   uses the CPU account. For target-specific placement, use `OUTERLOOP_GPU_LANES`
   per "Placement and lanes". Keep secrets private when updating settings.
   The resident loop also needs a kernel source checkout accessible to compute
   nodes: set `OUTERLOOP_HOME` to it, while keeping the target repo separate.
   Follow the guide's Apptainer image setup before starting Slurm work.

   Let the user supply model choices and secrets through the wizard's private
   prompts in their terminal. Never request secrets in chat, write credentials
   to files, put them in argv, or read key files. If interactive secret entry is
   unavailable, hand this exact command to the user and stop. The CLI owns
   credential storage. Local forge needs no GitHub credential.
5. Check the exit status and the wizard's configuration summary without reading
   secrets. If settings or a forge already exist, stop before overwriting or
   switching them; do not add `--force` or silently reseed. Report the target,
   selected compute and forge, state root, CPU/GPU placement, and any containment
   or panel warning.
   Run `outerloop status` to check the selected deployment. Setup is complete;
   do not run `outerloop start` unless the user asks to start a run.
