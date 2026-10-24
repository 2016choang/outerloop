---
name: setup
description: Set up Outerloop when the user asks to use it on a repository, with local forge and local compute defaults.
---

1. Work in the target repository, never the Outerloop kernel repository. Read its
   guidance and check `git status --short`, `git --version` (2.38+),
   `python --version` (3.12+), and `uv --version`. If prerequisites are missing,
   stop and explain what to install. Follow the
   [install guide](https://github.com/outerloop-science/outerloop/blob/main/docs/install.md).
2. Install the PyPI package in the user's Python environment with
   `pip install outerloop-science`, then check `outerloop --version` and
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

   Let the user supply model choices and secrets through the wizard's private
   prompts in their terminal. Never request secrets in chat, write credentials
   to files, put them in argv, or read key files. If interactive secret entry is
   unavailable, hand this exact command to the user and stop. The CLI owns
   credential storage. Local forge needs no GitHub credential.
5. Check the exit status and the wizard's configuration summary without reading
   secrets. If settings or a forge already exist, stop before overwriting or
   switching them; do not add `--force` or silently reseed. Report the target,
   local compute and local forge selection, and any containment or panel warning.
   Run `outerloop status` to check the selected deployment. Setup is complete;
   do not run `outerloop start` unless the user asks to start a run.
