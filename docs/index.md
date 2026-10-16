# Outerloop

Outerloop runs AI research agents on your own benchmark repositories. An agent
proposes a change, runs its experiments on your compute, and opens a pull
request when the kernel's own measurement shows the benchmark improved. Every
attempt ends in a written report, including the ones that failed. You run
Outerloop yourself, with your own keys, compute and repositories.

- [Getting started](getting-started.md) takes one repository through a first
  run on a single machine.
- [Guides](install.md) cover installing Outerloop, operating runs, writing a
  contract, connecting model endpoints and adding the PR reviewer.
- [Concepts](how-it-works.md) explain the trust boundary, the credit path,
  where state is stored and how concurrent writers stay safe.
- [Reference](reference/contract.md) lists every contract field, operator
  command, agent syscall, inbox message and environment variable.
- [Project](roadmap.md) holds the roadmap, the changelog, and the guides for
  contributors, security reports and releases.
