---
name: contract
description: Write or repair an Outerloop benchmark contract, identify solver scope, and validate .outerloop.yaml until it passes.
---

1. Work at the target repository root and read its guidance. Never target the
   Outerloop kernel itself. Inspect benchmark scripts, tests and package commands
   to find a deterministic, repeatable command that prints a numeric metric as
   JSON on one line. Ask the user if the benchmark, metric or direction is
   ambiguous; do not invent an evaluator or run an expensive benchmark without
   an agreed compute budget.
2. Read [docs/contract.md](https://github.com/outerloop-science/outerloop/blob/main/docs/contract.md)
   for fields and its linked reference for constraints. Inspect an existing
   `.outerloop.yaml` before proposing changes. Show the benchmark command, metric,
   `min` or `max` direction, budget caps and narrow solver-only `scope.allowed`.
   Trace evaluator code, fixtures and scoring dependencies; keep them outside
   every allowed path. If solver and evaluator cannot be separated safely, stop
   and ask the user to choose a narrower scope or reorganize them.
3. Write the agreed `.outerloop.yaml` in the target repository. Preserve existing
   budgets unless the user requests a change; ask for missing budget choices.
   Never allow `.github/`, the contract or `docs/roadmap.md` in solver scope.
   Contract authoring is an operator setup action, not permission for the solver
   to edit it. If repository guidance forbids this edit, present the proposal
   and stop for the user to apply it. Do not enable automatic merging.
4. In the Python environment containing `outerloop-science`, run:

   ```bash
   python -m outerloop.contract_cli .outerloop.yaml
   ```

   Read each validation error, fix the relevant field, and run the same command
   again until it exits zero. If a fix needs a scientific choice, broader write
   access or more budget, stop and ask instead of guessing. A missing module is
   an installation problem: return to **setup** rather than altering the schema.
5. Report successful validation, metric direction, budget and allowed paths.
   Validation checks the contract, not benchmark correctness: disclose whether
   the benchmark was actually run and its JSON checked. Leave commit, push and
   starting the loop to an explicit user request. If a local forge already exists,
   explain that editing this checkout does not update its target branch; ask how
   the user wants to synchronize it before operating on the new contract.
