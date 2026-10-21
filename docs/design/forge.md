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
shared filesystem.

One factory builds the forge at every entry point: the tick, attempt and
steward jobs, and the CLI. It replaces today's direct credential handling
there. Startup checks become backend-aware: the tick currently disables
servicing without a PAT or App and a bot login, which stays true for
`github` and does not apply to `local`. The factory also builds each
`Workspace`: clone URL and credential from the forge.

The verbs are the ones the kernel calls now, grouped by role:

| Role | Verbs |
|---|---|
| Transport | clone URL and credential for a `Workspace` |
| Refs and files | `default_branch`, `branch_head`, `branch_sha`, `head_contains`, `commit_tree`, `get_tree`, `get_file`, `get_file_content`, `list_directory`, `create_ref`, `ensure_branch`, `put_file`, `put_files` |
| Pull requests | `create_pull`, `get_pull_request`, `find_open_pull_for_head`, `update_candidate_row`, `append_pull_body`, `mark_ready_for_review`, `arm_auto_merge_when_review_required`, `disable_auto_merge`, `allowed_merge_methods`, `merge_pull`, `get_pull_request_diff`, `close_issue` |
| Threads | `list_comments`, `comment`, `list_pr_reviews`, `list_pr_review_comments`, `create_pr_review` |
| Issues | `list_open_issues`, `create_issue`, `update_issue` |
| Checks | `list_check_runs`, `job_log_tail` |
| Identity | `is_kernel_post`, `qualifies` (below) |
| References | `ref_for(kind, id)`: a stable reference for records and reports, and an optional clickable link |

Return values keep the shapes callers read today. A pull request is a
mapping with `number`, `state`, `merged`, `merged_at`, `merge_commit_sha`,
`draft`, `mergeable_state`, nested `head.sha`, `head.ref` and `base.ref`,
`html_url`, and `labels`, so the ledger observer, bless and merge checks and
publish read local records unchanged. Reads at a ref follow the real
client's semantics: a branch or a sha, `None` for a missing file in
`get_file_content`, regular files only, and the same compare-and-swap result
for `put_files`.

Four client methods have no caller (`create_pr`, `get_issue`,
`get_pull_request_files`, `upsert_comment`) and are deleted rather than
carried into the interface.

### The local backend

- **Storage.** A bare repository at
  `$OUTERLOOP_ROOT/forge/<owner>/<repo>.git`, and records beside it:
  `pulls/<n>.json`, `issues/<n>.json` and `threads/<n>/<id>.json`. Issues and
  pull requests share one number sequence, as on GitHub. One lock covers
  number allocation and every read-modify-write of a record; comment ids are
  increasing integers, since the inbox cursor and claim markers depend on
  their order. Branch writes keep git's own compare-and-swap, as the ledger
  and board need for concurrent writers.
- **Transport.** A path URL with no credential (`Workspace` already accepts
  `auth=None`). Filesystem permissions are the access control. The bare
  repository sits outside every path a session can see, and every clone a
  session can see uses `--no-local` with no alternates, so it shares no
  object files with the bare repository. A contained session cannot reach
  the state root; an uncontained one can, which the install guide's
  uncontained trade-offs will say.
- **Line snapshots.** `_push_line_snapshot` returns early when a workspace
  has no credential. It changes to ask whether the forge has a transport,
  so local snapshots are pushed.
- **Pull requests.** Opening one pushes the head branch first, as on GitHub.
  `mergeable_state` comes from git: `clean` when the head contains the base
  tip, `behind` when it does not but merges without conflict, and `dirty`
  only when a trial merge conflicts, which is what the inbox treats as a
  conflict. `html_url` holds the local reference; numbers parse as before.
- **Merging.** A merge commit into the base, with `git update-ref` given the
  old base tip, after checking that the live head ref still equals the
  expected head sha. The record is written after the ref moves. A process
  that dies between the two leaves the base containing the head, and the
  next read of that record marks it merged from git, so the state heals
  instead of drifting.
- **Auto-merge.** Nothing is armed locally:
  `arm_auto_merge_when_review_required` returns `False` and
  `disable_auto_merge` reports disarmed. Under the auto dial the kernel
  merges a blessed head through `merge_pull` with the same bless rules as on
  GitHub. There are no required checks, so the stale-base guard is the
  head-contains-tip test.
- **Checks and reviews.** `list_check_runs`, `list_pr_reviews` and
  `list_pr_review_comments` return nothing, and `job_log_tail` is unused.
  They serve the GitHub Actions workflows, which do not run locally.

### People: review and merge from the CLI (open question 1)

A small `outerloop pr` group: `list`, `show N`, `diff N`, `comment N`,
`merge N` and `close N`, plus `outerloop issue new [--label L]` and
`outerloop issue comment N`. Labels matter because the steward lane only
takes issues carrying its label. These commands write the same records the
kernel reads, so a person's comment reaches a run's inbox and an issue
becomes a work order as on GitHub. No web view in the first version.

### Ledger, board, research log (open question 2)

The ledger, reports and board go through refs-and-files verbs and work
unchanged. Research-log publication also creates and comments on the
research-log issue; locally those become records like any issue. Links in
reports come from `ref_for`, so they point at the forge in use. The
steward's clone URL, hard-coded to GitHub today, comes from the forge too.

### Identity, and who merges (open question 3)

`is_own_login(author, bot_login)` becomes `forge.is_kernel_post(item)`:

- **GitHub with an App or bot account**: the login check, aliases included,
  as today.
- **Local**: records the kernel writes carry the author `outerloop`; the CLI
  writes the operator's user name.
- **GitHub with a personal token** (follow-up): the kernel and the person
  share one login, so a post is the kernel's when it comes from that login
  and carries an `outerloop:` marker. Every kernel post gets a marker for
  this; most already have one.

One ownership check has no post: `_observe_auto_pr` disarms auto-merge only
when the kernel armed it, read from `auto_merge.enabled_by.login`. With a
shared login that cannot tell the kernel from the person, so the follow-up
reads it from the run record instead (the kernel already records the head
it blessed).

`author_association` checks become `forge.qualifies(item)`: author standing
only. Locally every person-written record qualifies, since only people with
write access to the forge directory can write one. Each lane keeps its own
rules on top: intake also accepts its vouching label, the steward lane
requires author standing, and the verifier still trusts kernel replies and
marked verifier posts.

Who merges is unchanged on every backend. Under the manual dial the kernel
never merges; on GitHub it only arms auto-merge behind a required human
review, and locally it arms nothing. Under the auto dial the kernel merges
blessed solver heads, and steward pull requests stay a person's to merge.
Reviews are comments only. Locally none of this is enforced by permission,
because the kernel and the person share the filesystem; that is the trust
the operator already gives the kernel over its state root.

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
   factory at every entry point, callers typed to `Forge`, the four dead
   methods removed. No behavior change.
2. `LocalForge` refs, files and transport, with `--no-local` session clones
   and transport-gated snapshots; the ledger, research log and board on a
   bare repository.
3. `LocalForge` pull requests, threads and issues, the merge and recovery
   path, and the `outerloop pr` and `outerloop issue` commands.
4. The identity seam (`is_kernel_post`, `qualifies`) on both backends.
5. `outerloop init` offers the local backend; docs.

Then the personal-token mode: `init` offers "use my GitHub login" and writes
`gh auth token` to the PAT file, the marker rule from step 4 applies,
auto-merge ownership comes from the run record, and PR bodies and commit
trailers name Outerloop as the author. Its docs state the trade-offs: the
person cannot approve their own agents' pull requests, so a required-review
rule needs a second reviewer; the kernel's restraint about merging holds by
its code, not by permission; and a fine-grained token limited to the target
repository is safer than the `gh` login token.

## Not in the first version

A web view, hosted alternatives such as Gitea or GitLab (later backends of
the same interface), migration between backends, CI checks, inline reviews
and job logs on the local backend, and the Actions-based workflows on it.
