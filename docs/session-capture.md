# Session capture

Every model session Outerloop runs leaves private evidence in that run's
directory: the delivered prompt, the captured output, the model's own
transcript, and a record of tokens and cost. This page covers where the
evidence lives, what is redacted, how to price tokens yourself, and how the
cost totals in `outerloop status` are computed.

## Session files

Each harness invocation, including every resume or wake, writes its files
under the run's directory, `<root>/runs/<run-id>/`, where `<root>` is the
state root. A
`<workspace>-<backend>-<invocation>.session.json` record holds the backend
and model, the session and resume identifiers, start and end times, stop
reason, turns, the reported token counters, and the session's cost. Beside
it sit the delivered prompt and the stored native log, with the SHA-256
hash of each stored file.

These files sit outside the author's writable workspace and survive the
24-hour workspace cleanup, following the run record's own retention. The
standalone reviewer keeps the same files beside the workspace it was given.
A capture failure is logged and never changes the run's outcome.

## Secret redaction

Stored evidence is redacted against every secret the kernel knows: the
session key, application tokens it issued, and a snapshot of each
credential file it provisioned before the session started. Deleting a
credential during the session does not remove it from the redaction set.
This is not a general secret scanner, so treat the files as private
anyway. Never commit them, attach them to pull requests, or publish them.

The files use owner-only permissions.

## Token pricing

Claude reports dollars directly. For Codex and Hermes, set
`OUTERLOOP_TOKEN_PRICES` to a JSON table of rates in USD per million
tokens, keyed by the exact configured model:

```sh
export OUTERLOOP_TOKEN_PRICES='{"open-model":{"input_tokens":2,"cached_input_tokens":0.5,"output_tokens":10,"cache_creation_input_tokens":2.5}}'
```

Input and output rates are required, and every nonzero bucket needs a
finite, nonnegative rate. Missing models or rates, malformed prices, or
absent usage leave that session's cost unknown, and an unknown cost makes
the run's total unknown. Explicit zero rates price a free or self-hosted
model at zero. This prices recorded usage, not invoices.

## Native log size

The stored native log is capped by `OUTERLOOP_NATIVE_LOG_MAX_BYTES`,
default 32 MiB. A cutoff marks the record `truncated` and stores the cap,
and a truncated log is not guaranteed to be valid JSON. Redaction still looks
across the cutoff so a secret straddling it cannot leak. An invalid value
falls back to the default.

## Cost totals in status

`outerloop status` reports each run's session cost from the run directory's
persisted index, never from files the author could touch:

```text
my-run target=owner/repo ... session-cost=$1.2345 unpriced-sessions=0
```

A run shows `unknown` cost while any captured session is unpriced, and
counts those sessions separately. Totals carry a `verified` flag, true when
every indexed session ran inside the session container. An uncontained
session labels the totals `unverified`, and in local mode the author shares
the operator's filesystem, so persisted totals are informational there.

Two backends resume differently, and the totals account for it. A resumed
Codex thread is priced once, at its newest leg, so the same tokens are never
counted twice. Claude session legs are summed, which can overstate a
resumed session on a current CLI but never undercounts one on an older CLI.

Sessions that ran before capture was added have no index entry. Do not
read the known subtotal as total historical spend.
