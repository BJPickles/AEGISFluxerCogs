# Flux Questions

A persistent, reaction-driven Q&A system for **Fluxer** communities.

Flux Questions turns ordinary community messages into numbered questions using
a guild-specific reaction emoji. Questions are posted to a dedicated pending
channel with native 👍 and 👎 reactions, may be privately corrected by their
author for a configurable time window, may continue to be clarified by trusted
staff after that soft-lock, and can then be answered into a permanent archive.

Designed for Red-based bots using the Fluxer patches.

---

## Features

- Reaction-based question submission from ordinary community messages
- Guild-configurable submission emoji
- Unicode and custom guild emoji support
- Self-submission by the original message author
- Privileged submission of other users' messages
- Separate submitter, editor, and operator roles
- Optional source-channel restrictions
- Permanent per-community question numbering
- Native 👍 and 👎 community-interest voting
- No vote threshold or automatic approval logic
- No buttons or persistent Views
- Final vote snapshots when a question is answered
- Bot seed reactions excluded from exact totals
- Bot accounts excluded from exact totals
- Users reacting with both choices are ignored
- Configurable author self-edit window
- Private DM editing workflow
- Raw Markdown copy/paste support in DM editing
- Multiline Markdown question and answer support
- Privileged question editing after the author soft-lock
- Permanent question revision history
- Question reverts without destroying revision history
- Permanent answers
- Answer editing in place
- Permanent answer revision history
- Soft removal of pending questions
- Verbose per-community audit-log channel
- Unix timestamp storage
- Fluxer timestamp display using:
  - `<t:1785181680:F> (<t:1785181680:R>)`
- Pending-question repair and recreation
- Answer repair and recreation
- Automatic restart/crash reconciliation
- Interrupted-answer recovery
- Persistent source-message duplicate protection
- Aggregate statistics
- Red data-deletion support
- No additional Python packages

---

## Design

Flux Questions treats Red Config as the canonical state.

Fluxer messages are projections of that stored state.

A question is not merely a message sitting in `#questions`. It is a permanent
numbered record which may have:

- an original source message
- a pending question message
- question revisions
- a final vote snapshot
- a permanent answer
- answer revisions
- removal metadata
- recovery metadata

The central rule is:

> **Question #147 is always Question #147.**

Question numbers are never deliberately reused.

Changing channels, recreating messages, editing text, answering a question, or
repairing a record does not change its number.

---

## Question Lifecycle

A normal question follows this lifecycle:

```text
Ordinary community message
        │
        │ configured submission reaction
        ▼
Question #147 created permanently
        │
        ▼
Posted in #questions
        │
     👍     👎
        │
        ├── Author may self-edit during timed window
        │
        ├── Editors/operators may clarify while pending
        │
        ▼
Operator answers Question #147
        │
        ├── Current votes are snapshotted
        ├── Permanent answer is posted
        ├── Stored status becomes answered
        └── Pending question message is removed
```

A pending question may instead be soft-removed by an operator.

Its permanent record and number remain stored.

---

## Storage Model

Flux Questions does **not** store every question inside one ever-growing guild
dictionary.

Each question is stored as its own Red Config custom-group record.

Conceptually:

```text
FLUXQUESTIONS_QUESTION
    guild_id
        question_number
            permanent question record
```

A separate persistent source index is maintained:

```text
FLUXQUESTIONS_SOURCE
    guild_id
        source_message_id
            question_number
```

This allows permanent Q&A history to grow without rewriting every historical
question whenever one record changes.

It also allows duplicate source-message protection to survive bot restarts and
crashes.

---

## Crash and Restart Safety

Crash and restart resilience is a core part of the design.

Flux Questions assumes that Red Config operations and Fluxer HTTP operations
cannot form one perfectly atomic transaction. Instead, it stores enough durable
state to identify and repair incomplete work.

### Question Number Reservation

The next question number is persisted before the new question record is fully
created.

This means a hard crash at exactly the wrong moment can leave a harmless gap:

```text
#146
#148
```

rather than risking two different questions both becoming:

```text
#147
```

A skipped number is considered safer than number reuse.

### Question Submission Recovery

When a question record exists but its pending Fluxer message was not fully
attached before a crash, startup reconciliation attempts to:

1. Find an already-posted bot message for that Question ID.
2. Attach it to the stored question if found.
3. Otherwise recreate the pending question in the current questions channel.
4. Restore missing 👍 and 👎 seed reactions.

The stored Question ID is retained.

### Duplicate Source Recovery

If the question record was saved but the source-message index was not saved
before a crash, the storage layer can scan the permanent records and rebuild the
missing index.

Reacting to the same source message after a restart therefore does not
legitimately create a second question.

### Interrupted Answer Recovery

Before an answer is posted, Flux Questions stores a staged answer operation.

The stored operation includes the answer, operator, vote snapshot, and start
time.

If the bot stops after posting the answer but before completing the storage
transition, startup reconciliation searches the answers channel for the
already-posted Question ID.

If it finds the answer, it attaches that existing message and completes the
answer instead of posting another copy.

If no completed answer message exists, the staged answer is cleared and the
question returns to the pending state.

### Pending Cleanup Recovery

If a question was successfully answered but the old pending message could not
be removed before shutdown, startup reconciliation attempts the cleanup again.

### Edited Content Recovery

Question text is saved before its pending embed is edited.

Answer edits are also saved before their published message is synchronised.

This means a crash between Config and Fluxer does not lose the new canonical
text.

Pending questions are reconciled during startup.

Previously edited answers are also resynchronised during startup.

### Manually Deleted Completed Answers

An already-completed historical answer which is later manually deleted by
community staff is not blindly recreated every time the bot starts.

Use:

```text
[p]questions resendanswer #147
```

to repair or recreate that answer from permanent storage.

---

## Timestamps

Flux Questions stores times internally as Unix timestamp integers.

Displayed times use the same format throughout the cog:

```text
<t:1785181680:F> (<t:1785181680:R>)
```

This renders both:

- a full localised timestamp
- a relative timestamp

The format is used for submission times, edits, answers, edit deadlines,
recovery events, history, and audit logging.

---

## Installation

Add the repository containing the cog:

```text
[p]repo add aegis <your_repository_url>
```

Install Flux Questions:

```text
[p]repo install aegis FluxQuestions
```

Load the cog:

```text
[p]load FluxQuestions
```

Replace `[p]` with your bot's command prefix.

---

## Quick Start

### 1. Set the pending questions channel

```text
[p]questions set questions #questions
```

`pending` is also accepted as an alias:

```text
[p]questions set pending #questions
```

### 2. Set the permanent answers channel

```text
[p]questions set answers #answers
```

### 3. Set the verbose audit-log channel

```text
[p]questions set log #questions-log
```

### 4. Set the submission emoji

For example:

```text
[p]questions set emoji ❓
```

Custom community emoji may also be used.

### 5. Check the configuration

```text
[p]questions settings
```

### 6. Submit a test question

Write a normal message in an eligible channel:

```text
Will the desktop client support Linux?
```

Then react to **your own message** with the configured question emoji.

The bot creates the next numbered question in the configured questions
channel and adds:

```text
👍
👎
```

---

## Commands

Question IDs accept either form:

```text
147
#147
```

### Author Command

| Command | Description |
|---|---|
| `[p]qedit <QuestionID>` | Start the private timed self-edit workflow for your own pending question |

### Editor / Operator Commands

| Command | Description |
|---|---|
| `[p]questionedit <QuestionID> <text>` | Edit a pending question in place |
| `[p]questionrevert <QuestionID> <revision>` | Revert a pending question to a stored revision |
| `[p]questions show <QuestionID>` | Display stored question information |
| `[p]questions list [pending/answered/removed] [page]` | List permanent records by state |
| `[p]questions history <QuestionID> [question/answer] [page]` | View stored revision history |
| `[p]questions stats` | Display aggregate Q&A statistics |

### Operator Commands

| Command | Description |
|---|---|
| `[p]answer <QuestionID> <answer>` | Answer a pending question |
| `[p]answeredit <QuestionID> <answer>` | Edit a published answer in place |
| `[p]questionremove <QuestionID> [reason]` | Soft-remove a pending question |
| `[p]questions resend <QuestionID>` | Repair or recreate a pending question |
| `[p]questions resendanswer <QuestionID>` | Repair or recreate a permanent answer |

### Configuration Commands

| Command | Description |
|---|---|
| `[p]questions` | Display current configuration |
| `[p]questions settings` | Display current configuration |
| `[p]questions status` | Alias for settings |
| `[p]questions set questions #channel` | Set the pending questions channel |
| `[p]questions set pending #channel` | Alias for setting the questions channel |
| `[p]questions set answers #channel` | Set the permanent answers channel |
| `[p]questions set log #channel` | Set the verbose audit-log channel |
| `[p]questions set logging #channel` | Alias for setting the log channel |
| `[p]questions set emoji <emoji>` | Set the reaction used to submit questions |
| `[p]questions set editwindow <minutes>` | Set the author's timed self-edit window |
| `[p]questions set editminutes <minutes>` | Alias for editwindow |
| `[p]questions role add <submitter/editor/operator> <role>` | Add a privileged role |
| `[p]questions role remove <submitter/editor/operator> <role>` | Remove a privileged role |
| `[p]questions source add #channel` | Restrict submission to an additional source channel |
| `[p]questions source remove #channel` | Remove an allowed source channel |
| `[p]questions source clear` | Clear source restrictions and allow all eligible channels |
| `[p]questions reconcile` | Manually run the startup-style reconciliation pass |

`questionset` is an alias for the main `questions` command group.

---

## Submission Behaviour

Flux Questions does not require users to write a bot command to submit a
question.

A user writes an ordinary community message, then reacts to that message with
the guild-configured submission emoji.

For example:

```text
Will Monarch Online support Linux?
```

Then:

```text
❓
```

The bot:

1. Confirms the reaction is the configured question emoji.
2. Confirms the source channel is eligible.
3. Ignores Flux Questions' own questions, answers, and log channels.
4. Fetches the source message.
5. Rejects bot-authored source messages.
6. Confirms the reactor may submit the message.
7. Checks persistent duplicate-source protection.
8. Reserves the next permanent Question ID.
9. Stores the source text and metadata.
10. Posts the pending question.
11. Adds 👍 and 👎.
12. Stores the pending message location.
13. Writes a verbose audit entry when logging is configured.
14. Attempts to DM the submitting user a confirmation.

The source message itself remains where it was.

Removing the submission reaction later does not withdraw the question.

There is no reaction-removal listener which treats the trigger emoji as the
continued existence condition for a submitted question.

---

## Self-Submission

A normal member may turn **their own message** into a question.

No special Q&A role is required.

The original message author becomes the permanent question author.

---

## Privileged Submission of Another User's Message

A member may turn somebody else's message into a question when they are:

- the bot owner
- recognised as a Red moderator through the bot/fork
- granted appropriate management permission
- assigned a configured Flux Questions submitter role

The two identities remain separate.

For example:

```text
Original message author: Alice
Reaction submitted by: Bob
```

The permanent record stores conceptually:

```text
author_id = Alice
submitted_by_id = Bob
```

Alice remains the question author.

Bob only nominated Alice's message.

Alice therefore receives the normal author self-edit entitlement.

---

## Unauthorised Submission Attempts

If a user reacts to somebody else's message without permission, Flux Questions
attempts to remove their submission reaction and DM them an explanation.

Failure to remove that reaction does not grant submission rights.

---

## Source Channel Restrictions

By default, the configured question emoji may work in eligible community text
channels.

The cog automatically excludes its configured:

- questions channel
- answers channel
- verbose log channel

This prevents Flux Questions from recursively turning its own messages into new
questions.

Administrators may restrict submission to selected source channels.

Add a source channel:

```text
[p]questions source add #general
```

Add another:

```text
[p]questions source add #development
```

Remove one:

```text
[p]questions source remove #general
```

Clear all restrictions:

```text
[p]questions source clear
```

When the source-channel list is empty, all otherwise eligible channels are
allowed.

---

## Source Message Snapshot

The question text is copied into permanent storage when the reaction submission
is accepted.

The original source message is not treated as the canonical question text after
that point.

For example, if the source originally says:

```text
Will Linux support be added?
```

and its author later edits the ordinary source message to:

```text
five smells lol
```

the stored question does not automatically change.

Question edits go through Flux Questions' revision system.

---

## Question Length

Questions may contain up to **4,000 characters**.

Internal Markdown and line breaks are preserved.

Only leading and trailing whitespace is trimmed.

An attachment-only source message with no textual content is not a valid
question in the current implementation.

---

## Pending Question Embed

A pending question is posted to the configured questions channel.

Conceptually:

```text
❓ Question #147

Will the desktop client support Linux?

Asked by:
Username (User ID)

Submitted:
<t:1785181680:F> (<t:1785181680:R>)

Source:
#general • View original message

Vote with 👍 or 👎 to indicate community interest
```

The exact visible layout is produced by the embed builder and may adapt to
available metadata.

The bot then adds:

```text
👍
👎
```

---

## Voting Behaviour

Voting is deliberately simple.

The reactions indicate **community interest**.

They do not:

- automatically approve a question
- automatically reject a question
- require a threshold
- automatically determine whether the question is answered
- maintain a live vote database

Votes remain native Fluxer reactions.

The bot reads them when an operator answers the question.

### Exact Vote Reading

When reaction-user enumeration is available:

- the bot's own seed reaction is excluded
- other bot accounts are excluded
- each user contributes at most one effective vote
- a user reacting with both 👍 and 👎 is removed from both totals
- the number of dual-vote conflicts is retained in the final snapshot

### Fallback Vote Reading

If Fluxer does not allow exact reaction-user enumeration, the cog falls back to
the visible reaction totals and subtracts its own seed reactions.

The stored snapshot marks whether exact user enumeration was available.

### Vote Storage

Individual voter identities are **not** stored.

When a question is answered, only aggregate data is retained:

```text
up
down
conflicts
exact/fallback indicator
captured timestamp
```

---

## Author Self-Editing

Question authors receive a timed self-edit window.

The default is:

```text
30 minutes
```

Administrators can change it:

```text
[p]questions set editwindow 45
```

The command accepts between 1 and 1,440 minutes.

The timed window is an **author soft-lock**, not a permanent lock on staff.

After the author's window closes, configured editors/operators may continue to
clarify or correct the pending question.

---

## Starting a Private Edit

The original author runs:

```text
[p]qedit #147
```

The cog confirms:

1. Question #147 exists.
2. It is still pending.
3. The command user is the stored author.
4. The configured author edit window is still open.
5. The bot can DM the user.

If DMs cannot be opened, the bot says so in the community channel and does not
start an edit session.

---

## DM Editing Workflow

When the DM succeeds, the bot sends an editing card containing the current
question as **raw Markdown** inside a safely generated codeblock.

Conceptually:

````text
Editing Question #147

Copy your current question below, make your changes, then send the corrected
version back to me.

```text
Will the desktop client support **Linux**?

I also saw [this roadmap](https://example.com).
```

Edit deadline:
<t:1785183480:F> (<t:1785183480:R>)
````

The user's next DM becomes the proposed replacement question.

The user may send:

```text
cancel
```

or:

```text
cancel edit
```

to end the active DM edit session.

---

## Safe Raw-Markdown Codeblocks

Flux Questions does not blindly wrap user text in a fixed triple-backtick
fence.

If the question itself contains backticks or fenced code, the outer fence is
automatically lengthened.

This keeps the DM copy/paste representation valid even when the user's question
contains Markdown codeblocks.

---

## DM Edit Session Grace

The stored author entitlement is based on the configured question edit window.

An already-started DM session receives a small grace period so an author who
began editing near the deadline is not cut off while typing.

The current implementation gives an active DM session at least an additional
five minutes from session start when necessary.

The permanent question itself must still be pending.

If staff answer or remove the question while the user is editing it, the DM
edit cannot overwrite the completed state.

---

## Author Edit Result

A successful author edit:

1. Saves the new question text as a new permanent revision.
2. Updates the stored current question.
3. Records an edit timestamp.
4. Edits the existing pending Fluxer message in place.
5. Preserves its existing 👍 and 👎 reactions.
6. Writes a verbose before/after audit entry.
7. DMs the author a rendered confirmation.

The pending question is not deleted and reposted merely because its text
changed.

---

## Staff / Privileged Question Editing

Configured editors and operators may continue editing a pending question after
the normal author's self-edit window closes.

For example:

```text
[p]questionedit #147 Will the desktop application support Linux, including Ubuntu- and Fedora-based distributions?
```

Multiline Markdown is supported:

```text
[p]questionedit #147 Will the desktop application support **Linux**?

Specifically, does this include Ubuntu- and Fedora-based distributions?
```

The existing pending question message is edited in place.

Votes remain attached to it.

---

## Revision History

Question edits do not destroy the previous text.

A question begins with revision 1:

```text
Revision 1
Kind: Submitted
```

Each genuine edit appends a new revision containing:

- revision number
- question content
- editor ID
- edit timestamp
- edit kind

A stored history may therefore look conceptually like:

```text
Revision 1
Will Linux support be added?

Revision 2
five smells lol

Revision 3
Will Linux support be added?
```

This allows staff to recover from accidental or malicious edits without losing
the audit trail.

---

## Viewing Question History

Display question revisions:

```text
[p]questions history #147
```

or explicitly:

```text
[p]questions history #147 question
```

Display answer revisions:

```text
[p]questions history #147 answer
```

A page may also be supplied:

```text
[p]questions history #147 question 2
```

---

## Reverting a Pending Question

Editors/operators may revert to an earlier stored question revision:

```text
[p]questionrevert #147 1
```

A revert does **not** erase later revisions.

Instead, the historical text is copied into a new current revision.

For example:

```text
Revision 1 — original
Revision 2 — unwanted edit
Revision 3 — staff revert using Revision 1
```

The pending embed is then synchronised to the new current revision.

---

## Answering Questions

An operator may answer a pending question with:

```text
[p]answer #147 Yes, Linux support is planned.
```

Multiline Markdown is preserved:

```text
[p]answer #147 Yes. **Linux support is planned.**

There are still some technical issues to resolve.

Read more [on the wiki](https://example.com).
```

Everything following the Question ID is treated as the answer text.

Answers may contain up to **4,000 characters**.

---

## Answer Process

Flux Questions uses a staged answer process:

1. Load the permanent pending record.
2. Confirm the pending message exists or repair it.
3. Read the current 👍 and 👎 reactions.
4. Store the answer and vote snapshot as an unfinished answer operation.
5. Post the permanent answer to the configured answers channel.
6. Save the answer channel and message IDs.
7. Change the permanent question status to `answered`.
8. Clear the unfinished operation marker.
9. Delete the old pending question message.
10. Write the answer event to the verbose audit log.

The pending record is not destroyed.

It becomes an answered permanent record.

---

## Permanent Answer Presentation

A normal completed Q&A is displayed as a permanent answer card containing the
question, answer, final community-interest snapshot, authorship information, and
timeline.

Conceptually:

```text
✅ Question #147

Yes. Linux support is planned.

Question:
Will the desktop client support Linux?

Community interest when answered:
👍 42   👎 3

Asked by:
Username

Answered by:
Operator

Asked:
<t:...:F> (<t:...:R>)

Answered:
<t:...:F> (<t:...:R>)
```

---

## Long Questions and Answers

Flux Questions does not silently truncate a long completed Q&A merely to force
it into one embed.

If the combined question and answer cannot safely fit into a single
Discord-compatible embed layout, the embed layer uses a matched split
presentation:

```text
Embed 1:
Question #147
<full question>

Embed 2:
Answer
<full answer>
```

Both embeds are posted as part of the same answer message where supported by
the Fluxer-patched library.

---

## Editing Published Answers

Operators may edit an answered question's published answer:

```text
[p]answeredit #147 Updated answer text.
```

Multiline Markdown remains supported.

The new answer is saved as a new revision and the existing answer message is
edited in place.

The Question ID does not change.

The original answer time remains stored, and the answer receives a last-edited
timestamp.

---

## Answer Revision History

The first answer is answer revision 1.

Subsequent `answeredit` operations append permanent answer revisions.

Use:

```text
[p]questions history #147 answer
```

to inspect the stored answer history.

The current implementation provides question revert tooling for pending
questions. Answer history is retained for auditability, but there is no separate
public `answerrevert` command in version 1.0.0.

---

## Soft Removal

Operators may remove a pending question:

```text
[p]questionremove #147
```

or include a reason:

```text
[p]questionremove #147 Duplicate of Question #121.
```

Removal reasons may contain up to **1,000 characters**.

Soft removal changes:

```text
status = pending
```

to:

```text
status = removed
```

and stores:

- removing actor ID
- removal timestamp
- optional reason

The pending Fluxer message is then removed where possible.

The Question ID and permanent record remain.

Only pending questions use this soft-removal path.

---

## Question States

Permanent questions use three normal states:

```text
pending
answered
removed
```

### Pending

Waiting for an answer.

May receive community-interest reactions.

May be author-edited during the timed window.

May be edited/reverted by configured staff.

### Answered

Has a permanent stored answer and vote snapshot.

The pending message should no longer remain.

The published answer may be edited by operators.

### Removed

Was soft-removed while pending.

Its permanent number, question record, removal metadata, and revision history
remain available to authorised staff.

---

## Verbose Audit Logging

Flux Questions can write a human-readable audit trail to a configured channel.

Set it with:

```text
[p]questions set log #questions-log
```

The log channel is intended for community operators rather than end users.

The Python logger remains separate:

```text
red.five.fluxquestions
```

### Audit Events

The verbose log can include events such as:

- question submitted
- privileged submission of another user's message
- author DM edit session started
- author question edit completed
- staff question edit
- question revert
- question answered
- answer edited
- question soft-removed
- pending-message repair
- answer-message repair
- interrupted-operation recovery
- startup reconciliation errors
- source-index conflicts
- permission problems
- configuration changes
- role changes
- source-channel changes

### Before / After Logging

Question and answer edits include human-readable before/after text in the staff
audit entry where practical.

Long audit text may be shortened for embed safety.

The permanent revision record remains authoritative.

### Technical Exceptions

Detailed Python exceptions and tracebacks are written through the normal Python
logger rather than dumping stack traces into the staff-facing Fluxer channel.

The audit log receives a readable summary.

---

## Role Model

Flux Questions intentionally separates three configurable privileges.

### Submitter Role

A submitter may turn **another user's ordinary message** into a question.

This does not grant answer or editing authority.

### Editor Role

An editor may:

- edit pending questions
- revert pending questions
- inspect question records
- inspect revision history
- list questions
- view statistics

The editor role exists specifically so the author soft-lock does not prevent
trusted staff from clarifying or fixing a question later.

### Operator Role

An operator receives editor-style question access and may additionally:

- answer questions
- edit published answers
- soft-remove pending questions
- repair/recreate pending question messages
- repair/recreate permanent answer messages

### Red Moderation / Ownership

The cog also honours bot ownership and Red/fork moderation or management checks
used by the implementation.

Administrators with the appropriate Red checks configure channels, roles,
source restrictions, and the edit window.

---

## Configuring Roles

Add a submitter:

```text
[p]questions role add submitter @Community Helper
```

Add an editor:

```text
[p]questions role add editor @Q&A Editor
```

Add an operator:

```text
[p]questions role add operator @Q&A Operator
```

Role mentions, raw numeric role IDs, exact names, and case-insensitive exact
names are supported by the custom role converter.

Remove a role:

```text
[p]questions role remove editor @Q&A Editor
```

Role type aliases include:

```text
submit
edit
op
```

for:

```text
submitter
editor
operator
```

---

## Custom Emoji Configuration

The question submission trigger may be a Unicode emoji or a custom community
emoji.

Unicode example:

```text
[p]questions set emoji ❓
```

Custom emoji may be supplied using:

- rendered custom emoji mention
- numeric emoji ID
- `:name:`
- exact emoji name

Custom emoji identity is stored by numeric ID.

This means renaming the custom emoji does not change its authoritative stored
identity.

---

## Changing the Questions Channel

Set a new pending channel:

```text
[p]questions set questions #new-questions
```

Existing pending questions retain their stored message/channel location when
their old message still exists.

If an existing pending message is genuinely gone, repair/recreation uses the
current configured questions channel.

Use:

```text
[p]questions resend #147
```

to repair one explicitly.

---

## Changing the Answers Channel

Set a new answers channel:

```text
[p]questions set answers #new-answers
```

Existing permanent answer messages remain where they were originally posted
when those messages still exist.

If an answer needs recreation, repair uses the current configured answers
channel.

Use:

```text
[p]questions resendanswer #147
```

---

## Repairing Pending Questions

Use:

```text
[p]questions resend #147
```

For an existing pending message, the cog attempts to:

- keep the same Question ID
- edit the message to match canonical stored text
- preserve existing user reactions
- restore a missing bot 👍 seed reaction
- restore a missing bot 👎 seed reaction

If the old message no longer exists, the cog recreates the pending question in
the current questions channel.

Votes attached to a deleted Fluxer message cannot be recovered.

The recreated message begins with fresh seed reactions.

---

## Repairing Answers

Use:

```text
[p]questions resendanswer #147
```

The cog attempts to find and synchronise the stored answer message.

If the old answer is genuinely missing, it recreates the permanent answer in
the current answers channel and updates the stored message location.

The Question ID, answer text, question text, answer revision history, and stored
vote snapshot remain unchanged.

---

## Manual Reconciliation

Administrators can manually run the same general recovery pass used after bot
startup:

```text
[p]questions reconcile
```

This is useful after:

- restoring permissions
- fixing channels
- an unusual crash
- manual message changes
- storage/index repair work

Normal restart recovery is automatic.

---

## Listing Questions

List pending questions:

```text
[p]questions list
```

or:

```text
[p]questions list pending
```

List answered questions:

```text
[p]questions list answered
```

List removed questions:

```text
[p]questions list removed
```

Display another page:

```text
[p]questions list pending 2
```

The compact shorthand:

```text
[p]questions list 2
```

means pending page 2.

Each page contains up to ten records.

---

## Showing a Question

Use:

```text
[p]questions show #147
```

The stored-information card may include:

- current status
- current question text
- creation time
- author
- submitter
- question revision count
- answer revision information
- source link
- pending-message link
- answer link
- unfinished-operation warning where applicable

---

## Statistics

Use:

```text
[p]questions stats
```

Statistics include:

- total permanent records submitted
- currently pending
- answered
- removed
- last reserved Question ID
- next Question ID

The counter is never deliberately reduced.

A crash may leave an unused number if it stopped after reservation but before
the question record was completed.

That number is not reused.

---

## Configuration Display

Use:

```text
[p]questions settings
```

The settings embed includes:

- questions channel
- answers channel
- verbose log channel
- submission emoji
- author self-edit window
- next Question ID
- submitter roles
- editor roles
- operator roles
- source-channel restrictions
- record statistics

---

## Permissions

### Source Channels

To process a reaction submission, the bot must be able to access and fetch the
original source message.

In practice it should have:

- View Channel
- Read Message History

The cog may also attempt to remove an unauthorised submission reaction. Failure
to perform that optional cleanup does not make the submission valid.

### Pending Questions Channel

The bot requires:

- View Channel
- Send Messages
- Embed Links
- Add Reactions
- Read Message History

These permissions are checked when configuring/using the pending channel.

### Answers Channel

The bot requires:

- View Channel
- Send Messages
- Embed Links
- Read Message History

Read Message History is used for crash recovery and answer-message repair.

### Verbose Log Channel

The bot requires:

- View Channel
- Send Messages
- Embed Links

### Direct Messages

DMs are required only for the author's private `qedit` workflow and some
best-effort user notifications.

If the bot cannot DM an author when `qedit` is requested, it reports the
problem publicly and does not start the private edit session.

A DM failure does not erase the question.

---

## Markdown and Mentions

Question and answer text is stored as user-supplied Markdown with internal line
breaks preserved.

The cog uses `AllowedMentions.none()` on its primary embed sends where
appropriate so user-controlled text does not intentionally generate unwanted
mentions merely by being republished.

---

## Stored Data

Per community, Flux Questions stores configuration including:

- schema version
- last reserved question number
- aggregate submitted count
- aggregate answered count
- aggregate removed count
- questions channel ID
- answers channel ID
- verbose log channel ID
- configured submission emoji data
- submitter role IDs
- editor role IDs
- operator role IDs
- source-channel IDs
- author self-edit-window duration

### Permanent Question Records

A question record may store:

- existence/schema marker
- Question ID
- author user ID
- submitting user ID
- source channel ID
- source message ID
- pending channel ID
- pending message ID
- current question text
- submission timestamp
- last question edit timestamp
- current revision number
- question revision history
- current status
- final vote snapshot
- permanent answer data
- removal data
- temporary recovery-operation data

### Question Revisions

A revision may store:

- revision number
- question text
- editor user ID
- timestamp
- edit kind
- reverted revision reference when applicable

### Answer Data

An answered question may store:

- answer text
- answering operator ID
- answer timestamp
- last answer-edit timestamp
- answer channel ID
- answer message ID
- current answer revision
- answer revision history

### Vote Snapshot

The final snapshot may store:

- 👍 total
- 👎 total
- dual-vote conflict total
- whether exact reaction-user enumeration succeeded
- capture timestamp

Individual voter IDs are not retained in the permanent vote snapshot.

### Soft Removal Data

A removed question may store:

- removing actor user ID
- removal timestamp
- optional reason

### Recovery Operation Data

During an unfinished state transition, the record may temporarily retain enough
information to recognise and recover the operation after a restart.

For an answer this includes the staged answer itself through the normal answer
record plus an operation marker identifying the operator and start time.

---

## Permanent History

Unlike Flux Suggestions, completed Flux Questions records are **not removed
from Config** after answering.

This is intentional.

The cog needs the permanent record in order to support:

- stable Question IDs
- answer editing
- revision history
- answer recreation
- auditability
- crash recovery
- record lookup
- statistics

The Fluxer messages are not the sole copy of the Q&A.

---

## Data Deletion

Flux Questions implements Red's user-data deletion hook.

For a matching user, the cog performs best-effort removal/anonymisation of
stored personal identifiers.

When the user authored a stored question, the current implementation also
replaces the permanent stored question text and question-revision text with a
data-deletion placeholder and clears its source-message location.

Other appearances of the matching user ID, such as submitter/editor/operator
metadata, are anonymised where handled by the deletion routine.

Permanent Question IDs and non-personal aggregate statistics remain.

### Posted Fluxer Messages

Messages already published into community channels are normal Fluxer messages.

The Red data-deletion hook changes the cog's stored Config data; it does not
guarantee removal of every historical message already visible in community
channels.

Those remain subject to community moderation and Fluxer retention.

---

## Failure Safety

Flux Questions deliberately avoids treating a Fluxer send and a Config write as
if they were one atomic operation.

### Submission

Canonical storage may exist before the pending message is fully posted.

If projection fails, the question remains recoverable rather than being
silently forgotten.

### Answering

The answer is staged in Config before the final answer message is sent.

If sending fails normally, the staged answer is cleared and the question
remains pending.

If the final answer message is sent but the completion write then fails, the
cog deliberately leaves the staged operation available for restart recovery.

It warns staff **not to answer the question again**.

Startup recovery then attempts to locate and attach the already-posted answer.

### Editing

Question/answer text is saved before the Fluxer message is synchronised.

If the live message update fails, the permanent stored revision remains
canonical and can be repaired.

### Cleanup

An answered or removed state is saved before attempting destructive cleanup of
the old pending message.

A cleanup failure therefore does not destroy the permanent result.

---

## Troubleshooting

### Reacting does nothing

Check that:

- the cog is loaded
- a questions channel is configured
- the reaction matches the configured submission emoji
- the source message contains text
- the source channel is permitted by any configured source restrictions
- the source channel is not the questions, answers, or log channel
- the bot can View Channel and Read Message History
- the message has not already been submitted as a question

Check configuration:

```text
[p]questions settings
```

---

### I cannot submit somebody else's message

Normal members may submit only their own messages.

To nominate another user's message, use a Red moderator/management role or add a
Flux Questions submitter role:

```text
[p]questions role add submitter @Community Helper
```

---

### The bot says the message is already a question

Flux Questions maintains a persistent source-message index.

The same original message is intentionally prevented from creating multiple
Question IDs.

Use the existing Question ID rather than resubmitting it.

---

### The bot cannot post pending questions

Check the configured questions channel permissions:

- View Channel
- Send Messages
- Embed Links
- Add Reactions
- Read Message History

Then set the channel again if needed:

```text
[p]questions set questions #questions
```

---

### The author cannot self-edit

Check:

- the question is still pending
- they are the stored original author
- the configured self-edit window has not closed
- they can receive DMs from the bot

Check the configured window:

```text
[p]questions settings
```

---

### The author's edit window has closed

This is intentional.

The timed restriction applies to ordinary author self-editing.

Configured editors/operators may still clarify or correct a pending question:

```text
[p]questionedit #147 Corrected question text
```

or revert it:

```text
[p]questionrevert #147 1
```

---

### Someone replaced their question with nonsense

Use revision history:

```text
[p]questions history #147 question
```

Then restore an appropriate revision:

```text
[p]questionrevert #147 1
```

The unwanted revision remains in history for audit purposes.

---

### A pending question message was deleted

Repair it:

```text
[p]questions resend #147
```

The Question ID remains the same.

The stored question text is retained.

Votes attached only to the deleted Fluxer message cannot be recovered.

---

### A voting reaction was removed

Repair the pending question:

```text
[p]questions resend #147
```

If the message still exists, missing bot seed reactions are restored without
deliberately replacing the message or its remaining user reactions.

---

### An answer message was deleted

Repair or recreate it from permanent storage:

```text
[p]questions resendanswer #147
```

---

### The bot crashed while answering

Do not immediately answer the same question again.

Flux Questions stores an unfinished answer operation before sending the final
answer.

On restart it attempts to:

1. find the already-posted answer and attach it, or
2. clear the staged answer and restore the pending question when no completed
   answer exists

Review the verbose log after restart.

Administrators may also run:

```text
[p]questions reconcile
```

---

### The pending message remains after answering

The answer and permanent state may still be safe even when cleanup failed.

Try a reconciliation after checking permissions:

```text
[p]questions reconcile
```

The verbose log records cleanup problems.

---

### Vote totals differ from the visible reaction number

The visible Fluxer reaction total may include the bot's own seed reaction.

Flux Questions excludes its own 👍 and 👎 from the stored result.

When exact reaction-user enumeration is available, bot accounts are also
excluded.

---

### A user's vote was ignored

If the same user reacted with both 👍 and 👎, both choices are treated as
ambiguous and removed from the exact totals.

They should leave only one reaction before the question is answered.

---

### The log channel is empty

Check:

```text
[p]questions settings
```

The bot needs:

- View Channel
- Send Messages
- Embed Links

in the configured verbose log channel.

The Python log remains separate from the Fluxer audit channel.

---

### A custom role will not resolve

The Flux Questions role converter supports:

- exact role mention
- raw role ID
- exact role name
- case-insensitive exact role name

When duplicate role names are ambiguous, use the numeric role ID.

---

### A custom emoji will not resolve

Try:

- the rendered custom emoji
- its numeric emoji ID
- `:emoji_name:`
- its exact name

For custom emoji, the stored numeric ID is authoritative.

---

## Updating

Check for updates:

```text
[p]cog checkforupdates
```

Install available updates:

```text
[p]cog update
```

Reload the cog:

```text
[p]reload FluxQuestions
```

Permanent records and guild configuration are stored through Red Config and
survive normal cog reloads and bot restarts.

---

## Requirements

- Red Discord Bot patched for Fluxer
- Red 3.5.0 or newer
- Python 3.8 or newer
- Fluxer permissions required by the configured channels
- No additional Python packages
- No message-component support required

---

## Files

The cog is intentionally split into a small set of focused modules:

```text
fluxquestions/
├── __init__.py
├── fluxquestions.py
├── storage.py
├── embeds.py
├── converters.py
├── utils.py
└── info.json
```

### `fluxquestions.py`

The actual Cog:

- commands
- reaction listener
- DM edit listener
- permissions
- answering workflow
- message repair
- startup reconciliation
- audit-log orchestration

### `storage.py`

Canonical durable state:

- per-question records
- permanent numbering
- source-message index
- question revisions
- answer revisions
- soft removal
- answer staging
- crash-recovery metadata
- aggregate statistics

### `embeds.py`

Presentation:

- pending question embeds
- answer embeds
- long Q&A split layouts
- DM edit cards
- revision-history embeds
- settings
- audit-log embeds

### `converters.py`

Command input:

- `147` / `#147` Question IDs
- Fluxer-friendly role resolution
- Unicode/custom question emoji resolution

### `utils.py`

Shared side-effect-free helpers:

- Unix timestamps
- Fluxer timestamp rendering
- raw-Markdown codeblock fencing
- Question ID parsing
- emoji serialisation/comparison
- text limits
- role/ID helpers
- Fluxer message URLs

---

## Version

```text
1.0.0
```

---

## Licence

MIT

---

Developed by **Five** for the **AEGIS** community.
