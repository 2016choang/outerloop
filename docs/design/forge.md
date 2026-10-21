# A forge interface with a local backend

Status: proposed for the release after 0.4. Tracking issue: #488. A
personal-token mode for GitHub follows on top of it (end of this note).

## Problem

The kernel reaches GitHub in three roles that share one client today:

- **Code transport**: clone, fetch and push of line branches, candidate
  branches and the base, through `Workspace` with a credential scoped to
  `https://github.com/`.
- **Review surface**: pull requests (open, draft, body edits, ready for
  review, auto-merge, merge, close), thread comments, reviews, check runs.
- **Intake and record**: issues as work orders, claim markers, the
  research-log branch (ledger, reports, board) written through the git-data
  API, and pointer comments on the research-log issue.

All of it goes through `GitHubClient` (`github.py`), which about a dozen
modules call. Two costs follow. A measured result depends on GitHub's
availability and rate limits; outages have already aborted runs. And every
deployment needs a hosted repository and GitHub access through an App or a
machine-user token, which some labs cannot or will not set up. Adopters
report the App setup as the main friction of getting started.

## Design

### One interface, two backends

A `Forge` Protocol, selected the way `Compute` is (`compute.py`):
`OUTERLOOP_FORGE=github` (the default) or `local`. `GitHubForge` wraps the
existing client and changes no behavior. `LocalForge` implements the same
verbs over a bare git repository and JSON records on the deployment's
shared filesystem. `OUTERLOOP_FORGE` joins the keys `start` exports and the
deploy step carries into every tick.

One factory builds the forge at every entry point (the tick, attempt and
steward jobs, and the CLI) and supplies three things callers take from the
client or the environment today:

- **Workspaces.** Clone URL and credential come from the forge. This
  replaces `target_clone_url` (`attempt.py`) everywhere it is used: run
  wakes, publish, `finish_run`, the tick's mid-leg sync and the steward.
- **Kernel identity.** `bot_login` stays load-bearing: commits take their
  author from it, and intake and the steward refuse to run without it. On
  GitHub it is the App or bot login, as today; locally the factory supplies
  `outerloop`.
- **Secrets and dry run.** `forge.secrets()` feeds redaction, replacing the
  direct `github.auth.token()` read in the tick; `forge.dry_run` replaces the
  direct attribute read in the ledger.

Startup gates become backend-aware. Servicing needs a home, a target and an
image on every backend; the credential and bot-login gate applies to
`github` only.

The kernel's verbs, grouped by role:

| Role | Verbs |
|---|---|
| Transport | clone URL and credential for a `Workspace` |
| Refs and files | `default_branch`, `branch_head`, `branch_sha`, `head_contains`, `commit_tree`, `get_tree`, `get_file`, `get_file_content`, `list_directory`, `create_ref`, `ensure_branch`, `put_file`, `put_files` |
| Pull requests | `create_pull`, `get_pull_request`, `find_open_pull_for_head`, `update_candidate_row`, `append_pull_body`, `mark_ready_for_review`, `arm_auto_merge_when_review_required`, `disable_auto_merge`, `allowed_merge_methods`, `merge_pull`, `close_issue` |
| Threads | `list_comments`, `comment`, `list_pr_reviews`, `list_pr_review_comments` |
| Issues | `list_open_issues`, `create_issue` (with labels) |
| Checks | `list_check_runs` |
| Identity | `is_kernel_post`, `qualifies` (below) |
| References | `ref_for(kind, id)`: a stable reference and an optional link |

`get_pull_request_diff`, `create_pr_review`, `update_issue` and
`job_log_tail` serve only the GitHub Actions posting CLIs (or are reached
only through a GitHub check run), so they stay on the GitHub client and out
of the Protocol. Four client methods have no caller (`create_pr`,
`get_issue`, `get_pull_request_files`, `upsert_comment`) and are deleted.

### Shapes and errors

Return values keep the shapes callers read today, so the ledger observer,
bless and merge checks, intake and the steward read local records unchanged:

- **Pull request**: `number`, `state`, `merged`, `merged_at`,
  `merge_commit_sha`, `draft`, `mergeable_state`, `user.login`, `html_url`,
  `labels`, and nested `head.sha`, `head.ref`, `base.ref`. `head.sha` and the
  base tip are read from the live refs on every call, so a follow-up push is
  seen at once.
- **Issue**: `number`, `title`, `body`, `user.login`, `author_association`,
  `labels[].name`, `created_at`.
- **Comment**: an increasing integer `id`, `user.login`, `body`,
  `author_association`, and an ISO `created_at` (the steward releases stale
  claims by age).
- **Reads at a ref** follow the real client: a branch or a sha; `get_file`
  raises on a missing file and `get_file_content` returns `None`; regular
  files only; `get_tree` reports `truncated` false; `branch_head` returns
  `""` for a missing branch and `None` on failure; `put_file` and `put_files`
  return what the client returns.
- **Errors**: a missing branch, file or record raises the client's error
  with `status` 404. Several callers read a 404 as "absent" (the ledger, the
  board, `close_if_done`, base-moved checks); any other error reads as an
  outage and stalls them.

Contract reads use `default_branch` instead of the literal `main` the tick
passes in two places today, so a local repository with another default
branch still loads its contract. User-visible text that names GitHub (the
inbox's member label, its conflict line, the self-merge status comment)
takes the forge's name instead.

### The local backend

- **Bootstrap.** `outerloop init` creates the bare repository at
  `$OUTERLOOP_ROOT/forge/<owner>/<repo>.git` with `git clone --bare` from an
  existing repository (a path or URL), or `git init --bare` with a first
  commit of a given directory. Its default branch is recorded.
- **Records.** `pulls/<n>.json`, `issues/<n>.json` and
  `threads/<n>/<id>.json` beside the repository. Issues and pull requests
  share one number sequence, as on GitHub, and `close_issue` closes either
  kind. One lock covers number allocation and every read-modify-write of a
  record. Branch writes keep git's own compare-and-swap, as the ledger and
  board need for concurrent writers.
- **Transport.** A path URL with no credential (`Workspace` already accepts
  `auth=None`). Filesystem permissions are the access control. The bare
  repository sits outside every path a session can see. Every clone a
  session can see uses `--no-local` with no alternates, so it shares no
  object files with the bare repository; today only the single-branch clone
  passes `--no-local`, so the default `Workspace.clone` and the channel
  bootstrap clone change too. A contained session cannot reach the state
  root; an uncontained one can, which the install guide's uncontained
  trade-offs will say.
- **Line snapshots.** `_push_line_snapshot` returns early when a workspace
  has no credential. It changes to ask whether the forge has a transport,
  and the forge reaches it through `finish_run` as well as the run legs, so
  local snapshots are pushed on every path.
- **Pull requests.** Opening one pushes the head branch first, as on GitHub.
  `mergeable_state` is computed when read and cached by the head and base
  shas: `clean` when the head contains the base tip, `behind` when it does
  not but a trial merge succeeds, and `dirty` only when the trial merge
  conflicts, which is what the inbox treats as a conflict.
- **Merging.** `allowed_merge_methods` returns `["MERGE"]`. `merge_pull`
  builds a merge commit from the expected head sha and moves the base in one
  git ref transaction (`git update-ref --stdin`): verify the head branch
  still points at the expected sha, then update the base from its old tip.
  Either both checks pass and the base moves, or nothing changes. The record
  is written after the ref moves. A process that dies in between leaves the
  base containing the head; the next read of that record finds the merge
  commit on the base whose second parent is the head and marks the record
  merged, under the record lock.
- **Auto-merge.** Nothing is armed locally:
  `arm_auto_merge_when_review_required` returns `False` and
  `disable_auto_merge` reports disarmed. Under the auto dial the kernel
  merges a blessed head through `merge_pull` with the same bless rules as on
  GitHub. There are no required checks, so the stale-base guard is the
  head-contains-tip test.
- **Checks and reviews.** `list_check_runs`, `list_pr_reviews` and
  `list_pr_review_comments` return nothing. The inbox reads them to pass
  CI results and Actions review rounds to the author; locally there are
  none.

### People: review and merge from the CLI (open question 1)

A small `outerloop pr` group: `list`, `show N`, `comment N`, `merge N` and
`close N`, plus `outerloop issue new [--label L]` and `outerloop issue
comment N`. The labels are the kernel's own: `outerloop:steward` routes an
issue to the steward lane and `outerloop:task` vouches for a work order.
These commands write the same records the kernel reads, so a person's
comment reaches a run's inbox and an issue becomes a work order as on
GitHub. Diffs come from `git diff` against the bare repository. No web view
in the first version.

### Ledger, board, research log (open question 2)

The ledger, reports and board go through refs-and-files verbs and work
unchanged. Research-log publication also creates and comments on the
research-log issue; locally those become records like any issue. Links are
hard-coded to `github.com` today in the tick (the research-log pointer and
the rolling issue body), `progress.py`, `ledger_branch.py` and
`maintain.py`; they move to `ref_for`, so they point at the forge in use.

### Identity, and who merges (open question 3)

`is_own_login(author, bot_login)` becomes `forge.is_kernel_post(item)` at
the call sites that judge an issue, comment or post:

- **GitHub with an App or bot account**: the login check, aliases included,
  as today.
- **Local**: records the kernel writes carry the author `outerloop`; the CLI
  writes the operator's user name.
- **GitHub with a personal token** (follow-up): the kernel and the person
  share one login, so a post is the kernel's when it comes from that login
  and carries an `outerloop:` marker. Every kernel post gets a marker for
  this; most already have one.

Two call sites judge a pull request's author rather than a post (the
reviewer's and the verifier's skip rules). They run in GitHub Actions only,
and read `user.login` from the pull request.

One ownership check has neither: `_observe_auto_pr` disarms auto-merge only
when the kernel armed it, read from `auto_merge.enabled_by.login`. With a
shared login that cannot tell the kernel from the person, so the follow-up
records the arming itself (pull request number and head) in the run's stage
when the kernel arms, and compares against that.

`author_association` checks become `forge.qualifies(item)`: author standing
only. Locally every person-written record qualifies, since only people with
write access to the forge directory can write one. Each lane keeps its own
rules on top: intake also accepts its vouching label, the steward lane
requires author standing, and the verifier still trusts kernel replies and
marked verifier posts.

Who merges is unchanged on every backend. Under the manual dial the kernel
never calls `merge_pull`; on GitHub it only arms auto-merge behind a
required human review, and locally it arms nothing. Under the auto dial the
kernel merges blessed solver heads, and steward pull requests stay a
person's to merge. Reviews are comments only.

Locally none of this is enforced by permission. The kernel and the people
share the filesystem, so anyone who can write the forge directory can write
a record with the author `outerloop`, including a claim marker that GitHub's
bot identity makes unforgeable. That is the trust the operator already gives
everyone with write access to the state root.

### Migration (open question 4)

One backend per deployment, chosen at `outerloop init`. Moving between
backends is not supported in the first version. Branches can be pushed to
another remote by hand; pull-request and issue records do not migrate.

## What stays GitHub-only

The advisory review and verifier workflows run in GitHub Actions on
`pull_request_target`, and the maintenance digest posts issues there. On
the local backend they do not run. The kernel's own review panel and gate
run inside the kernel and are unaffected.

## Build order

Each step is one PR with its tests:

1. The `Forge` Protocol, `GitHubForge` around the existing client, the
   factory at every entry point (workspaces, kernel identity, secrets, dry
   run), `OUTERLOOP_FORGE` in the deploy and start key lists, callers typed
   to `Forge`, contract reads at `default_branch`, the four dead methods
   removed. No behavior change.
2. `LocalForge` bootstrap, refs, files and transport, with `--no-local`
   session clones, transport-gated snapshots and the 404 error contract; the
   ledger, research log and board on a bare repository; links through
   `ref_for`.
3. `LocalForge` pull requests, threads and issues, the merge transaction and
   its recovery, and the `outerloop pr` and `outerloop issue` commands.
4. The identity seam (`is_kernel_post`, `qualifies`) on both backends, and
   the forge name in user-visible text.
5. `outerloop init` offers the local backend; docs.

Then the personal-token mode: `init` offers "use my GitHub login" and writes
`gh auth token` to the PAT file, the marker rule from step 4 applies,
auto-merge arming is recorded in the run's stage, and PR bodies and commit
trailers name Outerloop as the author. Its docs state the trade-offs: the
person cannot approve their own agents' pull requests, so a required-review
rule needs a second reviewer; the kernel's restraint about merging holds by
its code, not by permission; and a fine-grained token limited to the target
repository is safer than the `gh` login token.

## Not in the first version

A web view, hosted alternatives such as Gitea or GitLab (later backends of
the same interface), migration between backends, CI checks, inline reviews
and job logs on the local backend, and the Actions-based workflows on it.
