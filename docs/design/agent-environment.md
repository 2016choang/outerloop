# Environment, not workflow

Status: proposal, 2026-09-25.

## The rule

The kernel adds structure only where it guards a trust boundary. Everything
else is environment: the agent uses its tools as it sees fit, and research
habits are taught through the brief and skills, where they can change without
a kernel release and where each adopter can set their own.

The boundaries:

- **Credentials.** Sessions hold none. Anything published goes through the
  kernel.
- **Measurement and credit.** The gate, the panel, the ledger, and what
  reaches the base branch.
- **Budgets.** GPU-hours, launches and sleeps.
- **Other agents' state.** Another author's branches and private memory.

A rule that guards none of these is a convention, not a mechanism. If a
convention keeps failing in a way that crosses a boundary, it earns a
mechanism then, not before.

## The guidance layer

The same split now applies to the kernel's agent-facing text. Mechanics —
session lifecycle, the report's field names, the fold procedure, the
anti-fabrication rules — stay in the kernel. Research taste — how to pick a
direction, how much landscape a claim needs, what counts as publishable —
moved into a GUIDANCE layer selected by the contract's top-level `guidance`
key:

- `default`: the kernel's own pack (the texts that used to live in the
  brief and the verifier rubric);
- `none`: no guidance at all;
- a repo-relative directory holding `author.md` and `panel.md` —
  recommended location `.github/outerloop/guidance/`, which agents can
  never write. A missing file is empty guidance, never a contract error.

One reader, `outerloop.contract.read_guidance`, runs with the SAME ref the
contract was read at — the default branch for the Actions verifier, and
for climbs and wakes the base ref's git objects (`git show` at the pinned
sha; a working-tree file is never read, and a symlink blob — whose text is
its target, not file content — is refused as empty guidance) — so an
agent can never influence its own guidance. Each file is capped at 8,000
characters. The author half is inserted LAST in the brief under a
`# Lab guidance` heading labelled advisory ("the kernel rules above win");
the panel half rides the judges' own prompt, after the kernel rubric and
outside the fence that holds the claim as untrusted data, labelled as this
repository's advisory standards — the judges may raise it only as advisory
findings.

The panel may block only on validity: harness exploitation, ruler/seed
fishing, data leakage, a confirmed contradiction between the claim and its
own evidence, and scope and measurement facts. The kernel rubric names no
taste categories at all — the aggregation and landscape standards live
only in the pack, so `none` leaves the judges validity criteria only. The
former one-idea mandate is gone entirely — a repository that wants it
writes it into its guidance files, where it is advice, never a blocking
rule.

## What this changes now

**1. Sessions may commit on their own line.** The rule "do not commit" guards
no boundary. The sealed tree is what gets measured, the scope check guards
what reaches the base branch, and the `.git` tamper guard protects the
repository. The rule did cause harm: an author could not merge the updated
base branch into its own work until a special permission was added, and
merging another author's work would have needed another. Lifting it removes that class of exception.
Pushing stays with the kernel.

**2. Authors may publish branches under their own namespace.** One generic
capability: stage a push of a named branch under `ideas/<agent-id>/`. The
kernel checks these things and nothing else:

- the name stays inside the author's namespace and is a valid ref name;
- the author's memory (`AGENT_MEMORY.md`, `agent_memory/`) is left out of
  the published tree, and instruction-bearing files carry the base
  branch's reviewed versions, the same cleanup the kernel applies when it
  checks out a line;
- the author stays within a bound: at most 20 published branches, and a
  push that adds more than 50 MB of new objects is refused. The byte
  bound applies to every push the kernel makes for an author, line
  snapshots included, which today are unbounded. Both numbers are
  defaults the contract can change.

Memory is owned, not secret. Every session already fetches every agent
line, so an author's memory is readable by its siblings today. Leaving it
out of the published tree keeps it out of merges and measured trees; it
does not remove it from history. If memory ever needs to be confidential,
it has to leave the shared repository, which is a separate design.

The author may also reset or delete its own branches. Work another author
already merged survives in that author's line; only the shared name goes.

Publishing costs no budget and earns no credit. A failed publish comes back
to the author as a message.

Branches under another agent's namespace are read-only to everyone else.
Every session already fetches all branches, so reading and merging them is
plain git.

**3. Ideas are a convention, not a mechanism.** How an author names an idea,
describes it, marks it parked or abandoned, and finds a sibling's idea is up
to the author, using git. The brief and a skill suggest a default: the
branch name says what the idea is, the commit message states the mechanism
and points at the reports, and a PR that builds on another author's idea
cites its branch and commit. The board lists the branches under `ideas/`
with their last commit, and nothing more.

## Two protections a merge needs

When a session merges another branch into its line, the kernel keeps the
receiver's private memory and the reviewed instruction files. It already
resets instruction files after merging the base branch at run start; keeping
the receiver's memory through an arbitrary merge is new, since today's rule
for it covers only resets. The scope check is unchanged: code merged from an
idea counts as the author's change when it reaches the base branch.

## Deferred

Idea status fields, automatic parking, per-author caps, "best number"
tracking, a separate discovery command, and an operator command for humans.
Each can be added if the plain version shows a need.

## Decided

- Authors may reset or delete their own published branches (owner,
  2026-09-25).
- The namespace is `ideas/<agent-id>/`: it names the purpose. Adopters may
  use it for other purposes; the kernel only enforces ownership.
