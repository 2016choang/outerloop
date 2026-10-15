# One model gateway per deployment

Status: draft for review. Tracking issue: #464.

## Problem

Harnesses and model servers do not speak the same API. Codex speaks only the
Responses API; self-hosted servers speak Chat Completions reliably and
Responses only partly (tool calls in particular). The kernel translates
between them, and today it does so in two different ways:

- **Per-session bridge** (`docs/endpoints.md`, "Codex on a chat-completions
  endpoint"): for a profile with `API=chat`, every Codex session starts a
  pinned LiteLLM proxy and a streaming shim inside its own container, on
  loopback, with ephemeral tokens. Only the shim holds the endpoint
  credential. Untranslatable requests are rejected.
- **Direct endpoint**: a profile that advertises `responses` is called
  directly, upstream key passed into the session. A deployment can point
  it at its own translating proxy, which the kernel cannot see or check.

The two paths diverge. When Codex 0.160 added a sub-agent tool and a hosted
web-search tool, the per-session bridge rejected every request ("unsupported
bridge request") while a shared proxy on another deployment let the same
requests through. One harness upgrade, two behaviors; tests covered only one
deployment. The kernel now disables those tools, but an untested translation
path can still differ from the tested one.

## Proposal

Each deployment runs **one model gateway**: a single pinned service that
every author and judge session reaches by URL, whatever the backend.

- **First build scope**: one pinned Responses-to-Chat translation, plus
  passthrough for native routes — one translation path, identical across
  sites. The Anthropic Messages surface is deferred (Claude Code stays
  direct; open question 2), as is anything else not needed for that path.
- **Faithfulness rule**: the request check the bridge applies today becomes
  a gateway rule. A request with a part the gateway cannot translate
  faithfully is refused with a clear error, the same way on every path. An
  incomplete upstream reply reaches the session as a clear retryable error,
  never a silent truncation.
- **Authorization**: the gateway holds the upstream credentials (provider
  keys, self-hosted endpoint keys, cloud service accounts). On every
  surface it checks gateway key -> role -> allowed routes and models; a
  client cannot choose the upstream. Sessions receive a gateway key scoped
  to their role. Today only the per-session bridge hides the upstream
  credential; direct endpoint profiles pass it into the session. The
  gateway's new guarantee is that no session, on any path, holds an
  upstream credential. Author/judge separation moves from distinct key
  files (today's preflight only checks the two files differ) into this rule.
- **Accounting**: the gateway meters spend per gateway key — per role per
  deployment — against the deployment's budget cap; the cap is
  load-bearing. A session-supplied run header is forgeable, so per-run
  attribution stays with session capture; per-run keys remain open
  (question 3).
- **Logs**: token counts and metadata only — never prompts, bodies or
  auth headers. Today's bridge disables message logs; the gateway keeps
  that rule.
- **Timeouts**: per route and sized to the model. Long completions from
  large open models need far longer than chat-sized calls; one
  gateway-wide limit is the wrong shape.
- **Profiles**: an endpoint profile names the gateway through a URL file,
  for example `OUTERLOOP_ENDPOINT_AUTHOR_URL_FILE=/run/model-gateway/author`
  with `_MODEL=open-model`. The file is published only once the gateway
  answers. The kernel probes URL-file profiles (the existing `/v1/models`
  probe), so a gateway that is down parks fresh runs and defers wakes via
  the endpoint-wait path; fixed `_URL` profiles are not probed, so the
  gateway profile uses a URL file. `_API` lists the surfaces offered for
  that route, so preflight stays a local check.
- **Lifecycle**: an operator service with a health endpoint, pinned like
  the harnesses (`harnesses.toml`). `outerloop harness upgrade` knows only
  claude, codex, hermes and bridge today, so the gateway needs its own pin
  entry and installer; that is part of the build. Restart without killing
  sessions: start the new gateway, republish the endpoint file, then stop
  the old gateway once the longest request timeout has passed.

## What changes for sessions

- Codex keeps Responses over HTTP/SSE; the gateway does the translation the
  bridge did. Claude Code keeps Anthropic Messages, direct. Hermes keeps
  Chat.
- Containers need network access to the gateway (they already reach the
  model server for direct endpoints). The per-session loopback listeners,
  tokens and read-only bridge runtime go away.

## Trade-offs

- **Availability**: the gateway is shared; if it is down, fresh runs park
  and wakes defer (endpoint-wait on the URL file). A session already
  running sees a retryable error; endpoint-wait does not cover it. The
  model server is already shared in the same way.
- **Blast radius of a bad upgrade**: one pinned gateway upgrade reaches
  every session at once. Mitigation: a conformance check (below) before any
  pin moves, pinning, never floating.
- **Trust boundary**: today the credential lives inside each session's
  container for the session's lifetime (bridge shim or direct key). With
  the gateway it lives in one operator-owned process. A gateway key's
  scope (role, models, a spend cap) limits what a leaked key can do.

## Migration

1. Ship the gateway as an optional service, with profiles pointing at it,
   while the per-session bridge stays the default for `API=chat`.
2. Run one deployment on the gateway through a trial period; compare
   session outcomes and token accounting with session capture.
3. Make the gateway the default; delete the per-session bridge, its runtime
   install and its tests in the same change (one implementation per role).

## Conformance check

A scripted session per (harness version, gateway version, upstream server)
triple: tool calls, freeform `apply_patch`, resume, streaming, and a refused
unsupported part. Run before any of the three pins moves. It would have
caught the Codex 0.160 tool types before it reached all deployments, and it
records each harness's client retry window so a restart knows how long the
old gateway must keep answering.

## Open questions

1. Where the gateway runs on a cluster without a long-lived service host.
   Model servers on such clusters run as scheduler jobs under a service
   manager that starts a successor before the current job's walltime and
   publishes an endpoint file once the new server answers. The same
   pattern works for the gateway; the alternative is a small always-on VM.
2. Whether Claude Code sessions on an Anthropic-compatible server should
   also go through the gateway (uniform accounting) or stay direct (one
   hop fewer). Deferred with the Anthropic Messages surface.
3. Gateway key scope: per role per deployment (first build), or per run.
   Per-run keys, revoked at the run's end, would move per-run attribution
   into the gateway; forgeable run headers make that the open question.
