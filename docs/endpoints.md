# Self-hosted model endpoints

Outerloop can run its authors, panel judges and the standalone reviewer
against models you host yourself, such as a vLLM server or an internal
gateway. This page covers defining an endpoint profile in the operator
environment, selecting a profile for each role, and what happens when a
server's address changes. It assumes the [install guide](install.md) setup.

## An endpoint profile

A profile is a name plus five settings in the operator `.env`. Put
coordinates and a key-file path in the file, never a bearer key:

```dotenv
OUTERLOOP_ENDPOINT_AUTHOR_URL=https://llm.example.internal/v1
OUTERLOOP_ENDPOINT_AUTHOR_KEY_FILE=/keys/model-author
OUTERLOOP_ENDPOINT_AUTHOR_MODEL=open-model
OUTERLOOP_ENDPOINT_AUTHOR_API=chat
OUTERLOOP_ENDPOINT_JUDGE_URL=https://llm.example.internal/v1
OUTERLOOP_ENDPOINT_JUDGE_KEY_FILE=/keys/model-judge
OUTERLOOP_ENDPOINT_JUDGE_MODEL=open-model
OUTERLOOP_ENDPOINT_JUDGE_API=chat,anthropic

OUTERLOOP_AUTHOR_BACKEND=hermes
OUTERLOOP_AUTHOR_ENDPOINT=author
OUTERLOOP_PANEL=verify:hermes:[endpoint=judge],review:claude:open-model[endpoint=judge]
```

For each profile, `OUTERLOOP_ENDPOINT_<NAME>_<KEY>` takes these values:

- `URL` or `URL_FILE`: the server's base address. Exactly one of the two is
  required. `URL_FILE` is for servers whose address changes; see
  [addresses that change](#server-addresses-that-change).
- `KEY_FILE`: the path to a file holding the API key. The path must be
  absolute, and the file readable, nonempty and private (`chmod 600`).
- `MODEL`: the model the server actually serves.
- `API`: a comma-separated list of the APIs the server speaks.

Profile names start with a letter and contain only letters, digits and
underscores. References are case-insensitive and the environment keys are
uppercase. A profile owns its model: any model you select through the
profile must match the served `MODEL`, and Outerloop refuses a mismatch
before a session starts.

The key is read by the orchestrator and passed to the session through its
environment. It never appears in command lines or in generated provider
configuration.

## The API list

`API` declares which wire protocols the server speaks, out of `anthropic`,
`responses` and `chat`. The `claude` backend needs `anthropic`, `codex`
needs `responses` (or `chat` through the bridge below), and `hermes` needs
`chat`.

Use the base URL accepted by that backend's client. One server can expose
several APIs under different prefixes, in which case define one profile per
prefix: an Anthropic-compatible client may need
`https://llm.example.internal` while OpenAI-compatible clients need
`https://llm.example.internal/v1`.

## Codex on a chat endpoint

A Codex author or judge may select a profile whose `API` lists only `chat`.
Install the bridge first, which pins a translation proxy alongside the
session:

```bash
outerloop harness upgrade --used
```

A profile that also lists `responses` uses the direct endpoint instead. The
bridge supports text, reasoning, function tools and freeform tools such as
`apply_patch`. It rejects hosted tools, images, WebSockets, server-side
response references and remote compaction.

## Selecting a profile for each role

Each role selects its own profile and credential.

- The author runs on a backend plus profile:
  `OUTERLOOP_AUTHOR_BACKEND=hermes` and `OUTERLOOP_AUTHOR_ENDPOINT=author`.
- Panel judges are configured per lens in `OUTERLOOP_PANEL`. An entry is
  `kind:backend:model[endpoint=profile]` where kind is `verify` or `review`,
  and the model may be omitted to take the profile's served model. A lens
  that names no model and no profile runs the author's model when it runs
  the author's backend, and a lens on any other backend must name its model.
- The standalone reviewer uses `REVIEW_BACKEND`, `REVIEW_ENDPOINT` and
  optionally `REVIEW_MODEL`.

Author and judge credentials must differ, including when two files contain
the same key. Judges may share one judge profile.

## Server addresses that change

Set `OUTERLOOP_ENDPOINT_<NAME>_URL_FILE` to the absolute path of a file the
address publisher rewrites by atomic rename. The file contains either a bare
URL or a JSON record:

```json
{"url": "http://localhost:8000/v1"}
```

The record may also carry `model`, which must match the profile, and
`expires_at`, a Unix time in seconds that must still be in the future. A
mismatched model, an expired or invalid expiry, or a record over 64 KiB
makes the endpoint unavailable. A malformed file is a configuration error.

The kernel reads the file outside the session sandbox each time a session
starts, including resumes and wakes, so publishing a new address reroutes
the next session without a restart. Each read resolves the address and, for
a `URL_FILE` profile, one models-list request checks the server with a
three-second timeout and the profile key.

## Outages

A profile with a `URL_FILE` is probed before every session: a missing address
file, a bad record or a dead server parks fresh runs and defers parked wakes
without spending wake retries, and nothing polls in the background. A fixed
`URL` profile is not probed, so a dead fixed server surfaces as ordinary
session failures instead.
Run `outerloop status` to see which endpoint each run waits for and since
when:

```text
my-run target=owner/repo ... waiting for endpoint judge since 2026-10-16T09:00:00+00:00
```

A successful session probe ends the outage for that run. Other waiting runs
keep their own wait times until they resume. Endpoint waits never appear on
the GitHub board.

## Changing a profile

A run persists the profile it started with, so it does not follow a later
change to `OUTERLOOP_AUTHOR_ENDPOINT`. Keep the profiles old runs reference
until they finish. Changing a profile's URL or key reroutes everything that
selects it, while changing its served model makes old runs fail validation
rather than silently switch models.
