# The kernel and its agents

Outerloop runs untrusted research agents against a small trusted kernel, and
only the kernel's own measurement can grant credit for a change. This page
explains the parts of that split everyone meets: where trust sits, how agents
talk to the kernel, how runs wait for experiments, and how a change earns
credit. The full design lives in [the architecture page](design/architecture.md).

## The kernel at a glance

<figure class="ol-fig">
<svg viewBox="0 0 1600 940" role="img" aria-label="Outerloop architecture. Sandboxed author, judge and maintenance agents reach a trusted kernel only through a small syscall CLI. The kernel meters compute, dispatches jobs to Slurm, measures submissions itself, runs judge agents that can only withhold credit, and publishes through GitHub.">
<defs>
<marker id="k-flow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-flow"/></marker>
<marker id="k-credit" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-credit"/></marker>
<marker id="k-thin" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-thin"/></marker>
<marker id="k-model" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-model"/></marker>
</defs>

<rect x="20" y="58" width="452" height="862" rx="10" class="zone-sb"/>
<rect x="660" y="58" width="610" height="862" rx="10" class="zone-k"/>
<text x="36" y="84" class="zt" text-anchor="start">SANDBOX · untrusted agents</text>
<text x="676" y="84" class="zt" text-anchor="start">KERNEL · fixed code, trusted</text>
<text x="1290" y="84" class="zt" text-anchor="start">OUTSIDE WORLD</text>
<text x="566" y="84" class="zt" text-anchor="middle">SYSCALLS</text>
<line x1="482" y1="60" x2="482" y2="920" class="bound"/>
<line x1="650" y1="60" x2="650" y2="920" class="bound"/>

<rect x="52" y="92" width="412" height="178" rx="6" class="box sb"/>
<rect x="46" y="98" width="412" height="172" rx="6" class="box sb"/>
<rect x="40" y="104" width="412" height="166" rx="6" class="box sb"/>
<text x="52" y="128" class="bt" text-anchor="start">Author agents  (agent-NN, one hypothesis per run)</text>
<text x="52" y="150" class="bl" text-anchor="start">harness · Claude Code, Codex, Hermes</text>
<text x="52" y="170" class="bl" text-anchor="start">no repository credentials</text>
<text x="52" y="190" class="bl" text-anchor="start">edits only the contract's allowed paths</text>
<text x="52" y="210" class="bl" text-anchor="start">edit → launch → sleep → wake → … → submit</text>

<rect x="40" y="386" width="412" height="196" rx="6" class="box sb"/>
<text x="52" y="410" class="bt" text-anchor="start">Judge agents  (review panel)</text>
<text x="52" y="432" class="bl" text-anchor="start">verify · gaming, leakage, claims</text>
<text x="52" y="452" class="bl" text-anchor="start">review · code, gain attribution</text>
<text x="52" y="472" class="bl" text-anchor="start">read-only · the kernel decides when</text>
<text x="52" y="492" class="bl" text-anchor="start">they run · can only withhold credit</text>

<rect x="40" y="698" width="412" height="200" rx="6" class="box sb"/>
<text x="52" y="722" class="bt" text-anchor="start">Maintenance agents  (separate lane)</text>
<text x="52" y="744" class="bl" text-anchor="start">digest · cleanup items</text>
<text x="52" y="764" class="bl" text-anchor="start">steward · upkeep as contract PRs</text>
<text x="52" y="784" class="bl" text-anchor="start">neither touches solver credit</text>

<rect x="484" y="150" width="168" height="26" rx="13" class="port"/>
<text x="496" y="168" class="pn" text-anchor="start">launch</text>
<text x="642" y="168" class="pa" text-anchor="end">--array N</text>
<rect x="484" y="182" width="168" height="26" rx="13" class="port"/>
<text x="496" y="200" class="pn" text-anchor="start">sleep</text>
<rect x="484" y="214" width="168" height="26" rx="13" class="port"/>
<text x="496" y="232" class="pn" text-anchor="start">status</text>
<text x="642" y="232" class="pa" text-anchor="end">cancel</text>
<rect x="484" y="284" width="168" height="26" rx="13" class="port"/>
<text x="496" y="302" class="pn" text-anchor="start">message</text>
<text x="642" y="302" class="pa" text-anchor="end">agent-NN</text>
<rect x="484" y="312" width="168" height="26" rx="13" class="port"/>
<text x="496" y="330" class="pn" text-anchor="start">siblings</text>
<text x="642" y="330" class="pa" text-anchor="end">reports</text>
<rect x="484" y="340" width="168" height="26" rx="13" class="port"/>
<text x="496" y="358" class="pn" text-anchor="start">queue</text>
<text x="642" y="358" class="pa" text-anchor="end">history</text>
<rect x="484" y="368" width="168" height="26" rx="13" class="port"/>
<text x="496" y="386" class="pn" text-anchor="start">sync</text>
<rect x="484" y="420" width="168" height="26" rx="13" class="port"/>
<text x="496" y="438" class="pn" text-anchor="start">submit</text>
<text x="642" y="438" class="pa" text-anchor="end">--report</text>
<rect x="484" y="452" width="168" height="26" rx="13" class="port"/>
<text x="496" y="470" class="pn" text-anchor="start">stop</text>
<text x="642" y="470" class="pa" text-anchor="end">--withdraw</text>
<text x="566" y="500" class="pz" text-anchor="middle">author · shared</text>
<rect x="484" y="560" width="168" height="26" rx="13" class="port"/>
<text x="496" y="578" class="pn" text-anchor="start">finding</text>
<text x="642" y="578" class="pa" text-anchor="end">--blocking</text>
<rect x="484" y="592" width="168" height="26" rx="13" class="port"/>
<text x="496" y="610" class="pn" text-anchor="start">conclude</text>
<text x="566" y="640" class="pz" text-anchor="middle">judge</text>

<rect x="676" y="98" width="578" height="32" rx="6" class="box kh"/>
<text x="688" y="113" class="bt" text-anchor="start">Resident tick</text>
<text x="688" y="126" class="bl" text-anchor="start">long-lived Slurm job or local loop · state in files and git</text>
<rect x="676" y="140" width="250" height="110" rx="6" class="box k"/>
<text x="688" y="164" class="bt" text-anchor="start">Meter</text>
<text x="688" y="186" class="bl" text-anchor="start">GPU-hours · launches · wakes</text>
<text x="688" y="206" class="bl" text-anchor="start">checked before submit,</text>
<text x="688" y="226" class="bl" text-anchor="start">refunded after</text>
<rect x="946" y="140" width="308" height="110" rx="6" class="box k"/>
<text x="958" y="164" class="bt" text-anchor="start">Dispatcher</text>
<text x="958" y="186" class="bl" text-anchor="start">freezes a snapshot, submits</text>
<text x="958" y="206" class="bl" text-anchor="start">it to Slurm · sweeps run</text>
<text x="958" y="226" class="bl" text-anchor="start">as throttled arrays</text>

<rect x="676" y="280" width="310" height="112" rx="6" class="box k"/>
<text x="688" y="304" class="bt" text-anchor="start">Inbox</text>
<text x="688" y="326" class="bl" text-anchor="start">results · findings · messages</text>
<text x="688" y="346" class="bl" text-anchor="start">· operator notes · a wake</text>
<text x="688" y="366" class="bl" text-anchor="start">delivers them</text>
<rect x="1002" y="280" width="252" height="112" rx="6" class="box k"/>
<text x="1014" y="304" class="bt" text-anchor="start">Session watcher</text>
<text x="1014" y="326" class="bl" text-anchor="start">answers queue, history and</text>
<text x="1014" y="346" class="bl" text-anchor="start">sync in seconds, while</text>
<text x="1014" y="366" class="bl" text-anchor="start">the session waits</text>

<rect x="676" y="410" width="260" height="120" rx="6" class="box kc"/>
<text x="688" y="434" class="bt" text-anchor="start">Measure  (the gate)</text>
<text x="688" y="456" class="bl" text-anchor="start">candidate vs base · paired</text>
<text x="688" y="476" class="bl" text-anchor="start">or cached per base · seed</text>
<text x="688" y="496" class="bl" text-anchor="start">drawn at measurement time</text>
<text x="688" y="516" class="bl" text-anchor="start">credit when gain beats floor</text>
<rect x="956" y="410" width="298" height="120" rx="6" class="box k"/>
<text x="968" y="434" class="bt" text-anchor="start">Panel rules</text>
<text x="968" y="456" class="bl" text-anchor="start">judges spawn on credited work</text>
<text x="968" y="476" class="bl" text-anchor="start">verdicts combined by fixed rules</text>
<text x="968" y="496" class="bl" text-anchor="start">blocking findings reach the inbox</text>

<rect x="676" y="560" width="186" height="110" rx="6" class="box k"/>
<text x="688" y="584" class="bt" text-anchor="start">Publish</text>
<text x="688" y="606" class="bl" text-anchor="start">PR · measured numbers</text>
<text x="688" y="626" class="bl" text-anchor="start">and report · draft</text>
<text x="688" y="646" class="bl" text-anchor="start">until cleared</text>
<rect x="872" y="560" width="186" height="110" rx="6" class="box k"/>
<text x="884" y="584" class="bt" text-anchor="start">Research lines</text>
<text x="884" y="606" class="bl" text-anchor="start">agents/agent-NN</text>
<text x="884" y="626" class="bl" text-anchor="start">branch and notes,</text>
<text x="884" y="646" class="bl" text-anchor="start">kept across runs</text>
<rect x="1068" y="560" width="186" height="110" rx="6" class="box k"/>
<text x="1080" y="584" class="bt" text-anchor="start">Ledger</text>
<text x="1080" y="606" class="bl" text-anchor="start">research-log branch</text>
<text x="1080" y="626" class="bl" text-anchor="start">pending → confirmed</text>
<text x="1080" y="646" class="bl" text-anchor="start">when the merge is seen</text>

<rect x="676" y="690" width="578" height="208" rx="6" class="box k"/>
<text x="688" y="714" class="bt" text-anchor="start">Contract  .outerloop.yaml</text>
<text x="688" y="736" class="bl" text-anchor="start">benchmark · command, metric, direction, gpus, eval minutes</text>
<text x="688" y="756" class="bl" text-anchor="start">floor · min_delta (calibrated) · baseline · paired or cached</text>
<text x="688" y="776" class="bl" text-anchor="start">budgets · GPU-hours per run · runs per week · width</text>

<text x="688" y="796" class="bl" text-anchor="start">scope · allowed paths · steward paths · merge · auto or manual</text>
<text x="688" y="816" class="bl" text-anchor="start">guidance · taste files (advisory) · channels · messages, siblings</text>
<text x="688" y="836" class="bl" text-anchor="start">always forbidden · contract, roadmap, .github</text>

<rect x="1290" y="104" width="290" height="164" rx="6" class="box x"/>
<text x="1302" y="128" class="bt" text-anchor="start">Slurm cluster</text>
<text x="1302" y="150" class="bl" text-anchor="start">runs frozen snapshots and</text>
<text x="1302" y="170" class="bl" text-anchor="start">the gate's measurements</text>
<text x="1302" y="190" class="bl" text-anchor="start">on-prem · cloud · local</text>
<rect x="1290" y="290" width="290" height="234" rx="6" class="box x"/>
<text x="1302" y="314" class="bt" text-anchor="start">GitHub</text>
<text x="1302" y="336" class="bl" text-anchor="start">the App is the only write</text>
<text x="1302" y="356" class="bl" text-anchor="start">path · PRs, CI, auto-merge</text>
<text x="1302" y="376" class="bl" text-anchor="start">when allowed · merged code</text>
<text x="1302" y="396" class="bl" text-anchor="start">is the next runs' base</text>

<rect x="1290" y="548" width="290" height="122" rx="6" class="box x"/>
<text x="1302" y="572" class="bt" text-anchor="start">Model endpoints</text>
<text x="1302" y="594" class="bl" text-anchor="start">hosted APIs, or self-hosted</text>
<text x="1302" y="614" class="bl" text-anchor="start">vLLM behind a gateway</text>
<rect x="1290" y="690" width="290" height="208" rx="6" class="box x"/>
<text x="1302" y="714" class="bt" text-anchor="start">People  (optional)</text>
<text x="1302" y="736" class="bl" text-anchor="start">maintainer · merges, comments,</text>
<text x="1302" y="756" class="bl" text-anchor="start">edits the contract</text>
<text x="1302" y="776" class="bl" text-anchor="start">operator CLI · start · status ·</text>
<text x="1302" y="796" class="bl" text-anchor="start">limits · message · end · rebind</text>

<polyline points="652,163 676,163" class="flow" marker-end="url(#k-flow)"/>
<polyline points="652,195 676,195" class="flow" marker-end="url(#k-flow)"/>
<polyline points="926,195 946,195" class="flow" marker-end="url(#k-flow)"/>
<polyline points="1254,170 1290,170" class="flow" marker-end="url(#k-flow)"/>
<text x="1272" y="188" class="al" text-anchor="middle">sbatch</text>
<polyline points="1310,268 1310,271 840,271 840,280" class="flow" marker-end="url(#k-flow)"/>
<text x="1110" y="266" class="al" text-anchor="middle">results</text>
<polyline points="700,280 700,262 452,262" class="flow" marker-end="url(#k-flow)"/>
<text x="566" y="258" class="al" text-anchor="middle">wake</text>
<polyline points="652,297 676,297" class="thin" marker-end="url(#k-thin)"/>
<polyline points="652,367 664,367 664,401 1128,401 1128,392" class="thin" marker-end="url(#k-thin)"/>
<polyline points="652,433 676,433" class="credit" marker-end="url(#k-credit)"/>
<polyline points="936,470 956,470" class="credit" marker-end="url(#k-credit)"/>
<text x="946" y="462" class="al credit-t" text-anchor="middle">credited</text>
<polyline points="1105,530 1105,540 660,540 660,573 652,573" class="thin" marker-start="url(#k-thin)" marker-end="url(#k-thin)"/>
<text x="884" y="536" class="al" text-anchor="middle">spawns judges · findings + verdict</text>
<polyline points="1254,470 1266,470 1266,552 770,552 770,560" class="credit" marker-end="url(#k-credit)"/>
<text x="1010" y="549" class="al credit-t" text-anchor="middle">cleared</text>
<polyline points="770,670 770,680 1282,680 1282,420 1290,420" class="credit" marker-end="url(#k-credit)"/>
<text x="1190" y="676" class="al credit-t" text-anchor="middle">PR</text>
<polyline points="1290,500 1272,500 1272,615 1254,615" class="credit" marker-end="url(#k-credit)"/>
<text x="1280" y="496" class="al credit-t" text-anchor="end">merge seen</text>
<polyline points="246,898 246,930 1435,930 1435,664" class="model" marker-end="url(#k-model)"/>
<text x="840" y="925" class="al" text-anchor="middle">every session calls its model through the endpoint layer</text>
</svg>
<figcaption>The diagram shows one run's path through the system. Orange arrows trace the credit path.</figcaption>
</figure>

The kernel's parts are few. A resident tick, either a long-lived Slurm job or
a local loop, does all the acting. A meter guards budgets, a dispatcher turns
launches into cluster jobs, an inbox holds what a woken agent should see, and
a session watcher answers live questions while a session waits. The gate
measures, the panel rules judge, publish opens pull requests, and the ledger
records every confirmed gain.

## The trust boundary

Everything inside the sandbox is untrusted. Author agents work in a container
with no repository credentials, so they cannot push, merge or comment on
GitHub. Their edits are confined to the paths the contract allows, and three
paths are forbidden everywhere: the contract itself, the roadmap and
`.github/`. Judge agents are read-only by design, and the kernel decides when
they run. Maintenance agents work in their own lane and never touch solver
credit.

Everything outside the sandbox is trusted kernel code. It meters compute,
dispatches jobs, measures submissions and publishes results, and it is the
only writer to the target repository, through a GitHub App. Because judges
work on author-written checkouts, the kernel re-creates the `.outerloop/`
channel they speak through and validates every field they commit. The full
threat model lives in [the architecture page](design/architecture.md).

## The syscall surface

Agents reach the kernel only through one command-line tool, installed at
`.outerloop/syscall` in the workspace. The tool is a single standalone Python
file with no dependencies, so any harness can call it: Claude Code, Codex and
Hermes all use the same verbs. Which verbs a role may call is set by its role
specification, so a new role or harness needs no kernel change.

The tool checks arguments and gives fast feedback, but it is not the trust
boundary. It stages requests into `.outerloop/request.json`, and the
committing verbs write a typed request to `.outerloop/syscall.json`, which the
kernel reads after the session ends. The kernel re-validates every field
authoritatively. The [syscall reference](syscalls.md) lists each verb, its
arguments and its refusals.

## Sleep and wake

A run is one hypothesis, and no single agent session spans a multi-hour GPU
experiment. The author stages the jobs it wants, then calls `sleep` and ends
its turn. The session ends, so no model tokens are spent waiting, and the
kernel submits each launch as a cluster job on a frozen snapshot of the code.

When every job has finished, the kernel wakes the same session. The author
resumes with its plan, notes and prior context intact, and finds each job's
exit code, output tails and delivered artifacts in its inbox. The cycle
repeats, launch by launch, until the author submits or stops the run. How the
kernel parks and resumes a run safely is the subject of [the concurrency
page](concurrency.md), and the message format is covered in [the inbox
reference](inbox.md).

## The credit path

Only the kernel's own measurement grants credit, and nothing else can. A
`submit` seals the author's tree. The gate then measures the candidate
against the base, either as a paired comparison or against a cached baseline
measurement, under a seed the kernel draws at measurement time. The candidate
is credited only when its gain beats the calibrated floor in the contract
(`min_delta`), so seed luck cannot pose as progress.

Judges come after credit, not before. On a credited submission the kernel
spawns judge agents, and their verdicts are combined by fixed rules. A judge
can withhold credit with a blocking finding, which goes to the author's
inbox for a revision, but no judge can grant credit or publish code.

Publishing is also the kernel's alone. A credited, cleared submission
becomes a pull request carrying the measured numbers and the author's report,
held as a draft until the panel clears it. A human merges it, or the contract
opts into `merge: auto`. Only when the kernel sees the merge does it record
the gain as confirmed in the ledger, and the merged code becomes the next
runs' base.
