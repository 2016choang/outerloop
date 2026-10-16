# Syscalls

The syscall tool is the one command-line surface an agent uses to talk to the
kernel. It is a single standalone Python file the kernel installs at
`.outerloop/syscall` in the workspace, so any harness can call it. This page
lists every verb, who may call it, its arguments and what it refuses. [How
Outerloop works](how-it-works.md) explains the design behind the surface.

A verb is invoked as `python .outerloop/syscall <verb>` from any directory. A
bad invocation prints the reason to stderr and exits with code 2, staging
nothing. Most verbs stage a request that the kernel acts on after the session
ends. The committing verbs are noted below.

| Verb | Who | What it does |
| --- | --- | --- |
| [`launch`](#launch) | author | stage a job to run outside the sandbox |
| [`sleep`](#sleep) | author | commit the staged launches and end the session |
| [`message`](#message) | author | send to the PR thread, yourself or a sibling |
| [`submit`](#submit) | author | stage a submit for measurement and review |
| [`stop`](#stop) | author | end the run, or withdraw its open PR |
| [`finding`](#finding) | judge | record one finding |
| [`conclude`](#conclude) | judge | commit the verdict and end the session |
| [`status`](#status) | any | show staged syscalls and remaining budget |
| [`cancel`](#cancel) | any | discard the staged request |
| [`siblings`](#siblings) | any | what the other agents are working on |
| [`queue`](#queue) | any | the kernel's jobs in the cluster queue |
| [`history`](#history) | any | this run's launches and how each ended |
| [`reports`](#reports) | any | past attempts' research reports |
| [`sync`](#sync) | any | refresh origin refs now, inside this session |

Which verbs a role may call is set by its role specification, and the brief
tells the role which verbs it has. The tool's checks are fast in-session feedback
only. The kernel re-validates every field after the session ends, so a role
that writes its request file by hand is still fully checked.

## Author verbs

### launch

Stages one job to run outside the sandbox, on a frozen snapshot of the
workspace. Multiple launches accumulate until `sleep` commits them.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--name` | required | 1-32 chars of `a-z`, `0-9` and `-`; unique among staged launches |
| `--minutes` | 30 | positive integer; clamped to 240 |
| `--why` | empty | at most 200 chars; shown to every agent in `queue` |
| `--array` | 1 | positive integer; clamped to 16; one job per task with `SWEEP_INDEX` 0..N-1 |
| `--concurrency` | 0 | non-negative; only with `--array`; at most K tasks at once; the contract may cap it |
| `--artifact` | none | repeatable, at most 8; a repo-relative file path, no traversal |
| `command` | required | after `--`; at most 2000 chars |

Refused when:

- a stop is staged
- there is no command after `--`
- a staged launch already uses the name
- more than 8 launches would be staged per sleep

### sleep

Commits the staged launches and any staged submit, then ends the session. The
kernel submits each launch as a cluster job and wakes the same session with
the results. Takes no arguments. A sleep with no launches is a checkpoint, and
it still spends one sleep from the budget. Refused: a stop is staged.

### message

Sends a message to the run's PR or issue thread (the default), to yourself, or
to a sibling agent. The message is committed immediately and posted or
delivered by the kernel.

| Argument | Default | Constraint |
| --- | --- | --- |
| `text` | none | the message text, or read from `--file`; 1 to 20000 chars |
| `--file` | none | a file the text is read from |
| `--to` | `thread` | `thread`, `self` or `agent-NN` |
| `--reply-to` | none | the sequence number of an inbox message |
| `--show` | none | print a message chain instead of sending |

Refused when:

- `--show` is combined with a message
- the destination is not `thread`, `self` or `agent-NN`
- inter-agent messages are disabled by the contract
- `--reply-to` names no message in the inbox
- more than 8 messages would be staged, or the request exceeds 65536 bytes

### submit

Stages a submit. On the next `sleep` the author's tree is sealed, measured by
the gate and read by the review panel. A credited verdict publishes it.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--report` | empty | a markdown file, 1 to 8000 chars; becomes the PR's research report |
| `--minutes` | none | walltime for each paired gate eval; positive; clamped to 1440 |
| `--claimed-value` | none | a finite number; required for self-reported verification |
| `--claimed-baseline` | none | a finite number; required for self-reported verification |

Refused when a stop is staged, or the report is empty or over the limit.
Self-reported verification additionally requires the two claimed values.

### stop

Ends the run. With `--report`, the report is posted and the run parks
with an open PR. Without one, the run ends without a PR. Committed
immediately.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--report` | empty | a file containing the final report, 1 to 8000 chars |
| `--withdraw` | none | a reason of 1 to 20000 chars; closes the open PR |

Refused when:

- a submit is already staged or committed
- a sleep is staged, or a launch is staged
- `--withdraw` is used with no open PR

## Judge verbs

### finding

Records one finding. Findings accumulate until `conclude` commits the
verdict.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--file` | required | non-empty |
| `--line` | none | 1-indexed; omit for a non-local finding |
| `--confidence` | required | `low`, `medium` or `high` |
| `--summary` | required | 1 to 6000 chars |
| `--detail` | required | 1 to 6000 chars |
| `--kind` | `note` | `change`, `suggestion`, `question` or `note` |
| `--blocking` | off | a confirmed defect that gates merge |
| `--category` | empty | at most 6000 chars; the verifier's gaming taxonomy |

Refused when the confidence, kind, file, summary or detail is empty or
invalid, or when more than 200 findings would be staged.

### conclude

Commits the verdict with all staged findings and ends the session. This is
the judge's final answer. A judge that concludes nothing fails its round.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--notes` | empty | at most 6000 chars; the summary the reader sees |

## Shared verbs

### status

Prints the staged syscalls, the staged launches with their reasons, and the
remaining budget as the kernel last wrote it. Takes no arguments.

### cancel

Discards the staged request and any committed syscall, so nothing is acted
on. Takes no arguments.

### siblings

Prints what the other agents of the fleet are working on, as of this wake.
Takes no arguments.

### queue

Prints the kernel's jobs in the cluster queue, every agent's, with each
launch's one-line `--why`. Answered by the session watcher. When no watcher
runs, the queue is visible again at the next wake.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--wait` | 30 | seconds to wait for the kernel's answer |

### history

Prints this run's launches so far, grouped by sleep, with each job's end state
and exit code. Answered by the session watcher like `queue`.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--wait` | 30 | seconds to wait for the kernel's answer |

### reports

Prints the research-report archive the kernel fetched for this run. With no
names, one summary line per report, newest first. With names, those reports
in full, several at once.

| Argument | Default | Constraint |
| --- | --- | --- |
| `names` | none | report file names from the summary list |

Refused when shared reports are disabled by the contract's channels, or a
name is not in the summary list.

### sync

Asks the kernel to fetch fresh `origin/*` refs and waits inside this session's
own clock. A timeout is not an error, and the refs refresh free at the next wake
regardless.

| Argument | Default | Constraint |
| --- | --- | --- |
| `--minutes` | 35 | how long to wait before giving up; 0 probes and returns |

## Staging and committing

The verbs differ in how they reach the kernel. `launch`, `submit` and
`finding` stage into `.outerloop/request.json`, a file the agent may rewrite
until it commits. `message`, `sleep`, `stop` and `conclude` write the typed
request to `.outerloop/syscall.json`, the file the kernel reads after the
session ends. `sleep` and `conclude` also discard the staged file, so nothing
is committed twice. Building a request and committing it are separate acts:
`status` shows what is staged, and `cancel` discards it.
