# Contract fields

Every field of `.outerloop.yaml`, the file a target repo's owner writes to
opt in to a climb: its benchmarks, budgets and the paths the agents may
write. Each entry below states the type, the default and the constraint the
loader enforces. For how to choose the values, see
[the contract guide](../contract.md).

The contract is untrusted input, so the loader rejects anything ambiguous.
A file over 64 KiB, a YAML alias, a duplicate key or an unknown field
fails validation rather than being partially read. A field the code does not
know is a typo, never a silent no-op. Validate a contract before pushing it:

```bash
uv run python -m outerloop.contract_cli .outerloop.yaml
```

## Field summary

| Field | Meaning | Default |
| --- | --- | --- |
| `benchmarks` | what the agents try to improve | required |
| `budgets` | the compute an attempt may spend | required |
| `scope` | the paths the agents may write | required |
| `roadmap` | the target's roadmap, read as context | required |
| `channels` | kernel-mediated sharing between agents | all on |
| `suite` | one aggregate target across benchmarks | none |
| `steward` | a separate maintenance lane's paths | none |
| `merge` | manual or automatic PR merging | `manual` |
| `guidance` | the research-taste text pack | `default` |

Paths in `scope`, `steward` and `guidance` are repo-relative POSIX paths.
Globs, absolute paths, `..` components and the git directory are refused.
Outerloop is never a valid target of itself, whatever the deployment names.

Some write paths are forbidden regardless of the contract: the contract
file itself, `.github/`, the git directory, the roadmap, the guidance
directory and the record ledger (`BENCHMARKS.md`, `results/leader.json`).
An allowed path that overlaps one of these fails validation.

## Top-level fields

### `benchmarks`

The list of metrics the agents try to improve, one entry per benchmark.
At least one entry. Benchmark names must be unique; they key branch names,
ledger rows and job names.

- Type: list of benchmark blocks
- Constraint: required, at least 1 entry, unique `name` values

### `budgets`

The compute allowance for one attempt. See
[the budgets fields](#budgets-fields).

- Type: object
- Constraint: required

### `scope`

The paths the author agents may write. See [the scope fields](#scope-fields).

- Type: object
- Constraint: required

### `roadmap`

The repo-relative path to the target's roadmap. The agents read it as
context and may never write it.

- Type: string
- Constraint: required, a repo-relative path

### `channels`

The kernel-mediated context sharing between agents. See
[the channels fields](#channels-fields).

- Type: object
- Default: every channel enabled

### `suite`

An aggregate target across every benchmark, so a change is evaluated on all
of them and reported per benchmark plus the aggregate, with no
cherry-picking. See [the suite fields](#suite-fields).

- Type: object
- Default: none (no aggregate)

### `steward`

A separate stewardship lane that may maintain the benchmarks themselves:
eval harnesses, tests and reference data. The steward never touches the
solver's paths, the record ledger or any forbidden path. See
[the steward fields](#steward-fields).

- Type: object
- Default: none (no steward lane)

### `merge`

How a clean PR is merged. `manual` arms auto-merge only when a required
human review stands between arming and merging. `auto` merges a clean PR at
the blessed head itself; the owner sets branch protection with strict
up-to-date checks and no required review. The gate, the suite phase and the
panel's validity findings still bind before publish in both modes.

- Type: `manual` or `auto`
- Default: `manual`

### `guidance`

Which research-taste text pack the author brief and the panel claims carry,
as advisory text. `default` ships the kernel's pack, `none` ships nothing,
and any other value is a repo-relative directory holding `author.md` and
`panel.md`. Each file is capped at 8,000 characters; a missing file is empty
guidance, never a contract error. The directory must not be `.outerloop/`
(the workspace syscall channel) or the agents' line memory. The recommended
location is `.github/outerloop/guidance/`, which agents can never write.

- Type: string
- Default: `default`

## Benchmark fields

### `name`

The benchmark's identity. It reaches branch names, ledger keys and job
names, so it is a slug, not free text. Duplicate names fail validation.

- Type: string
- Constraint: 1-64 characters of `A-Z`, `a-z`, `0-9`, `_`, `.` or `-`

### `command`

The command that measures a checkout. It runs in the repository root and
prints one JSON object containing the metric.

- Type: string
- Constraint: required, non-empty

### `metric`

The key of the measured number in the command's JSON output.

- Type: string
- Constraint: required, non-empty

### `direction`

Which way is better. `max` means a higher metric is an improvement; `min`
means a lower one is.

- Type: `min` or `max`
- Constraint: required

### `min_delta`

The absolute cross-seed noise floor, in metric units. A comparison against
the recorded best was measured under a different seed, so a delta inside
the floor is noise, not progress. A delta equal to the floor clears it.
Right for a bounded metric such as a
success rate. Set it from a seed-variance calibration. Credit also requires
a relative improvement of at least 0.5%, which the kernel applies absolutely
when the baseline is zero.

- Type: number
- Constraint: at least 0
- Default: none (no absolute floor)

### `min_delta_rel`

The relative noise floor, as a fraction of the recorded level. Right for an
unbounded metric such as wall-clock time, whose level drifts with hardware.
Set both floors and the more conservative one applies. The same 0.5%
relative gate applies on top of it.

- Type: number
- Constraint: between 0 and 1.0
- Default: none (no relative floor)

### `seed_env`

The environment variable the eval reads its run seed from, for benchmarks
that resample. The kernel draws one fresh seed per measurement pass and pins
both sides of a comparison to it (paired, common random numbers), then
records it in the ledger row. The eval must print the seed to stdout only
and never persist it into the tree.

- Type: string
- Constraint: an environment variable name, uppercase letters, digits and
  underscores, starting with a letter. Must not name or prefix the
  evaluator's own managed variables
- Default: none (no reseeding)

### `display_digits`

Significant digits shown on human surfaces: PR tables, `BENCHMARKS.md` and
replies. Comparisons always use full precision, so this never hides an
improvement. Full precision lives in `results/leader.json`.

- Type: integer
- Constraint: between 2 and 12
- Default: none (the usual rendering)

### `eval_minutes`

The expected eval duration, in minutes. Above the in-job threshold of 5
minutes, this benchmark's evals run as their own dispatched cluster jobs
rather than inside the attempt job. The value is clamped to 1440 and
governs only the first eval once measured durations exist.

- Type: integer
- Constraint: at least 1
- Default: none (in-job evals)

### `gpus`

The GPUs every dispatched job of this benchmark asks for, gate evals and
author launches alike. A GPU benchmark needs its evals to dispatch (see
`eval_minutes`) and the deployment to name a GPU lane on lane-based compute;
without one the tick
refuses to launch attempts on it. Local compute needs no lane, and a
target entry in `OUTERLOOP_GPU_LANES` stands in for the fleet default.
Multi-node evals are not supported, so
the value is bounded at one node's worth.

- Type: integer
- Constraint: between 0 and 8
- Default: 0 (CPU)

### `baseline`

How the gate obtains the baseline number it compares a candidate to.
`paired` re-measures the base tree next to every candidate, both under one
fresh seed, at two evals per attempt. `cached` measures the base tree once
per base commit into a cache shared by every attempt on that base, then runs
only the candidate at a fresh seed; the comparison is unpaired, so a
positive `min_delta` or `min_delta_rel` is required. The ledger row records
which baseline measurement a credited delta was taken against.

- Type: `paired` or `cached`
- Default: `paired`

### `depth_k`

How many external experiment jobs the author may launch within one
attempt. A meter on actions, not a loop the kernel drives. `0` turns the
launch syscall off for this benchmark.

- Type: integer
- Constraint: between 0 and 16
- Default: 10

### `sleep_k`

How many dispatch-sleep-wake cycles the author may spend. Independent of
`depth_k`: launches meter external compute, sleeps meter wake cycles.
Many launches under one sleep burn one sleep.

- Type: integer
- Constraint: between 1 and 32
- Default: 20

### `lines`

Research lines: each agent slot works on its own persistent
`agents/<agent-id>` branch, checked out at run start with the base merged
in. Off, the run works on the fork's main line only.

- Type: boolean
- Default: `false`

### `verification`

How a submit on this benchmark is verified. `gate` keeps kernel
measurement, suite checks and the panel. `self_report` accepts the author's
claimed candidate and baseline values instead: submit then requires
`--claimed-value` and `--claimed-baseline`, scope and improvement floors
still apply, and no gate evals or panel run. For what this mode does and
does not establish, see the contract guide.

- Type: `gate` or `self_report`
- Default: `gate`

### `regression`

The sibling regression allowance for this benchmark: how far it may worsen
when shared code changes and the climbed benchmark improves. Omission
keeps the default gate, which refuses a regression beyond the sibling's own
significance floors. See [the regression fields](#regression-fields).

- Type: object
- Default: none (floors only)

## Regression fields

One unit family per block: the relative fields or the absolute fields, never
both. All values are finite and nonnegative.

### `free_rel`

The fraction of the sibling's absolute baseline always allowed. Defaults to
this benchmark's `min_delta_rel`, or zero when that is unset.

- Type: number
- Constraint: at least 0

### `max_rel`

The optional exclusive hard cap, as a fraction of the sibling baseline. A
regression at or above the cap is refused regardless of gain.

- Type: number
- Constraint: at least 0, at least `free_rel`

### `requires_gain_rel`

The minimum relative gain on the climbed benchmark that unlocks the
interval between `free_rel` and `max_rel`. Requires `max_rel`.

- Type: number
- Constraint: at least 0

### `free`

The absolute allowance always allowed, in sibling metric units. Defaults to
this benchmark's `min_delta`, or zero when that is unset.

- Type: number
- Constraint: at least 0

### `max`

The optional exclusive hard cap, in sibling metric units.

- Type: number
- Constraint: at least 0, at least `free`

### `requires_gain`

The minimum absolute gain on the climbed benchmark, in climbed metric
units, that unlocks the interval between `free` and `max`. Requires `max`.

- Type: number
- Constraint: at least 0

## Budgets fields

### `gpu_hours_per_run`

The attempt's GPU compute allowance. Every author launch (minutes times
GPUs) and a submit's paired gate evals draw on it, and an over-budget
request is refused with the numbers. Zero for CPU benchmarks; nothing is
metered.

- Type: number
- Constraint: at least 0
- Constraint: required

### `runs_per_week`

The maximum number of attempts started per week on this target. The weekly
spend guard.

- Type: integer
- Constraint: at least 1
- Constraint: required

### `review_topup`

Extra budget added to an attempt whose PR is open, when the PR opens. See
[the review top-up fields](#review-top-up-fields).

- Type: object
- Default: 2 launches, 4 sleeps, 0.5 GPU-hours

### `session_max_turns`

A wish for the author session's turn limit. The orchestrator clamps it into
its own bounds, so a target can ask for shorter sessions, never longer
ones.

- Type: integer
- Constraint: at least 1
- Default: none (the orchestrator's limit)

### `session_minutes`

A wish for the author session's walltime, clamped like `session_max_turns`.

- Type: integer
- Constraint: at least 1
- Default: none (the orchestrator's limit)

### `attempt_job_minutes`

A wish for the attempt job's walltime, clamped like `session_max_turns`.

- Type: integer
- Constraint: at least 1
- Default: none (the orchestrator's limit)

### `followup_job_minutes`

A wish for the follow-up job's walltime, clamped like `session_max_turns`.

- Type: integer
- Constraint: at least 1
- Default: none (the orchestrator's limit)

### `attempt_cooldown_minutes`

Minutes the target waits after an attempt before starting the next one.
Zero runs attempts back to back, with `runs_per_week` as the only spend
guard.

- Type: integer
- Constraint: at least 0
- Default: none (the orchestrator's cooldown, 6 hours)

### `max_active_attempts`

How many attempts run concurrently on this target. Each slot gets its own
agent identity (`agent-01` ... `agent-NN`), so branches, ledger rows and
reports stay distinct.

- Type: integer
- Constraint: at least 1
- Default: none (serial, one attempt at a time)

### `max_concurrent_gpus`

The pace ceiling for one author's sweeps, in GPUs. A sweep of N tasks runs
at most this many GPUs' worth of tasks at once. Lenient by design: agents
rarely launch at the same moment, and an idle share is wasted compute.

- Type: integer
- Constraint: at least 1
- Default: none (the author's own pace)

## Review top-up fields

### `launches`

Extra launches added to an attempt with an open PR.

- Type: integer
- Constraint: between 0 and 16
- Default: 2

### `sleeps`

Extra sleep cycles added to an attempt with an open PR.

- Type: integer
- Constraint: between 1 and 32
- Default: 4

### `gpu_hours`

Extra GPU-hours added to an attempt with an open PR.

- Type: number
- Constraint: at least 0
- Default: 0.5

## Scope fields

### `allowed`

The only paths the author agents may write. The repository root is never
permitted.

- Type: list of repo-relative paths
- Constraint: required, at least 1 entry, no globs

### `shared`

Paths whose changes suite-gate every sibling benchmark, not just the
benchmark being climbed: code every benchmark exercises, as opposed to
benchmark-specific solver code. Each entry must overlap an `allowed` entry,
or the suite gate could never trigger on it.

- Type: list of repo-relative paths
- Default: empty

## Steward fields

### `allowed`

The only paths the steward lane may write: eval harnesses, tests and
reference data. The steward's paths must not overlap the solver's
`scope.allowed`, the record ledger or any forbidden path.

- Type: list of repo-relative paths
- Constraint: required, at least 1 entry

## Channels fields

These switches control kernel-mediated sharing, not repository
authorization. Disabling one cannot stop an agent with its own credentials
from fetching remote refs or using outside communication.

### `siblings`

Whether the author sees what the other agents of the fleet are working on,
through the `siblings` syscall and the queue view.

- Type: boolean
- Default: `true`

### `messages`

Whether agents message each other. Operator thread posts and an agent's
own notes to itself remain available when off.

- Type: boolean
- Default: `true`

### `shared_reports`

Whether past attempts' research reports are fetched and offered to the
author through the `reports` syscall.

- Type: boolean
- Default: `true`

### `branches`

Whether other agents' `agents/agent-NN` branches are fetched and suggested.
The author's own branch keeps working when off.

- Type: boolean
- Default: `true`

## Suite fields

### `metric`

The name of the aggregate metric the suite target reports.

- Type: string
- Constraint: required, non-empty

### `direction`

Which way is better for the aggregate.

- Type: `min` or `max`
- Constraint: required
