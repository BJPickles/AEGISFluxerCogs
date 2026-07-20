# Flux Suggestions

A lightweight, anonymous suggestion board for **Fluxer** communities using
native emoji reactions.

Designed for Red-based bots using the Fluxer patches.

---

## Features

- Anonymous pending suggestions
- Native 👍 and 👎 voting
- No buttons or message components
- No persistent Views
- No reaction event listeners
- Automatic suggestion numbering
- Approved and rejected archive channels
- Approved suggestions reveal the author
- Rejected suggestions remain anonymous
- Optional approval and rejection reasons
- Optional result DMs
- Bot seed reactions excluded from totals
- Bot accounts excluded from exact totals
- Users reacting with both choices are ignored
- Deleted suggestion recovery
- Missing reaction repair
- Aggregate statistics
- Per-suggestion source channel tracking
- Red data-deletion support
- Persistent per-community configuration

---

## Design

Flux Suggestions deliberately avoids interaction components.

The bot does not process votes as they happen. Instead, it:

1. Posts a suggestion embed.
2. Adds 👍 and 👎 reactions.
3. Waits for a staff member to approve or reject it.
4. Reads the current reactions directly from the message.
5. Posts the final result.
6. Removes the pending record from Config.

Only pending suggestions are stored.

Once a suggestion is approved or rejected, its text and author ID are removed
from the cog's Config. The resulting Fluxer message remains in the configured
archive channel until community staff delete it.

---

## Installation

Add the repository containing the cog:

```text
[p]repo add aegis <your_repository_url>
```

Install Flux Suggestions:

```text
[p]repo install aegis FluxSuggestions
```

Load the cog:

```text
[p]load FluxSuggestions
```

Replace `[p]` with your bot's command prefix.

---

## Quick Start

### 1. Set the pending suggestions channel

```text
[p]suggestions set pending #suggestions
```

### 2. Set the approved suggestions channel

```text
[p]suggestions set approved #approved-suggestions
```

### 3. Set the rejected suggestions channel

```text
[p]suggestions set rejected #rejected-suggestions
```

### 4. Check the configuration

```text
[p]suggestions settings
```

### 5. Submit a test suggestion

```text
[p]idea We should change the bot's name to GPT.
```

The command message is deleted and an anonymous embed is posted in the pending
channel.

---

## Commands

### User Commands

| Command | Description |
|---|---|
| `[p]idea <text>` | Submit an anonymous suggestion |
| `[p]suggest <text>` | Alias for the idea command |

### Staff Commands

| Command | Description |
|---|---|
| `[p]approve <number> [note]` | Approve a suggestion |
| `[p]reject <number> [reason]` | Reject a suggestion |
| `[p]suggestions list [pending] [page]` | List pending suggestions |
| `[p]suggestions resend <number>` | Repair or recreate a pending suggestion |
| `[p]suggestions stats` | Display aggregate statistics |

### Configuration Commands

| Command | Description |
|---|---|
| `[p]suggestions` | Display the current configuration |
| `[p]suggestions settings` | Display the current configuration |
| `[p]suggestions status` | Alias for settings |
| `[p]suggestions set pending #channel` | Set the pending channel |
| `[p]suggestions set approved #channel` | Set the approved channel |
| `[p]suggestions set rejected #channel` | Set the rejected channel |
| `[p]suggestions clear pending` | Clear the pending channel |
| `[p]suggestions clear approved` | Clear the approved channel |
| `[p]suggestions clear rejected` | Clear the rejected channel |
| `[p]suggestions dm true/false` | Enable or disable result DMs |

---

## Permissions

### User submission channel

The bot requires:

- View Channel
- Send Messages
- Manage Messages

`Manage Messages` is required because the bot must delete the user's command
message before accepting the anonymous suggestion.

If the bot cannot delete the command message, the suggestion is not submitted.

### Pending suggestions channel

The bot requires:

- View Channel
- Send Messages
- Embed Links
- Add Reactions
- Read Message History

### Approved and rejected channels

The bot requires:

- View Channel
- Send Messages
- Embed Links

The bot does not require `Manage Messages` in the pending suggestions channel
because it owns the suggestion message and can normally delete its own message.

---

## Staff Access

Channel configuration commands use Red's administrator checks:

- Red administrator role
- Manage Community / Manage Guild permission
- Guild owner
- Bot owner
- Appropriate Red permission override

Approval, rejection, listing, resending and statistics use Red's moderator
checks:

- Red moderator role
- Manage Messages permission
- Guild owner
- Bot owner
- Appropriate Red permission override

Red's permission system can be used to allow or deny individual commands.

---

## Submitting Suggestions

Users submit suggestions with:

```text
[p]idea My suggestion text
```

or:

```text
[p]suggest My suggestion text
```

The bot:

1. Confirms that the pending channel is configured.
2. Confirms all required channel permissions.
3. Deletes the user's command message.
4. Assigns the next suggestion number.
5. Posts the anonymous suggestion embed.
6. Adds 👍 and 👎.
7. Saves the pending record.
8. Attempts to DM the author a confirmation.

If the author cannot receive DMs, a temporary confirmation is sent in the
original command channel.

Suggestions may contain up to 4,000 characters.

---

## Pending Embed

Example:

```text
💡 Suggestion #15

We should change the bot's name to GPT.

Suggested anonymously • Vote with 👍 or 👎
```

The bot adds:

```text
👍
👎
```

The pending embed never displays the author.

---

## Voting Behaviour

Votes are read only when a staff member runs `approve` or `reject`.

The cog first attempts to retrieve the users attached to both reactions.

When exact user information is available:

- The bot's seed reactions are excluded.
- Other bot accounts are excluded.
- Each person may cast one effective vote.
- A user reacting with both 👍 and 👎 is excluded from both totals.
- The number of ignored dual votes is shown in the result.

If Fluxer cannot return reaction-user information, the cog falls back to the
reaction totals displayed on the message and subtracts its own seed reactions.

No vote data is stored in Config.

---

## Approving Suggestions

Approve a suggestion:

```text
[p]approve 15
```

Approve it with an optional staff note:

```text
[p]approve 15 Planned for the next release.
```

The bot:

1. Fetches the original pending message.
2. Reads the current votes.
3. Posts the approved embed.
4. Saves the completed statistics.
5. Removes the pending Config record.
6. Deletes the pending message.
7. DMs the author when result DMs are enabled.

Approved suggestions reveal the author.

Example:

```text
✅ Approved Suggestion #15

We should change the bot's name to GPT.

Status:
✅ Approved

Votes:
👍 24
👎 7

Staff note:
Planned for the next release.

Suggested by Username (User ID)
```

The pending suggestion is not removed until the approved result has been sent
successfully.

If the approved channel cannot receive the result, the pending suggestion
remains unchanged.

---

## Rejecting Suggestions

Reject a suggestion:

```text
[p]reject 15
```

Reject it with a reason:

```text
[p]reject 15 This would create too much confusion.
```

Rejected suggestions remain anonymous.

Example:

```text
❌ Rejected Suggestion #15

We should change the bot's name to GPT.

Status:
❌ Rejected

Votes:
👍 24
👎 7

Reason:
This would create too much confusion.

Suggested anonymously
```

When result DMs are enabled, the author receives the rejection result and
reason privately.

---

## Result DMs

Result DMs are enabled by default.

Disable them:

```text
[p]suggestions dm false
```

Enable them:

```text
[p]suggestions dm true
```

A result DM includes:

- Suggestion number
- Approved or rejected result
- Final vote totals
- Number of ignored dual votes
- Staff note or rejection reason
- Result link when the author can view the archive channel

DM failures do not prevent approval or rejection.

---

## Listing Pending Suggestions

Display the first page:

```text
[p]suggestions list
```

The following syntax is also supported:

```text
[p]suggestions list pending
```

Display a particular page:

```text
[p]suggestions list pending 2
```

or:

```text
[p]suggestions list 2
```

Each page displays up to ten suggestions.

The list does not reveal suggestion authors.

---

## Repairing or Recreating Suggestions

Use:

```text
[p]suggestions resend 15
```

If the stored message still exists, the cog:

- Leaves all existing votes untouched
- Checks whether the bot's 👍 reaction is present
- Checks whether the bot's 👎 reaction is present
- Re-adds any missing bot reactions

If the message has been deleted, the cog:

- Recreates it in the currently configured pending channel
- Keeps the original suggestion number
- Keeps the original submission timestamp
- Adds fresh voting reactions
- Updates the stored channel and message IDs

Votes from a deleted message cannot be recovered.

If the cog cannot access the old channel, it does not automatically recreate
the suggestion because that could create a duplicate.

---

## Changing the Pending Channel

Changing the pending channel affects new suggestions only:

```text
[p]suggestions set pending #new-suggestions
```

Existing pending suggestions remain in their original channels.

Each pending record stores its own source channel ID and message ID, so existing
suggestions may still be approved or rejected after the configured pending
channel changes.

If an old message or channel has been deleted, use:

```text
[p]suggestions resend <number>
```

The suggestion will be recreated in the current pending channel.

---

## Statistics

Display statistics:

```text
[p]suggestions stats
```

Statistics include:

- Total submitted
- Currently pending
- Approved
- Rejected
- Approval rate
- Last assigned suggestion number

The counter is never reduced or reused.

Deleting or recreating a pending message does not change its suggestion number.

---

## Stored Data

Per community, the cog stores:

### Configuration

- Pending channel ID
- Approved channel ID
- Rejected channel ID
- Result DM setting

### Pending suggestions

- Suggestion number
- Author user ID
- Suggestion text
- Source channel ID
- Message ID
- Submission time
- Pending status

### Aggregate statistics

- Last assigned suggestion number
- Total submitted
- Total approved
- Total rejected

The cog does not store individual votes.

Votes are retrieved directly from Fluxer when a suggestion is resolved.

---

## Completed Suggestions

After successful approval or rejection, the pending Config record is removed.

This means the cog no longer retains:

- The completed suggestion text
- The completed suggestion's author ID
- Its pending message ID
- Its pending channel ID

The approved or rejected Fluxer message remains in the archive channel until
community staff delete it.

---

## Data Deletion

When Red requests deletion of a user's stored data, the cog:

1. Finds pending suggestions belonging to that user.
2. Attempts to delete their pending suggestion messages.
3. Removes their pending Config records.
4. Retains only aggregate, non-personal statistics.

Approved or rejected messages already posted in community channels are not
tracked after completion and must be managed by community staff.

---

## Failure Safety

Flux Suggestions uses the following result process:

1. Fetch the pending suggestion.
2. Read its votes.
3. Send the result to the archive channel.
4. Save the completed state.
5. Delete the pending message.

This order prevents a pending suggestion from being lost when the approved or
rejected channel is unavailable.

If the result is sent but Config cannot be saved, the cog attempts to delete the
new result message and leaves the pending suggestion open.

If the pending message cannot be deleted after completion, the cog attempts to
edit it into a visibly closed result instead.

---

## Troubleshooting

### The bot will not accept an idea

Ensure the bot has `Manage Messages` in the channel where the user runs:

```text
[p]idea <text>
```

This permission is required to remove the original command message.

---

### The bot cannot add voting reactions

Check the pending channel permissions:

- View Channel
- Send Messages
- Embed Links
- Add Reactions
- Read Message History

Then reset the pending channel:

```text
[p]suggestions set pending #suggestions
```

---

### A pending message was deleted

Recreate it:

```text
[p]suggestions resend <number>
```

---

### A voting reaction was removed

Repair it:

```text
[p]suggestions resend <number>
```

Existing user votes on the other reactions are preserved.

---

### Approval or rejection fails

Check the configured channels:

```text
[p]suggestions settings
```

Set the result channels again if necessary:

```text
[p]suggestions set approved #approved-suggestions
[p]suggestions set rejected #rejected-suggestions
```

The pending suggestion remains open when the result cannot be sent.

---

### The visible count is one higher than the result

The visible Fluxer reaction total includes the bot's seed reaction.

Flux Suggestions excludes its own 👍 and 👎 reactions from the final result.

---

### A user's votes were ignored

If the user reacted with both 👍 and 👎, both of their choices are treated as
ambiguous and excluded.

They should remove one reaction before staff approve or reject the suggestion.

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
[p]reload FluxSuggestions
```

Existing configuration and pending suggestions are retained.

---

## Requirements

- Red Discord Bot patched for Fluxer
- Python 3.8 or newer
- Permission to create embeds and reactions
- No additional Python packages
- No message component support required

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
