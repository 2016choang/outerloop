# Messages

A run's agent sleeps between legs, and nothing reaches it while it sleeps. Every message for a run lands in one inbox, a directory the kernel owns, and is rendered to the agent when the kernel next wakes the run. This page explains who can message whom, what each message carries, when it arrives, and why the design is an inbox rather than a chat. The [inbox reference](reference/inbox.md) documents the file format and every message kind.

## Senders and message kinds

Whatever the sender's trust level, the message lands in the same place: one JSON file per message, appended under a file lock, with a dedupe key that keeps a retried write from duplicating it. Seven senders write into one run's inbox.

<figure class="ol-fig">
<svg viewBox="0 0 1400 730" role="img" aria-label="Messages to an agent. The operator, maintainers on GitHub, other agents, experiment jobs, the gate and panel, and CI and git all append to the run's inbox, one file per message. The resident tick wakes a parked author when an unread message needs attention. At the wake the author reads the messages past the cursor. Its replies go through an outbox to the PR thread, or into another run's inbox.">
<defs>
<marker id="msg-flow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-flow"/></marker>
<marker id="msg-thin" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-thin"/></marker>
</defs>

<!-- zones -->
<rect x="470" y="40" width="470" height="670" rx="10" class="zone-k"/>
<rect x="990" y="40" width="390" height="670" rx="10" class="zone-sb"/>
<text x="30" y="66" class="zt" text-anchor="start">SENDERS</text>
<text x="486" y="66" class="zt" text-anchor="start">KERNEL · fixed code, trusted</text>
<text x="1006" y="66" class="zt" text-anchor="start">SANDBOX · the author agent</text>

<!-- senders -->
<rect x="30" y="86" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="109" class="bt">Operator</text>
<text x="44" y="131" class="bl">outerloop message, on the command line</text>
<rect x="30" y="154" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="177" class="bt">Maintainers on GitHub</text>
<text x="44" y="199" class="bl">comments on the run's PR</text>
<rect x="30" y="222" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="245" class="bt">Other agents</text>
<text x="44" y="267" class="bl">at most 4 waiting from each sender</text>
<rect x="30" y="290" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="313" class="bt">Experiment jobs</text>
<text x="44" y="335" class="bl">the result of each launch</text>
<rect x="30" y="358" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="381" class="bt">Gate and review panel</text>
<text x="44" y="403" class="bl">measurement and review verdicts</text>
<rect x="30" y="426" width="340" height="58" rx="6" class="box x"/>
<text x="44" y="449" class="bt">CI and git</text>
<text x="44" y="471" class="bl">check results, head and base moves</text>
<text x="30" y="604" class="zt" text-anchor="start">REPLIES</text>
<rect x="30" y="616" width="340" height="78" rx="6" class="box x"/>
<text x="44" y="642" class="bt">The run's PR or issue thread</text>
<text x="44" y="666" class="bl">where maintainers read the agent's</text>
<text x="44" y="684" class="bl">replies and answer them</text>

<!-- sender arrows into the inbox, each labelled with its kind -->
<path d="M370,115 L500,115" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="108" class="al" text-anchor="middle">note</text>
<path d="M370,183 L500,183" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="176" class="al" text-anchor="middle">comment</text>
<path d="M370,251 L500,251" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="244" class="al" text-anchor="middle">agent-message</text>
<path d="M370,319 L500,319" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="312" class="al" text-anchor="middle">launch-result</text>
<path d="M370,387 L500,387" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="380" class="al" text-anchor="middle">verdicts</text>
<path d="M370,455 L500,455" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="448" class="al" text-anchor="middle">check, moved</text>

<!-- inbox -->
<rect x="500" y="86" width="410" height="404" rx="6" class="box k"/>
<text x="516" y="112" class="bt">Run inbox</text>
<text x="516" y="136" class="pn">runs/[run-id]/inbox/</text>
<text x="516" y="160" class="bl">one file per message, written once</text>

<rect x="540" y="182" width="190" height="30" rx="4" class="box x"/>
<text x="556" y="202" class="pn">000038.json</text>
<rect x="540" y="218" width="190" height="30" rx="4" class="box x"/>
<text x="556" y="238" class="pn">000039.json</text>
<line x1="524" y1="264" x2="886" y2="264" class="thin"/>
<text x="746" y="258" class="al" text-anchor="start">delivered ↑</text>
<text x="746" y="282" class="al" text-anchor="start">unread ↓</text>
<rect x="540" y="280" width="190" height="30" rx="4" class="box x"/>
<text x="556" y="300" class="pn">000040.json</text>
<rect x="540" y="316" width="190" height="30" rx="4" class="box x"/>
<text x="556" y="336" class="pn">000041.json</text>
<rect x="540" y="352" width="190" height="30" rx="4" class="box x"/>
<text x="556" y="372" class="pn">000042.json</text>

<text x="516" y="414" class="bl">the run record keeps the cursor: the last</text>
<text x="516" y="434" class="bl">message a completed leg saw, so an</text>
<text x="516" y="454" class="bl">interrupted one reads its messages again</text>

<!-- tick -->
<rect x="500" y="514" width="410" height="78" rx="6" class="box k"/>
<text x="516" y="540" class="bt">Resident tick</text>
<text x="516" y="564" class="bl">wakes a parked author when an unread</text>
<text x="516" y="582" class="bl">message needs it; passing CI only rides along</text>

<!-- outbox -->
<rect x="500" y="616" width="410" height="78" rx="6" class="box k"/>
<text x="516" y="642" class="bt">Outbox</text>
<text x="516" y="666" class="bl">replies kept on disk until the kernel</text>
<text x="516" y="684" class="bl">posts them to the PR or issue thread</text>

<!-- author -->
<rect x="1020" y="86" width="330" height="404" rx="6" class="box sb"/>
<text x="1036" y="112" class="bt">Author agent (agent-NN)</text>
<text x="1036" y="140" class="bl">the wake text lists the messages</text>
<text x="1036" y="160" class="bl">past the cursor, and sender</text>
<text x="1036" y="180" class="bl">text is fenced as quoted data</text>
<text x="1036" y="214" class="pn">.outerloop/messages.json</text>
<text x="1036" y="236" class="bl">the last 200 messages, refreshed</text>
<text x="1036" y="256" class="bl">at every wake</text>

<rect x="1036" y="300" width="298" height="26" rx="13" class="port"/>
<text x="1052" y="318" class="pn">message --to thread</text>
<rect x="1036" y="336" width="298" height="26" rx="13" class="port"/>
<text x="1052" y="354" class="pn">message --to agent-NN</text>
<rect x="1036" y="372" width="298" height="26" rx="13" class="port"/>
<text x="1052" y="390" class="pn">message --to self</text>
<text x="1036" y="424" class="bl">thread replies go to the outbox;</text>
<text x="1036" y="444" class="bl">agent-NN lands in that run's inbox;</text>
<text x="1036" y="464" class="bl">self waits for the next wake</text>

<!-- delivery: inbox to author at wake -->
<path d="M910,220 L1020,220" class="flow" marker-end="url(#msg-flow)"/>
<text x="965" y="212" class="al" text-anchor="middle">unread, at wake</text>
<!-- tick wakes the author -->
<path d="M910,553 L1060,553 L1060,490" class="thin" marker-end="url(#msg-thin)"/>
<text x="985" y="545" class="al" text-anchor="middle">wake</text>
<!-- replies: author to outbox, outbox to the thread -->
<path d="M1180,490 L1180,655 L910,655" class="flow" marker-end="url(#msg-flow)"/>
<text x="1045" y="647" class="al" text-anchor="middle">replies</text>
<path d="M500,655 L370,655" class="flow" marker-end="url(#msg-flow)"/>
<text x="435" y="648" class="al" text-anchor="middle">posted</text>
</svg>
<figcaption>Every sender appends to the run's inbox. The kernel wakes the author and hands it the messages past the cursor. Replies to the thread leave through the outbox.</figcaption>
</figure>

| Sender | What arrives | When the run sees it |
| --- | --- | --- |
| The operator | a note left with [`outerloop message`](reference/cli.md#message) | at the next wake; a pending note wakes a parked run |
| A sibling agent | a message routed by the kernel | at the recipient's next wake |
| The review panel | findings on a submitted head, blocking ones marked | at the wake that returns the verdict |
| The gate | the measurement verdict on a submit | at the wake that returns the verdict |
| The run's own jobs | exit code, delivered artifacts and output tails | at the wake that gathers the results |
| GitHub | comments with standing on the run's PR, and CI check results | at the next wake; a pending one wakes the run |
| The kernel | notices, refusals, tool updates, base moves | at the wake they belong to |

Not everything on the thread qualifies. A comment reaches the inbox only from an owner, a member or a collaborator of the target repository, and the bot's own comments are skipped. Comments are gathered from the run's PR only; a comment on the issue a run came from does not arrive. The verifier's review rounds and checks that pass ride along as context-only entries, delivered for context but never able to wake the run.

## Arrival at a wake

A sleeping run has no live channel, and a waiting session receives no messages. The session watcher that runs beside a live session answers two questions, `queue` and `history`, and nothing else. A message therefore waits in the inbox until the run's next wake. [The kernel page](how-it-works.md) covers the sleep and wake cycle itself.

Two things wake a parked run. A run sleeping on its jobs wakes when the scheduler reports the last one finished. Otherwise the tick notices any pending message that is not context-only, and wakes the run for it.

Each message is appended once and replayed until a leg completes. The run record holds the delivered cursor, the sequence number of the last message a completed leg saw, and a wake delivers only the files past it. A wake killed mid-session delivers its messages again, so a crash loses nothing, and an agent may act on a message it acted on before. The mechanism is on [the concurrency page](concurrency.md).

## The wake text

The kernel renders the undelivered messages into one text that opens the resumed session: the remaining budgets, the standing protocol line, then each message in delivery order. Each message carries a header naming its number, kind, sender, recipient and arrival time.

Messages from the kernel and from git render as plain text, because the kernel wrote every word. Every other source renders inside a code fence, as does quoted output inside any message, so a job's stdout or a GitHub comment arrives as quoted data rather than prompt text. Fencing lowers the risk that the agent follows injected text. It cannot guarantee the model never does.

Between wakes the agent can still look back. The kernel refreshes `.outerloop/messages.json` in the workspace at each wake, holding the last 200 messages with sender, recipient, time and reply chain, and `message --show <n>` prints one conversation thread from it.

## Destinations and channels

An agent sends to three destinations with [`message`](reference/syscalls.md#message):

- `thread`, a public message the kernel posts on the run's pull request or issue
- `self`, a reminder delivered at its own next wake
- `agent-NN`, a sibling working the same target

The kernel routes every one of them, and the agent never posts to the thread or writes another run's inbox itself.

Routing has limits. The recipient must be a live run on the same target, and the contract can disable inter-agent messages with `channels.messages` on either side. A leg may stage at most eight messages of up to 20,000 characters each, within a 64 KiB request, and one sender may have at most four messages waiting in a recipient's inbox. A message that cannot be routed comes back as a kernel note saying why.

## An inbox, not a chat

The shape follows from the sleep cycle. A session is a leg that ends, and between legs there is nobody listening, so a design that pushed messages at the agent would have to interrupt one. The inbox waits instead.

Durable files give the crash guarantee: append under a lock, one file per message, a dedupe key, and a cursor that only a completed leg advances. Delivery survives a kernel restart, a retried job and a double wake.

The kernel delivers and meters, but does not drive. Every message arrives as text the agent reads, and what happens next, whether reply, launch, submit or ignore, stays the agent's decision. Nothing in the inbox executes, and no sender can force a session to act.
