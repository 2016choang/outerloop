# Personal-token mode

Status: accepted 2026-10-22, decisions below. Roadmap item "Personal-token mode". It builds the
identity seam that [forge.md](forge.md) (#488) named and left for this
follow-up.

## Need

An adopter connects the kernel to GitHub through a GitHub App
(`outerloop init --github-app`, [github-app-auth.md](github-app-auth.md)) or
through a bot account's PAT (`OUTERLOOP_PAT_FILE`). Adopters report the App
setup as the main friction of getting started. A bot account is worse for a
single researcher, since it means a second GitHub account.

Personal-token mode lets the adopter use their own token, for example the
one `gh auth token` prints. The kernel then posts, opens pull requests and
pushes as the person, so `OUTERLOOP_BOT_LOGIN` is the person's login.

Half of this works by accident today. `init` accepts any PAT and records the
login it acts as (`init.py:1247`). A person who pastes their own token gets
a deployment that looks healthy and silently ignores them. The kernel tells
its own posts from people's by `is_own_login(login, bot_login)`
(`github.py:77`), and with a shared login every post the person writes reads
as the kernel's.

### Where the login decides today

| Call site | What it decides | With the person's login | Marker on the item today |
|---|---|---|---|
| `intake.py:68` (`disqualification`) | an issue by the kernel is never a work order | every issue the person opens is skipped as "the kernel's own issue" | yes on the kernel's issues (`contract-alarm`, `research-log`), none on the person's |
| `steward.py:215` (via `qualifying_issue`) | same, for the steward lane | every steward order the person files is skipped | same |
| `intake.py:111`, `steward.py:232`, `steward.py:300` | only the kernel's comments move claim state | still works; the person's own pasted claim markers now count too | yes (`claimed`, `claim-released`, `outage-release`) |
| `inbox.py:676` (`qualifying_comments`) | a PR comment by the kernel never reaches the author's inbox | every comment the person writes on a kernel PR is dropped, so the person cannot steer a run | mostly (see below) |
| `tick.py:547` (`_find_alarm_issue`) | adopt only the kernel's open alarm issue | works (login and marker both checked) | yes |
| `tick.py:779` (self-merge status) | dedupe the kernel's last status comment | works (login and marker both checked) | yes |
| `tick.py:611` (`_observe_auto_pr`) | disarm auto-merge only when the kernel armed it, from `auto_merge.enabled_by.login` | the kernel also disarms auto-merge the person armed by hand | no marker possible; it reads who armed |
| `review.py:174` (`skip_reason`, Actions) | the advisory reviewer skips the kernel's PRs | it skips every PR the person opens by hand | no marker in PR bodies (`orchestrator.py:2658`, `steward.py:608`) |
| `verifier.py:231` (`verify_skip_reason`, Actions) | the verifier covers only the kernel's PRs | it verifies every PR the person opens by hand | same |
| `verifier.py:82` (`_standing`) | kernel replies have standing in the verifier's thread | unaffected; the person qualifies by association anyway | n/a |

The inbox drop is the one that defeats the mode. The intake skip is close
behind: the person opening an issue for the agents is the main way to ask
for work.

### Kernel posts without a marker

Most kernel posts already carry an `outerloop:` HTML-comment marker
(`markers.py`). These do not:

- On PR threads: the state-record warning (`attempt.py:4693`). With the
  person's login and `OWNER` association it would reach the author's inbox
  as a maintainer instruction.
- PR bodies: `create_pull` in `attempt.py:4529` and `steward.py:608`.
- On issue threads: run reports and ending comments (`attempt.py:650` when
  a PR exists, `attempt.py:4802`, `attempt.py:5316`, `attempt.py:5899`,
  `attempt.py:6237` for merged and plain closed endings), steward reports
  (`steward.py:544`, `steward.py:767`), the alarm-recovered note
  (`tick.py:472`) and research-log lines (`tick.py:1481`, `tick.py:1518`).
  Nothing reads issue comments except the claim scans, which already
  require a claim marker, so these are harmless today. They get a marker so
  the rule has no exceptions.

## What changes

### One identity rule

`is_own_login` becomes `forge.is_kernel_post(item)` at every call site in
the table except `_observe_auto_pr`. A post, issue or pull request is the
kernel's when its login is one of the kernel's and, in personal mode, its
body carries an `outerloop:` marker. Former logins in
`OUTERLOOP_BOT_ALIASES` are dedicated identities and keep the login check
alone. The local forge supplies the same function over its records.

Strangers' posts fail the login check, so a stranger who pastes a marker
gains nothing. Only the person, or something holding their token, can write
a marked post under their login. The trust model toward everyone else is
unchanged.

### Every kernel post carries a marker

`comment`, `create_issue` and `create_pull` on both forges (`GitHubClient`
and `LocalForge`) prepend `<!-- outerloop:kernel -->` when the body has no
`outerloop:` marker yet. One owner per forge, so a future call site cannot
forget. Kinds that are matched
today (`claimed`, `followup` and the rest) keep their own markers.

### Who armed auto-merge

The kernel records the arming in the run's stage (the PR number) when
`arm_auto_merge_when_review_required` returns true, in both lanes that arm
(`attempt.py:488` and `steward.py:648`), and clears the record once it
disarms or sees auto-merge off. `_observe_auto_pr` disarms only an arm the
stage claims. A stage without the record keeps the `enabled_by.login` read
outside personal mode, so runs armed before the upgrade still work. The
record is written after GitHub accepts the arm, so a crash in between
leaves an arm the kernel does not know; in personal mode it stays until a
follow-up publish (which disarms first) or a human removes it, and it still
needs the required review to merge.

### The mode setting

`OUTERLOOP_IDENTITY=personal` turns on the marker requirement. `init`
writes it, and it joins the keys `start` exports and the deploy step
carries into every tick, as `OUTERLOOP_FORGE` did. It cannot be detected. A
bot account's PAT and a person's PAT look the same to GitHub.

The reusable review and verify workflows take a matching `personal` input
next to `bot_login`, so their PR-author checks apply the marker rule to PR
bodies. This includes the shell gate in `verify-agent.yml`, which decides
`bot_authored` by login and aliases before any Python runs.

### Setup

`outerloop init --personal` runs `gh auth token`, writes the token to the
PAT file (mode 600), records the token's login as `OUTERLOOP_BOT_LOGIN`
and writes `OUTERLOOP_IDENTITY=personal`. It accepts a pasted fine-grained
token instead. It prints the trade-offs from the risks section in a few
lines.

### Tokens and scopes

The App's permissions map to a fine-grained token on the target repository
only: Contents read and write, Pull requests read and write, Issues read and
write, Actions read (job log tails), Checks read (CI results), Metadata
read. Whether a fine-grained token can read check runs is unconfirmed. If check runs cannot be
read, the inbox already logs a warning and the run goes on without CI
results (`inbox.py:844`). Members read is unneeded, since the person's own
token sees their own organization membership.

The `gh` token is an OAuth token with the `repo`, `read:org` and usually
`workflow` scopes. It reaches every repository the person can write and
can push `.github/`. The kernel's forbidden paths and the
no-credential-in-session rule are enforced in code, so the breadth matters
when the token leaks.

## Decisions

1. **The hard rule "the bot never merges and is never a code owner".** In
   personal mode the kernel acts with the person's rights, usually admin
   and code owner. What still holds by code: the client can only post
   COMMENT reviews (`github.py:821`), the manual dial never calls
   `merge_pull` and arms auto-merge only behind a required review
   (`github.py:616`), and GitHub never counts an author's approval, so the
   kernel identity cannot approve its own PRs. What is lost is the backing
   by permission. The identity could merge, and an admin token may bypass
   branch protection unless "Do not allow bypassing the above settings" is
   on. A required review now needs a second person.
   *Decided*: restate the rule for personal mode as "the kernel's
   code never approves, and never merges under the manual dial", say
   plainly in the docs that GitHub no longer enforces it, and keep the
   lab's deployments and this repository on the App, where the rule stays
   true as written. *Not chosen*: treat the rule as requiring a dedicated
   identity and do not ship personal mode.

2. **Scope of the marker rule.** *Decided*: personal mode only.
   With a dedicated login the login is already unforgeable, and kernel PRs
   opened before this change have no body marker, so marker-gating App PRs
   would make the reviewer start reviewing them and the verifier skip
   them. *Not chosen*: one rule in every mode. Simpler to state, with a
   legacy tolerance for open App PRs that the mode avoids.

3. **Default token.** *Decided*: `gh auth token` by default,
   because removing setup steps is the point; `init` prints its scopes and
   how to swap in a fine-grained token on the target repository.
   *Not chosen*: require a fine-grained token. Safer, and it brings back a
   web form with five permissions to tick.

4. **The auto dial in personal mode.** Under `merge: auto` the kernel
   merges blessed heads through `merge_pull`. With an admin token that
   merge may pass required checks that would stop the App. *Decided*:
   allow it, keep the kernel's own bless and stale-base checks, and tell
   adopters to turn on the no-bypass setting if required checks must bind
   the kernel. *Not chosen*: refuse the auto dial in personal mode.

5. **Switching modes on a live deployment.** App or bot to personal works
   with today's mechanism: the old login goes into `OUTERLOOP_BOT_ALIASES`
   and its unmarked posts stay recognized. Personal to App does not. The
   person's login cannot become an alias without hiding their comments, so
   claims posted as the person stop counting and intake could claim those
   issues again. *Decided*: document that switch as "release or
   close claimed issues first" and build nothing for it. *Not chosen*: a
   marker-gated alias list.

## What stays the same

The App and bot-account paths behave as today. `bot_login` stays
load-bearing and blank still fails closed at every gate. Agent sessions
still hold no GitHub credential. The kernel never submits approving
reviews. Branch prefixes, run records, the ledger and the board do not
change. Advisory reviews still post as `github-actions[bot]` from the
workflow token.

## Risks

- **No notifications.** GitHub does not notify a user about their own
  activity. The person gets no email or inbox item when the kernel opens a
  PR, replies or reports on their issue. `outerloop status` and the PR list
  are where they look.
- **Rate limits.** A user token has 5,000 requests an hour, shared with the
  person's other tools, and the secondary limit on content creation is per
  user. The App's installation limit is higher. The client has no
  rate-limit handling, so a 403 reads as an outage and stalls lanes.
- **Dropped human comments.** A comment the person writes that contains a
  raw `outerloop:` marker is treated as the kernel's. GitHub's quote reply
  copies rendered text, which has no HTML comments, so this needs a
  deliberate copy of raw text.
- **Forged claims by the person.** The person's own claim or release
  markers now move claim state. They own the repository, so this is
  accepted.
- **Attribution.** Commits are authored as the person (`git_identity`) and
  count on their contribution graph. The agent id stays in the commit
  trailer, and PR titles keep the `[agent]` and `[steward]` prefixes.
- **Token lifetime.** A copied `gh` token stops working when the person
  revokes it. Organization policy may block OAuth or classic tokens or
  require SSO authorization. The kill switch "suspend the bot account"
  becomes "revoke the token".
- **Branch protection.** The bypass question in decisions 1 and 4 needs a
  check on a scratch repository before docs claim either behavior.

## Compatibility

Markers are state read across kernel versions. An older kernel reading
posts from a newer one sees an extra marker and still skips them by login.
A newer kernel in App or bot mode keeps the login check, so old unmarked
posts are read as before. Personal mode starts with no old posts. Tests
carry one legacy fixture, an unmarked post under an alias login. The
changelog gets an `Upgrading:` line for switching modes (decision 5).

## Build plan

Each step is one PR with its tests.

1. The identity seam. `is_kernel_post` on both forges and at every call
   site in the table; markers stamped in the client; auto-merge arming
   recorded in the stage. No behavior change outside personal mode, which
   nothing enables yet.
2. The mode. `OUTERLOOP_IDENTITY` in the deploy and start keys; the
   `personal` input on the review and verify workflows; `init --personal`
   with `gh auth token` or a pasted fine-grained token.
3. Docs and a live check. Install guide and `operate.md` safety defaults
   (decisions 1, 3 and 4); one deployment on a scratch repository where
   the person opens an issue, comments on the kernel's PR and sees the
   author answer. The branch-protection bypass is checked there too.

## Open questions

- Should the local forge adopt the same rule? `outerloop pr comment`
  refuses when the operator's user name equals the kernel login
  (`cli.py:1093`); with the marker rule that refusal could go.
- Can a fine-grained token read check runs, and does an admin token's
  REST merge bypass required checks and reviews? Both need a scratch-repo
  test.
- Should `init` warn when a plain PAT's login matches the person running
  `gh`, to catch today's silent misconfiguration on the bot-account path?
- Does the maintenance digest's own login check
  (`maintain_post_cli.py:38`) need the rule, or does it stay on the
  workflow token only?
