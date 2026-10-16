# Community channels

Outerloop's community lives on Discord, with releases and results also
posted to X and Bluesky. This page lists what each channel carries and how
the Discord webhooks are wired, so you can set up the same for your own
repositories.

## Discord

Server: [discord.gg/W5fuTFtJwF](https://discord.gg/W5fuTFtJwF), linked from
the landing page.

| Channel | What goes there | Source |
|---|---|---|
| `#announcements` | releases of `outerloop-science`, with the release notes | a webhook on `outerloop-science/outerloop`, event Releases |
| `#dev` | pull requests on the kernel: opened, closed, merged, reopened | a second webhook on `outerloop-science/outerloop`, event Pull requests |
| `#speedrun` | every pull request on the lab's speedrun target repo, agents' and people's alike | a webhook on the target repo, event Pull requests |
| `#help` | adopters' questions about install and setup | people |
| `#general` | everything else | people |

That is three webhooks. One Discord webhook posts to one channel, and one
GitHub webhook has one destination, so a message is always a GitHub event
and never a second copy of state. GitHub sends every pull-request action;
Discord's formatter posts the opened, closed, merged and reopened ones and
drops the rest, which is what the table promises. Set one up once per
channel:

1. In Discord, under Server Settings → Integrations → Webhooks → New
   Webhook, pick the channel and copy the webhook URL. The URL lets anyone
   who holds it post to that channel, so treat it as a credential. It lives
   in GitHub's webhook settings and nowhere else.
2. In GitHub, under the repository's Settings → Webhooks → Add webhook, set
   the payload URL to the Discord URL with `/github` appended and the
   content type to `application/json`. Under "Let me select individual
   events", tick only the events the table names, Releases for
   `#announcements` and Pull requests for `#dev` and `#speedrun`. Leave pushes,
   issues and comments unticked, or the channel drowns.
3. Send the test payload GitHub offers and check the channel.

If you want a target repository to have its own channel, repeat steps 1 and
2 for it.

## X and Bluesky

`@outerloop_sci` on X and `@outerloop.science` on Bluesky, both linked from
the landing page. A person writes the posts, for final releases and results
worth the attention of people who do not read the changelog. Development and
pre-release builds go to Discord only.
