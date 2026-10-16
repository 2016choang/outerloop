# The Outerloop reviewer

The reviewer reads a pull request and posts its findings as a PR review. It
is advisory: it never approves, never blocks a merge, and never fails your
build. This page covers adding it to a repository, steering its rounds, and
the optional weekly maintenance digest that uses the same machinery.

## Review scope

The reviewer posts on pull requests opened from branches in your own
repository. PRs from forks are skipped, because they must not reach your
API key.

Nothing from a pull request is ever executed. The workflow checks out the PR
head read-only and the reviewer reads it as data, alongside the diff. Files
a model backend would auto-load as instructions, such as `CLAUDE.md` or
`.claude/`, are renamed before the session starts, so PR content can never
become instructions.

Each finding names a file and a line, states its evidence, and carries a
confidence level and a kind: `change`, `suggestion`, `question` or `note`.
Approval-like language in findings is redacted, so a crafted PR cannot elicit
an endorsement. The code owner decides what happens next.

## Adding the reviewer

You need an API key for the reviewer's model, and about five minutes.

1. Pick the bot identity. `bot_login` names the account whose PRs are never
   reviewed, so the reviewer never comments on its own work. If you have no
   bot yet, any placeholder login works. If you run the climber with a GitHub
   App, use the App's login.

2. Add this file to the repository as `.github/workflows/review.yml`:

```yaml
name: advisory-review
on:
  pull_request_target:
    types: [opened, reopened, labeled]
permissions:
  contents: read
  pull-requests: write
jobs:
  advisory:
    # a labeled event only runs for the outerloop:review label (re-review)
    if: github.event.action != 'labeled' || github.event.label.name == 'outerloop:review'
    uses: outerloop-science/outerloop/.github/workflows/advisory-review-agent.yml@v0
    with:
      reviewer_ref: v0             # keep on the same ref as the line above
      bot_login: my-bot            # PRs by this login are never reviewed
    secrets:
      anthropic_reviewer_key: ${{ secrets.ANTHROPIC_REVIEWER_KEY }}
```

3. Add the key as a repository secret. Under
   `Settings → Secrets and variables → Actions`, create
   `ANTHROPIC_REVIEWER_KEY` with your Anthropic key. A spend-capped key is
   strongly recommended.

Open a pull request and a review appears within a minute or two. The same
file ships as [`examples/review.yml`](https://github.com/outerloop-science/outerloop/blob/main/examples/review.yml).

## Re-review and opt-out

The first round runs when a PR opens. Apply the `outerloop:review` label to
run another round after you push fixes. The round stamp increments with each
review, so you can follow the fix-review-fix loop. The `outerloop:no-review`
label opts a PR out entirely.

## Backends and second opinions

`backend: claude` is the default. Two other backends work with the same
workflow and their own key:

- `backend: hermes` uses `hermes_provider: openrouter` by default with the
  `OPENROUTER_API_KEY` secret and an OpenRouter model id such as
  `openai/gpt-5.6-terra`. Add `hermes_provider: openai` to run directly
  against the OpenAI API with the `OPENAI_REVIEWER_KEY` secret and a
  provider-native id such as `gpt-5.6-terra`.
- `backend: codex` uses the `OPENAI_REVIEWER_KEY` secret and a Codex model
  id.

Every input of the reusable workflow is documented in
[`advisory-review-agent.yml`](https://github.com/outerloop-science/outerloop/blob/main/.github/workflows/advisory-review-agent.yml).

Add a second job to the same file for an independent opinion. Each opinion
posts its own labelled round:

```yaml
  second-opinion:
    if: github.event.action != 'labeled' || github.event.label.name == 'outerloop:review'
    uses: outerloop-science/outerloop/.github/workflows/advisory-review-agent.yml@v0
    with:
      reviewer_ref: v0
      bot_login: my-bot
      backend: hermes
      model: openai/gpt-5.6-terra
      opinion_label: second opinion — terra
    secrets:
      openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

A third opinion is one more block with a distinct `opinion_id`. If a
backend's key is missing or expires, every triggering PR gets a visible
could-not-run stub naming that opinion, so a dead key is never silent.

If you forked the Outerloop repository, set `reviewer_repo` to your fork and
point `uses:` and `reviewer_ref` at a ref your fork has.

## Following releases

Maintainers move the `v0` tag to each final release, never a pre-release or
a push to `main`. Keep `reviewer_ref` on the same tag as the `uses:` ref, so
the code that holds your reviewer key changes only when a release is cut.

A tag is a convention, not a lock: it trusts whoever can push tags to the
Outerloop repository. For code that cannot change under you, pin a full
commit SHA or an exact release such as `v0.3.0` in both places, and bump it
by hand.

## Missing reviews

Open the workflow run and read the log. The reviewer logs why it stopped,
for example a missing key, a skipped PR or a model refusal, and always exits
successfully so your PR stays green. A skipped round on a bot PR or an
opted-out PR is normal silence; a could-not-run stub on the PR names the
opinion that failed.

## The weekly maintenance digest

The same machinery can scan your whole repository once a week: dead code,
duplicated logic, oversized modules, stale pins, slow tests, repeated work on
the hot path, and documentation drift. It writes one issue titled
"Maintainer digest — <date>" and replaces its body on each scan. It changes
no code and opens no work orders.

Add `.github/workflows/maintenance.yml` with the same secret as the
reviewer:

```yaml
name: maintenance
on:
  schedule:
    - cron: '17 6 * * 1'   # weekly; or run it from the Actions tab
  workflow_dispatch:
permissions:
  contents: read
  issues: write
concurrency: maintenance
jobs:
  digest:
    uses: outerloop-science/outerloop/.github/workflows/maintenance-agent.yml@v0
    with:
      reviewer_ref: v0
    secrets:
      anthropic_reviewer_key: ${{ secrets.ANTHROPIC_REVIEWER_KEY }}
```

The `backend`, `model` and `hermes_provider` inputs select the model as they
do for the reviewer, and `lenses` picks which sections run. Every run scans
the default branch's head, whatever ref a manual run was started from.
Items marked **Decision** need your call; the rest are mechanical and can be
given to an agent as work orders.

## Safety model

Every review runs through a least-token split, whatever the backend:

- The session job holds read-only permissions. Only the chosen backend's
  model key enters it, so even a backend that can read other processes finds
  nothing worth stealing.
- A separate posting job holds the write token and runs no model. It
  re-validates the findings and renders them with sanitization.
- Both jobs treat PR content as untrusted data, never as instructions, and
  neither executes the PR's code.

A crafted PR can at most elicit a misleading advisory comment, which you
read with the same skepticism as any review.
