# Design: an assistant that proposes changes a person approves (v0.2)

Status: mostly built, not released, and tested only against fakes and mocks. It hasn't run against a real team or the
live Anthropic API. Version 0.1 stays read-only, and its client refuses every write tool.

Merged:

- Proposal and approval store (#89).
- The `propose` policy with argument schemas for the six proposable tools (#90, #92).
- The write executor, separate from `EpiphanClient` (#93).
- The assistant, which reads through the guard and can only propose (#94).
- The local approval page (#95).

Still to do:

- Chat wiring is in progress.
- Live verification is not done. It waits for the first live run, and the "Open until the first live run" list below
  still applies.

## Goal

An assistant that answers questions about the fleet in plain English or Spanish, and that can propose a change, such as starting a
recording, stopping a stream, or updating firmware. A person approves each change on a local page before it runs.

## Non-goals

- No change runs without a person approving that exact change. There are no standing approvals: no "always", no "similar", no
  "approve all".
- The heartbeat stays read-only. It never holds a sign-in that can write through Fleetwatch.
- Nothing is approved from Slack or Teams in v0.2.
- No shell, code execution, plugins, or skills.

## Decisions

| Topic | Decision |
|---|---|
| Model | Claude Haiku 5.5 through the Anthropic API (`FLEETWATCH_AI_MODEL`, default `claude-haiku-5-5`). No key, or the API can't be reached: the keyword `ask` answers instead and says so. Local models are backlog (#82). |
| Who chats | The operator only, on a local page. |
| Who approves | A person, on a separate approval page, one change at a time. |
| Where writes can go | One sandbox team, through its own sign-in. See "Fence". |
| Release | v0.1.0 ships read-only first. This design ships as v0.2.0. |

## Parts

1. Model tools. The model gets the read tools through the existing guarded client (`EpiphanClient.call`, results already
   redacted), plus one tool, `propose_change(tool, arguments, reason)`. That tool only writes a pending proposal row. It can't
   run anything. The model never sees a write tool directly.
2. `propose` policy. A new section in `tool_policy.yaml` lists each tool that may be proposed, with its argument schema, the
   maximum number of targets, and whether it's disruptive. A write tool that isn't listed can't be proposed. A tool that hasn't
   been reviewed counts as disruptive.
3. Proposal store. New SQLite tables in `state.py`: `proposals` and `approvals`.
4. Approval page. A separate local page, on its own port, that shows one proposal card at a time with Approve and Deny.
5. Write executor. A new class, separate from `EpiphanClient`, that takes only a consumed, validated approval. `EpiphanClient.call()`
   keeps refusing writes unconditionally, and `guard()` doesn't change.

## Flow

1. The operator asks a question. The model reads through the guarded read tools.
2. The model calls `propose_change`. Code then validates the arguments against the tool's schema and resolves each target to a
   device ID, using the sandbox sign-in's own fresh device list. It refuses if any target isn't on that list. It stores the proposal
   with the canonical arguments and a state fingerprint: each target's online status, recording flag, and next event start.
3. The approval page shows the card. Code builds every fact on the card from the stored proposal and fresh state:
   - the tool in plain words
   - each target's device ID, current name, model, and state
   - every argument in full
   - what happens, and how to undo it

   The model's reason appears in a separate box labelled "Written by the assistant, not checked", as escaped plain text with a
   length cap. The card won't render if any argument can't be shown in full.
4. The operator selects Approve. In one SQLite statement, the approval is consumed:
   `UPDATE approvals SET used = 1 WHERE id = ? AND used = 0 AND expires_at > ?`. If no row changed, it's refused.
5. The executor re-reads each target through the read tools, right then. It fails closed if a read fails, if the state fingerprint
   changed, or if a disruptive change now hits a room that's recording or inside the readiness window.
6. The executor calls the write tool once. There's no retry, not even after a 401. "Consumed but outcome unknown" is a final
   state that the operator sees.
7. Every step goes to the audit table: proposal, approval, denial, expiry, refusal, and result.

## Binding

- One canonical form is used for hashing and for sending: sorted keys, UTF-8, integers stay integers, no duplicate keys, defaults
  filled in. The same function produces it at proposal time and at run time.
- The bound record holds the proposal ID, the tool, the canonical arguments, the resolved target IDs, the state fingerprint, the
  schema version, and the sign-in slot. A keyed hash (HMAC) uses a secret made when the process starts.
- An approval is single-use and expires after five minutes on the database clock. Every pending proposal expires when the process
  starts.
- Names are resolved to IDs once, at proposal time, and never again.

## Fence: the sandbox sign-in

- Writes use a second sign-in, stored in its own token slot, made by signing in to the sandbox team only. The heartbeat and the
  read-only assistant keep using the normal sign-in and never load the sandbox token.
- Before each write, the executor checks that every target device ID is on the sandbox sign-in's own device list, freshly read.
- Fail closed: a change needs `FLEETWATCH_WRITE_DEVICE_IDS` (an allowlist of sandbox device IDs, which doesn't depend on Epiphan reporting anything), `FLEETWATCH_WRITE_TEAM_ID`, or both. With neither, the assistant proposes nothing, the executor refuses, `approve --serve` won't start, and `doctor` fails. The assistant checks the allowlist when it proposes and the executor checks it again when it runs.
- If Epiphan exposes a team ID (to confirm on the first live run), the executor also checks it against `FLEETWATCH_WRITE_TEAM_ID`.
- `login --sandbox` refuses, and forgets the sign-in, when the sandbox's device list overlaps the devices the normal sign-in has already saved.
- With no sandbox sign-in, Fleetwatch can't run any write.

## The approval page

- It has its own port, separate from `ask --serve`. It's never the wall-screen page.
- It listens on 127.0.0.1 only.
- A secret made when the process starts is printed to the console, never put in a URL. The page asks for it once per browser
  session.
- Every POST needs a token bound to the proposal ID, plus a same-origin `Origin` or `Sec-Fetch-Site` header. There are no GET
  side effects, and the existing Host header check stays.
- Approve is never the default or focused button. The model never sets labels, colors, or focus. The only countdown is the expiry.
- Disruptive changes need a second confirm that names the room.

## Limits against approval fatigue

- At most three pending proposals, and one card on screen at a time.
- A cap on proposals per hour.
- After three denials of the same tool and target, the assistant stops proposing it for an hour.
- The audit log counts approvals per session, so a rubber-stamping pattern shows.

## Disruptive tools

These are refused while a target room is recording or inside the readiness window, even with approval:
- `batch_reboot`, `batch_firmware_update`, and `apply_team_preset`
- `stop_stream_endpoint`, `delete_cms_event`, and `delete_stream_endpoint`
- for review, treated as disruptive until reviewed: `batch_recording` (stop), `switch_device_to_cms`, `update_cms_event`, and
  `cms_event_action`

## Prompt injection

The model isn't trusted, and injection alone isn't the boundary. These are:
- the read guard
- the `propose` allowlist and schemas
- the sandbox sign-in and device membership check
- the state refusal for disruptive tools
- a person approving a card built by code

Device, channel, source, and event names go into the prompt as quoted data fields, with caps on count and length. A test corpus of
injection strings in names must never produce a proposal.

## Data sent to the model

Redacted tool results and the operator's question go to the Anthropic API. README, SECURITY.md, and `doctor` say so, and say how
to turn it off: leave `FLEETWATCH_ANTHROPIC_API_KEY` empty. The model's raw prompts aren't written to disk by default.

## Fallback

The keyword `ask` stays read-only and can't create a proposal. If the model call fails partway, any proposal from that turn expires.

## Tests

- The model is mocked, and a fake MCP server records writes. CI makes no real Epiphan or Anthropic calls.
- Refusal tests:
  - no approval
  - an expired, reused, or tampered approval
  - changed arguments
  - a target outside the sandbox list
  - a changed state fingerprint
  - a disruptive change near an event
  - no sandbox sign-in
  - `observe` mode
  - a cross-site POST
  - a missing page secret
- A test fails if `call_tool` is used outside the two client classes.
- An injection corpus test.
- One end-to-end replay: a question, then a proposal, then the card, then Approve. The fake server records exactly one write, and a
  second Approve is refused.

## Docs to change when it ships

- README and README.es.md: "What you get" and "Status".
- SECURITY.md trust model: data sent to the model, and the boundary list above. Add SECURITY.es.md too.
- AGENTS.md hard rule 2: the read list never holds a write tool, `guard()` never weakens, writes go only through the executor with
  a consumed approval, and no flag or setting skips approval or the fence.

## Open until the first live run

- Whether Epiphan returns a team ID.
- That each proposable tool takes the arguments in its `tool_policy.yaml` schema. The schemas come from the Epiphan MCP
  server's tool definitions; the live run confirms them.
- How a firmware update reports progress and finishes.
