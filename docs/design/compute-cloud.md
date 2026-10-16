# Cloud compute and the run-artifact store

**Status: two parts. Part 1 — Slurm on a cloud — is the 0.4 deployment ("the
cloud release"). Part 2 — clouds without Slurm — is a direction, not
scheduled. The Slurm backend is never retired.**

## Part 1 — Now: Slurm on a cloud (0.4)

The kernel's Slurm backend runs unchanged on a cloud-hosted Slurm cluster:
the provider's Slurm blueprint tool builds it, and queues, accounts,
partitions and the container runtime carry over. This is 0.4's answer to
cloud compute: the easiest path, but not one every cloud offers — Part 2.

### What the deployment needed

Facts from one real deployment, kept generic.

- **Topology.** One always-on controller VM runs the Slurm controller, an NFS
  home, the kernel's instances, a watchdog and the model gateway
  ([model-gateway.md](model-gateway.md)). Everything else autoscales from
  zero: a CPU partition on on-demand nodes, and a spot GPU partition spread
  over several zones. Nodes suspend after a few idle minutes.
- **Network.** No external IPs (org policy). Egress goes through NAT; the
  operator reaches the controller through the provider's IAP-style tunnel.
  NAT egress was about 5% of the bill and was not metered.
- **Credentials.** One service account per node class. The controller may
  call the model API; CPU nodes (sessions) may; GPU nodes (agent experiment
  code) get no project roles, so agent code cannot reach the model API with
  the cloud credential. This complements the gateway's role-scoped keys.
- **Spot.** Spot GPU nodes are preempted: jobs end NODE_FAIL or get
  requeued. A measurement must not read a previous attempt's files. Runs
  lost to infrastructure were replaced, not counted.
- **Images.** Node boot installed tools (apptainer, gh, uv) through the
  package manager, which made boots slow: bake an image instead. The GPU
  driver was too old for the default PyTorch CUDA wheels: pin a CUDA build
  that matches the driver.
- **Broken nodes.** A failed first boot leaves the node marked configured:
  replace the VM, do not reboot it.
- **Cost.** Per-hour nodes plus per-token model spend. A spend watchdog (a
  systemd timer) and a cache reaper ran on the controller. The idle
  controller costs money, so teardown is part of the plan.
- **Teardown.** Archive results to a bucket first — excluding workspaces,
  caches and credentials, and scanning for tokens. And commit every
  hand-written config (gateway config, hooks, units, timers) to the
  operator's private ops repo when it is created: at teardown we lost the
  gateway config because only results were archived.
- **Operator time.** The operator's cloud login expired after about twelve
  hours on that provider;
  anything that needs the operator's credential waits for the next login.
  Infrastructure apply (Terraform) is the operator's step, outside the
  kernel.

### Kernel gaps the run exposed (0.4 work)

- **Spot preemption and requeue-safe measurement.** Already in
  [Unreleased]: a baseline, or an eval that completes cleanly without a
  result, is an infrastructure failure, retried twice uncharged, then parked
  (#474). A candidate eval killed by preemption (`NODE_FAIL`, `PREEMPTED`)
  still fails as the measure's own error. The policy for spot is set in
  [accelerators.md](accelerators.md) ("Preemption"): fail and report on
  wake, charge the time, and add automatic retries only with cumulative
  accounting; Slurm on a cloud follows it. Any requeue must not read files
  a previous attempt left.
- **Cost capture for every backend.** Per-token model spend is priced from
  the operator table (`OUTERLOOP_TOKEN_PRICES`, [Unreleased]). Node cost and
  NAT egress stay operational reporting, outside the kernel's budgets, as
  [accelerators.md](accelerators.md) decides ("Money in the kernel: no").
- **The model gateway.** Merged design ([model-gateway.md](model-gateway.md),
  #464); build planned for 0.4, with this deployment as its first host. Role
  isolation and per-key metering move into the gateway.

## Part 2 — Long term: clouds without Slurm

Needed when a cloud or accelerator offers no Slurm — per-job VMs (the per-job
TPU backend in [accelerators.md](accelerators.md)), Kubernetes, serverless
GPUs — or when capacity is spread over regions and jobs should chase
wherever it is cheapest. Slurm on a cloud stays supported; the Slurm backend
is never retired. [external.md](external.md) names the `compute` interface
and the storage interface this part builds on. The kernel's loop only
submits then polls, never reaching into a running job, but today's `Compute`
protocol is Slurm-shaped (`compute.py`): queue and lane queries beside
submit and cancel, a `JobSpec` with account, partition, dependencies and
arrays, and results read over the shared filesystem. A backend without
Slurm needs a narrower spec and an explicit result fetch; the per-job TPU
backend in accelerators.md faces the same work.

### git in, artifact out

A cloud experiment job runs self-contained on its own local disk. Code
crosses the boundary as **git** (the job clones the target, runs the
session, seals the candidate, pushes the branch — already how code flows).
Results cross as **one artifact to an object store**, keyed by run_id, which
the kernel pulls on wake. The syscall channel stays local to the box; only
the terminal result leaves. Both sides reach the store outbound, so the
kernel keeps its no-inbound posture.

This is deliberately not a shared filesystem: a cloud shared FS is
region-pinned, and the compute that mounts it must sit in the same region,
often the same zone — forfeiting the main reason to be on cloud, cheap
capacity that varies by region and is cheapest on spot. git is global and
the store is touched only at a job's start and end, so the substrate is
region-agnostic. (R2 is attractive: no egress fees, unlike cross-region
S3/GCS.) A shared or parallel filesystem earns its keep in one place only:
inside a single multi-node training job, same-zone by physical necessity,
provisioned by that job and torn down with it — never a fleet-wide
substrate. Big shared datasets are the same story: a per-region read cache,
not a global FS.

**Rule:** the fleet substrate is region-agnostic (git + object store); a
shared/parallel FS, if it appears at all, is scoped inside one multi-node job
and dies with it.

### The resident decoupled from the experiments

The resident is a cheap CPU orchestrator — wake on cadence, read run records,
submit, poll, publish the board. It needs outbound network and no GPU, and
it does not need to sit where experiments run. Cloud does not move the
resident; it moves the experiments. Today the backend is chosen per
deployment. Choosing it per run, so one resident runs cheap jobs on Slurm
and bursts GPU-heavy ones to cloud, is proposed here and would revisit
[multi-cluster.md](multi-cluster.md)'s tier-3 non-goal (open decision 6).

For a cloud-only adopter the same loop lives as a small always-on CPU VM (a
cloud VM has no walltime, so the Slurm handover chain is unnecessary) or a
serverless cron tick (near-zero idle cost; the tick is stateless per
invocation). `outerloop start` provisions the resident once ("job zero") and
the laptop goes away. Where the tick lives is already a deployment choice
(`OUTERLOOP_TICK_HOST`: `resident` or `login` today); a cloud controller VM
runs the `login` shape.

### Durable state without a database

An ephemeral or spot resident cannot lean on a shared disk for its own state
— run records, markers, job handles. The reflex is a database; resist it.
The kernel's state is single-writer, small, blob-shaped and scanned fresh
each tick — a filesystem workload, not a database workload, and a database
would add a standing service every adopter must provision. Durability is a
**storage-backend choice**, in increasing weight: a persistent volume (the
default; the state root is a mounted POSIX directory, zero kernel change) or
an object-store state root for the diskless serverless shape, reusing the
same store the results already need. Much of the durable state already
lives in git (the research-log branch); what needs a home off-disk is the
volatile in-flight state. **Rule:** state stays blob-shaped and
single-writer; disk → volume → bucket, never a database.

### Providers, and not writing N of them

Three shapes: managed batch/queue (AWS Batch, GCP Batch, a second Slurm), raw
GPU rental (RunPod, Lambda, vast.ai), serverless GPU (Modal, Replicate). The
first cloud backend should be a **meta-provider**, not a specific cloud:
[SkyPilot](https://skypilot.readthedocs.io) abstracts the majors behind one
submit-job API with spot and auto-teardown built in — one backend buys most
of the clouds. Modal is the strong second (serverless plus native volumes);
native per-vendor backends only where a meta-provider does not reach.

Slurm gave lifecycle, cost and security for free; cloud makes them explicit.
Ephemeral per-job first, a warm pool only if cold-start latency bites.
Teardown is load-bearing — you pay per second, and a box holding
credentials should not outlive its job — with a per-run dollar cap.
Credentials are the adopter's own on their rented box, but short-lived
scoped tokens rather than long-lived ones.

### The run-artifact store

A run produces a bundle of artifacts, not a scalar, and they all fit the
git+object-store substrate: results, reports, traces and measurement
trajectories are artifacts a job emits, keyed by run_id. This is better even
on Slurm, where traces and curves live in run dirs today and get reaped;
first-class object-store artifacts are durable and linkable. Split by shape:

- **Small, structured → the measurement record** (durable state, published
  to the board). The final scalar becomes a metric map — the climbed metric
  plus diagnostic and gate metrics, each with provenance (baseline pair,
  seed, tree_sha, image).
- **Time-series and large → object-store artifacts** keyed by run_id, with a
  pointer in the record: the loss trajectory, checkpoints, sample outputs,
  the trace. Not in git (churn), not in the small state (bloat).

Traces: store them in the object store beside the result, pointer in the
record. Private by default — a trace holds prompts, tool outputs and target
code; public sees the report, never the raw trace, and only through the
existing sanitizer chain. Bench-shaped (hermes' `save_sample`) so one store
feeds the reviewer bench and the lessons distiller. Retention: merged-win
traces forever, the rest aged out or downsampled. A trace streams to a local
file and is uploaded at checkpoints, so a preempted job still leaves what it
got; a spot node's local disk dies with it.

Richer measurement: standardize the emission, do not parse stdout — one
format the eval harness writes (a `metrics.jsonl` trajectory plus a final
`measurement.json` metric map), optional and graceful. The board
(per-attempt curves), compute-normalized metrics, the agent's depth loop and
verification all need the trajectory. No metrics database; W&B stays an
optional live view.

### Open decisions (for when this version comes up)

1. **Result/state transport** — object store (R2, recommended) vs. provider-native volumes.
2. **First backend** — SkyPilot-as-meta-provider (recommended) vs. one native cloud.
3. **Provisioning model** — ephemeral-per-job (recommended) vs. a warm resident.
4. **Retention** for traces, curves and checkpoints.
5. **Sequencing** — traces-as-artifacts and richer measurement are useful on
   Slurm independent of cloud; decide whether they ship earlier on their own.
6. **Backend per run** — one resident driving Slurm and a cloud at once, or
   one deployment per backend (multi-cluster.md's tier 2).
