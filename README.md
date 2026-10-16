<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/outerloop-science/outerloop/main/docs/assets/icon-dark.svg">
  <img alt="Outerloop" src="https://raw.githubusercontent.com/outerloop-science/outerloop/main/docs/assets/icon-light.svg" width="132">
</picture>

# Outerloop

[![ci](https://github.com/outerloop-science/outerloop/actions/workflows/ci.yml/badge.svg)](https://github.com/outerloop-science/outerloop/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/outerloop-science.svg)](https://pypi.org/project/outerloop-science/)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://pypi.org/project/outerloop-science/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Autoresearch agents that improve your benchmark.**

Outerloop runs AI agents on your own research code. An agent proposes a
change, runs the experiment on your cluster, and opens a pull request only
when your benchmark actually improves. Every attempt ends in a short
written report, including the attempts that failed.

You run it yourself, with your own keys, your own compute and your own
repositories, and nothing reports back to us. It is built and used every
day by the [Agentic Learning AI Lab](https://agenticlearning.ai) at NYU,
where it co-develops our research codebases.

## Get started

Three commands and one file. You need a repo with a benchmark command, an
API key for the model that will write the code, and a Slurm cluster or one
machine with a GPU.

```bash
pip install outerloop-science
outerloop init     # where the loop runs, which repo, which model and its key, your GitHub identity
```

The wizard asks for a GitHub identity for the agents to open pull requests
as. Pick `app` and it walks you through creating a GitHub App under your
account and installing it on the repo. Pick `pat` if you already have a
token, and it writes the config and the key files either way. Then add one
file, `.outerloop.yaml`, to the repo you want improved:

```yaml
benchmarks:
  - name: my-benchmark
    command: uv run python -m mypkg.eval --json   # prints {"success_rate": 0.42}
    metric: success_rate
    direction: max
budgets:
  gpu_hours_per_run: 8
  runs_per_week: 10
scope:
  allowed: [src/mypkg/solver/]   # the only paths an agent may change
roadmap: docs/roadmap.md # what the agents read for direction; never written
```

```bash
outerloop start    # on a Slurm login node this submits the loop; without Slurm it runs in the foreground
```

The [install guide](https://docs.outerloop.science/install/) walks through
every step, including the other model backends. The
[contract guide](https://docs.outerloop.science/contract/) explains the
decisions the contract asks you to make.

## The loop

1. **Propose.** An agent picks a hypothesis and writes the code change.
2. **Experiment.** It runs the training on your cluster and reads the results.
3. **Measure.** Outerloop scores the change against the base tree at the same
   seed. Noise does not count as an improvement.
4. **Review.** Judges read the change and the claim. If both hold up, a pull
   request opens.
5. **Record.** Every attempt gets a short report: hypothesis, change,
   outcome, next step. Negative results included.

Agents write only the paths the contract allows, and the contract, your
roadmap, and `.github/` are never writable by an agent. Keep the
benchmark's own eval code outside those allowed paths, so the agent cannot
edit the ruler it is judged by. Your branch protection and required checks
apply to them as to any contributor. By default a pull request waits for a
human, and a repo can also let clean ones merge themselves. The full
picture is in
[how it works](https://docs.outerloop.science/how-it-works/), and the
[operate guide](https://docs.outerloop.science/operate/) covers running
and steering runs.

## Pull request reviews only

The reviewer works on its own, with one workflow file and an API key, about
five minutes of setup, and no bot account or cluster. It comments on pull
requests with concrete findings and never approves, blocks, or fails your
build. See [the reviewer guide](https://docs.outerloop.science/reviewer/).

## Safety by design

- **Opt-in and contract-bound.** A repo takes part by granting the bot
  access and committing a contract. The contract, your roadmap, and
  `.github/` are never writable by an agent.
- **Nothing on trust.** In the default gate mode, Outerloop measures every
  claim itself, on committed trees, and re-verifies before a pull request
  exists. A repo can instead opt into labelled self-reported numbers.
- **Untrusted input.** Pull request text, diffs, issues, web pages, and job
  output are data, never instructions. Agents hold no GitHub credential,
  only their own model key.
- **Budgets in code.** Launches, GPU-hours, and runs per week are enforced
  by the kernel, not left to the agent.
- **No model lock-in.** Claude Code, Codex, and hermes-agent are wired
  today, including [self-hosted endpoints](https://docs.outerloop.science/endpoints/).

Further reading: the [architecture page](https://github.com/outerloop-science/outerloop/blob/main/docs/design/architecture.md) for the full design, the [roadmap](https://github.com/outerloop-science/outerloop/blob/main/docs/roadmap.md) for what is planned, and the [community page](https://docs.outerloop.science/community/) for where to ask questions.

## Developing

```bash
uv sync
uv run pre-commit install
uv run pytest
```

| Path | Purpose |
| --- | --- |
| `src/outerloop/` | The kernel: contract, tick (the Slurm chain), attempt/orchestrator (the climb), measure/dispatch (evals as jobs), syscall (the author's tool), panel/verifier/review, github, harness backends |
| `tests/` | Tiers: unit (default), `slow`, `llm`, `slurm` markers |
| `scripts/` | Committed operational scripts (the tick chain, provisioning) |
| `docs/` | The manual (deployed at [docs.outerloop.science](https://docs.outerloop.science/)), architecture and design notes, roadmap |

## License

Apache License 2.0 — see [LICENSE](https://github.com/outerloop-science/outerloop/blob/main/LICENSE) and [NOTICE](https://github.com/outerloop-science/outerloop/blob/main/NOTICE).
