# Operate

This page covers the day-to-day running of a deployment: starting and stopping the loop, checking status, steering individual runs, and the safety defaults that are on by default. Set the loop up in [Install](install.md) first.

## Starting and stopping the loop

`outerloop start` submits the resident tick on Slurm, or runs the local loop in the foreground where there is no scheduler. Stop the resident with `scancel --name outerloop-resident`, adding the instance's `<12 hex>` suffix when you run two instances, and stop a foreground loop with Ctrl-C. Restarting continues the same work from the saved records. Use tmux or a user service if a foreground loop should survive your terminal.

Only one loop may own a state root. Every loop holds the root's tick lease, and `start` refuses while the lease is held. After a crash, the next start on the same host takes the lease at once. A start from another host takes it once the heartbeat is three of the holder's cadences old, since file locks may not reach across nodes. Ctrl-C and SIGTERM release the lease.

## Checking status

```bash
outerloop status [--root <root>] [--json]
```

This read-only command lists every non-ended run with its target, agent, state and phase, author backend and model, override flag, GPU-hours used and budgeted, session cost, and any endpoint wait. Current endpoint outages and their waiting run ids follow the runs. It reads local files only, with no GitHub, scheduler or health-probe calls, so it is safe to run on a login node. GPU budgets come from each run's workspace contract, including review top-ups, and read `unknown` when that contract is unavailable. An empty or nonexistent root reports no runs and creates nothing.

The root resolves from `--root`, then `OUTERLOOP_ROOT` in the environment, then the selected settings file, then `~/.outerloop`. Outage semantics are in [endpoints](endpoints.md#outages).

## The three switches

Each switch is a file you create in the state root with `touch`, effective from the next tick, and remove to let work flow again.

- `PAUSE` stops tick work except the heartbeat, and jobs already running continue. The resident chain drains on it, cancelling its successor and exiting, so run `outerloop start` again after removing the file. The local loop and the per-tick chain resume on their own.
- `DISARM_WAKE` stops wake delivery by the sweep and by queued wake jobs, while fresh launches, ending records and the chain keep running. The starting environment's `OUTERLOOP_DISPATCH_WAKE=0` does the same, and unlike the file it is never read from `.env`.
- `HOLD_LAUNCHES` stops fresh intake, self-initiated and steward runs until removed, with no chain restart. The sweep, wake and message delivery, GitHub polling, the board and ending records continue. Existing runs keep spending, including their panels, author sessions and the authors' own launch submissions.

## Ending a run

```bash
outerloop end <run-id> [--root <root>] [--note <text>]
```

This writes an end request in the run's directory. Unknown and already-ended runs are refused, and repeating a pending request keeps its original time and note. The run ends as `operator`, with the same cleanup as a PR merge or close, at the first tick when no session holds it. Pending jobs are cancelled. A session in flight finishes its leg, cannot start a publish, and a publish already under way completes before the run ends. A run with an issue waits until GitHub is reachable, so the issue is told.

The slot becomes free, and the next claim uses the current settings, including author overrides. A paused loop must resume to process the request.

## Sending a run a message

```bash
outerloop message <run-id> (<text> | --file <path>) [--root <root>]
```

Use this to tell a run something it cannot see itself, such as a slot limit or a deployment change. The command appends one message to the run's inbox, the same queue a PR comment lands in, so it needs no open PR. The run sees it at its next wake as a note from the operator, trusted like the kernel's own notes and capped like a comment. Unknown and already-ended runs are refused.

No new wake is scheduled. A parked run sees the message at the next tick, a run sleeping on jobs sees it when they finish, and a paused or wake-disarmed loop delivers when it resumes.

## Rebinding a run

```bash
outerloop rebind <run-id> [--root <root>] [--note <text>]
```

Use rebind when a bound model endpoint is retired, or when you want a run to continue with its slot's current author selection. Update the author override or the fleet settings first. The next wake validates and binds that selection, and a session in flight finishes its leg first.

An unusable selection stays pending until three applications fail. The request then stays on disk as failed, with its count and last error, and normal endpoint deferral, deadlines and stuck handling resume. `outerloop status` shows pending and failed requests and their last error. A new rebind replaces a failed request, and `outerloop rebind <run-id> --cancel` removes a pending one.

The workspace, experiments, notebook, inbox and GPU-hour meter survive a rebind. A backend change starts a fresh session, while a model change on the same backend resumes the session. The author's `stop` syscall finishes its leg and may end the run, so use rebind to continue its work under a different author.

## Live operator ceilings

Create `limits.toml` in the state root to cap this fleet while leaving target contracts under their normal review process:

```toml
[defaults]
max_gpus = 8
max_active_attempts = 2

[targets."owner/repo"]
max_gpus = 4
max_active_attempts = 1
```

`defaults.max_gpus` caps aggregate fleet usage across this state root and also bounds each target. Target sections can only tighten the defaults. Attempt widths are the minimum of the operator value and the contract's `max_active_attempts`, whose default is one. Omitted keys impose no ceiling, and zero stops new admissions for that resource.

Limits are re-read at admission, including inside existing runs. Invalid TOML, unknown keys, unreadable files, negative values and non-integers are logged and fail closed for GPU work and fresh attempts. Replace the file atomically.

Each GPU check with a finite ceiling takes one scheduler snapshot of your running and pending jobs. A job belongs to this fleet when its name carries a run id present under `<root>/runs/`, or the hashed run key the kernel substitutes for long ids. Your unrelated jobs do not count, and neither do this fleet's jobs from before the current naming: drain those before relying on a ceiling. Over-ceiling experiments receive an author-visible refusal without launch or GPU-hour charges. Evaluations wait for capacity, and waiting does not exhaust wake retries. Lowering a ceiling never cancels existing jobs.

!!! warning
    These are live admission ceilings, not a scheduler-enforced quota. Two
    simultaneous admissions can see the same usage and together exceed the
    ceiling by at most one batch.

`outerloop limits --root <root>` is read-only and reports the ceilings, current fleet and target GPU usage, malformed files and scheduler query failures.

## Reading the board and reports

Every terminal attempt lands on the target's `research-log` branch. `CLIMB.md` renders the numbers and the attempts table on GitHub, `climb/data/<benchmark>.json` holds one row per attempt, and `index.html` charts that JSON when opened from a clone. Run reports go to the issue or PR thread that asked for the work, success or failure. The messages a run exchanges with the kernel are its inbox; see the [inbox reference](reference/inbox.md).

## Run lifecycle

Runs appear as `running`, `parked` or `ended` on the board, in status and in logs. A run waiting on jobs parks with no process alive, and its wake is queued behind those jobs. Preemption and lost jobs heal on the next tick. An open PR is a link on a parked run. Comments and base moves wake that run through the ordinary wake job, and a run sleeping on jobs receives comments after those jobs finish.

The resident chain checks its successor every tick and before handover, requeues vanished or terminal jobs with the same dependency, and keeps ticking through the walltime margin if recovery fails.

## Safety defaults

On by default. Think hard before changing any of them.

- The bot never merges and is never a code owner, so your branch protection applies to it like any contributor's. `merge: auto` is an explicit per-repo opt-in. In that mode the sweep merges a clean PR only at the head its gate and panel approved, through your required checks, and it never arms GitHub auto-merge.
- Agent sessions run with no GitHub credential in their environment; each holds only its own model key. Pushes happen after the session ends.
- Only maintainer-authored issues and comments become tasks. Everything else, including PR descriptions and diffs, is data, never instructions.
- Launches, sleeps, GPU-hours and runs per week are enforced in code, and so are the session turn and walltime ceilings. Token and dollar spending is not metered, so cap the keys at the provider instead.
- A pause file in the state root stops the loop from anywhere with write access, with no cluster login needed.

## Getting help

Open an issue on GitHub. If you are reporting something an agent did, include its run report, which every run writes. The community channels are listed in [community](community.md).
