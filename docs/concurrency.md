# Concurrency

Agents edit their workspaces while the kernel dispatches and wakes runs, and cluster jobs finish in any order. This page explains the five designs that keep those races safe. Each is stated as the problem, the mechanism and what it guarantees. [The storage page](storage.md) maps where the state lives.

## Snapshot per launch

A queued job must run the code that was submitted, but the agent keeps editing its workspace while the job waits. A run's workspace can also be removed after the run ends.

Every launch and every measurement takes a snapshot first. The kernel commits the workspace content as a git tree parented on the base commit, using a private index so the working tree is never touched. A temporary ref keeps the commit reachable, so garbage collection cannot prune it while a job is queued. The job checks the snapshot out into a worktree of its own and runs there, and the ref is dropped once the result has been read.

A job therefore runs exactly the tree that was submitted, whoever edits the workspace in the meantime. Two snapshots taken against the same base at the same time do not collide. A result is read back only from the slot that matches its full identity, which covers the image, the tree, the command, the metric, the GPU lane and the seed variables it was measured under.

## Inbox append and the delivered cursor

Jobs, the kernel, the panel and GitHub all append to one inbox, and a wake must deliver every message exactly once. A wake can be killed mid-session or run twice.

Appends happen under a file lock, and each message carries a dedupe key. Appending a key that already exists returns the stored message unchanged, so a retried job or a repeated sweep keeps the first copy. Each message lands in its own file named by a sequence number. The run record holds a delivered cursor, the sequence number of the last message a completed session leg saw. A wake delivers only files past that cursor, and delivery stops at a damaged entry, so the cursor can never move past a message the session did not see.

Every message is therefore delivered once, in order. A wake killed mid-session delivers its messages again, because the cursor only advances after a completed leg. The session tolerates the repetition, so no message is lost to a crash.

## Ledger compare-and-swap

Every run in the fleet writes to the one `research-log` branch, and two writes landing close together must not lose each other's changes.

A ledger write reads the branch head, applies a pure patch to what it read, and commits with the head it expects. If the head moved in the meantime, GitHub refuses the commit. The writer re-reads, recomputes the patch against the fresh state and tries again, up to three times. A write that still fails is kept as a pending intent on the run and retried by a later tick.

No update is lost. Every ledger edit is applied against a consistent read of the branch, and a slow writer never overwrites a faster one's result.

## Run leases

Both a wake job armed at park time and the tick's sweep can try to wake the same run, and a wake holder can die mid-session.

Whoever wakes a run first creates `lease.json` atomically, an exclusive create that lets exactly one contender win. The lease records its holder and a timestamp. A lease is stale when the holder's job is no longer alive per the scheduler, or when its age passes a TTL. The sweep reaps a stale lease by renaming it to a tombstone and checking it got the lease it observed, so a faster reaper cannot delete a fresh lease.

One wake delivers, and the others stand down. A wake killed mid-session delays the retry by one grace window. It cannot strand the run, because the sweep reaps the dead holder's lease.

## The tick lease

Two kernel loops on the same state root, for example a resident tick and a foreground loop started by hand, would act on every run twice.

The loop takes a file lock on a `TICK` file and heartbeats its holder name and TTL into it. The file is never unlinked, so every contender locks the same inode. A holder on the same host is proof of death once its lock is free, and from another host the last heartbeat must be older than the TTL. A fresh acquisition reads the record back once and yields if another holder's stands.

One kernel acts at a time per state root. A dead tick's lease is taken over promptly, on the same host at once and from another host after the TTL.
