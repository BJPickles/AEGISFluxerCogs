# FluxMobile

Automatically monitors GitHub and announces new **Fluxer Mobile** releases.

Designed for the **AEGIS** community and compatible with Red-based Fluxer bots.

---

## Features

- Automatic monitoring of Fluxer Mobile releases
- GitHub prerelease support enabled by default
- Draft release filtering
- APK asset detection
- Optional requirement for an APK before announcing
- Duplicate announcement protection
- Release state saved only after a successful announcement
- Per-guild polling intervals
- Manual release checks
- Test announcements
- Detailed verbose logging
- GitHub API rate-limit diagnostics
- Concurrent-check protection
- Config persistence across cog updates and restarts

---

## Monitored Repository

FluxMobile monitors the official Fluxer Flutter mobile client:

https://github.com/fluxerapp/flutter_client

Releases are retrieved using GitHub's releases collection endpoint so that prereleases can be detected.

---

## Installation

Add the repository containing FluxMobile:

```text
[p]repo add aegis <your_repo_url>
```

Install the cog:

```text
[p]repo install aegis FluxMobile
```

Load the cog:

```text
[p]load FluxMobile
```

Replace `[p]` with your bot's command prefix.

---

## Quick Start

### 1. Set the announcement channel

```text
[p]flux channel #announcements
```

The bot requires the following permissions in the selected channel:

- View Channel
- Send Messages
- Embed Links

### 2. Set the verbose logging channel

```text
[p]flux logs #fluxmobile-logs
```

### 3. Enable verbose logging

```text
[p]flux verbose true
```

### 4. Enable prerelease announcements

Prerelease announcements are enabled by default, but they can be explicitly enabled with:

```text
[p]flux prereleases true
```

This is required while Fluxer Mobile releases are published as beta prereleases.

### 5. Require an APK asset

APK requirements are enabled by default:

```text
[p]flux requireapk true
```

When enabled, FluxMobile waits until at least one APK asset is attached to the GitHub release before announcing it.

### 6. Enable monitoring

```text
[p]flux enable
```

### 7. Check the configuration

```text
[p]flux status
```

### 8. Force an immediate check

```text
[p]flux check
```

---

## Commands

All configuration commands require administrator permissions or the appropriate Red permission override.

| Command | Description |
|---|---|
| `[p]flux` | Display FluxMobile command help |
| `[p]flux enable` | Enable automatic release monitoring |
| `[p]flux disable` | Disable automatic release monitoring |
| `[p]flux channel #channel` | Set the release announcement channel |
| `[p]flux logs #channel` | Set the verbose logging channel |
| `[p]flux verbose true/false` | Enable or disable verbose guild logging |
| `[p]flux prereleases true/false` | Include or exclude GitHub prereleases |
| `[p]flux requireapk true/false` | Require an APK asset before announcing |
| `[p]flux interval <minutes>` | Set the guild's polling interval |
| `[p]flux latest` | Display the newest release allowed by guild policy |
| `[p]flux check` | Force an immediate release check |
| `[p]flux test` | Test an announcement without saving release state |
| `[p]flux status` | Display the current guild configuration |
| `[p]fluxdiag` | Display owner-only cog diagnostics |

---

## Command Details

### Enable monitoring

```text
[p]flux enable
```

Enables automatic monitoring for the current guild.

Enabling monitoring schedules the guild for a release check on the next monitor cycle.

---

### Disable monitoring

```text
[p]flux disable
```

Disables automatic monitoring for the current guild.

Manual commands such as `flux check`, `flux latest`, and `flux test` remain available.

---

### Set the announcement channel

```text
[p]flux channel #announcements
```

Sets the channel where new release embeds will be posted.

Changing the announcement channel schedules the guild for another release check. This allows a previously failed announcement to be retried after the channel has been corrected.

---

### Set the verbose log channel

```text
[p]flux logs #fluxmobile-logs
```

Sets the channel used for detailed release-check logs.

Verbose logging must also be enabled:

```text
[p]flux verbose true
```

---

### Configure verbose logging

Enable verbose logging:

```text
[p]flux verbose true
```

Disable verbose logging:

```text
[p]flux verbose false
```

Verbose logs include:

- Whether the check was manual or scheduled
- Current prerelease and APK policy
- Number of releases returned by GitHub
- Whether the GitHub response came from the network or local cache
- GitHub API rate-limit information
- Number of draft and prerelease entries found
- Releases excluded by guild policy
- The selected release candidate
- Release ID, type, publication time, asset count and APK count
- Previously stored release state
- Duplicate and stale-release decisions
- Announcement success or failure
- Persisted release state

Example:

```text
[INFO] Checking GitHub for new releases (scheduled). Policy: prereleases=included, require_apk=yes.
[INFO] GitHub returned 37 release(s) via GitHub network response. API rate limit: 59/60 remaining.
[INFO] Release classification: drafts=0, prereleases=37, excluded_by_policy=0.
[INFO] Selected newest eligible candidate: v1.0.0-beta.37 (...).
[INFO] Policy decision: candidate has prerelease=True and prerelease announcements are enabled, so the candidate is accepted.
[INFO] New release detected: v1.0.0-beta.37.
[INFO] Announcement sent to #announcements.
[INFO] Persisted announcement state for v1.0.0-beta.37.
```

---

### Configure prereleases

Enable prerelease announcements:

```text
[p]flux prereleases true
```

Disable prerelease announcements:

```text
[p]flux prereleases false
```

Prereleases are included by default because Fluxer Mobile is currently distributed through beta releases.

Draft releases are always ignored, regardless of this setting.

Changing this setting schedules the guild for another release check.

---

### Configure the APK requirement

Require at least one APK asset:

```text
[p]flux requireapk true
```

Allow releases without an APK asset:

```text
[p]flux requireapk false
```

When the requirement is enabled and a new release has no APK asset, FluxMobile:

1. Does not announce the release.
2. Does not mark the release as announced.
3. Records that it is waiting for an APK.
4. Retries the release check later.

This helps avoid announcing a GitHub release before its assets have finished uploading.

---

### Configure the polling interval

```text
[p]flux interval 30
```

The interval is configured independently for each guild.

The minimum interval is:

```text
5 minutes
```

FluxMobile's internal scheduler runs once per minute, but this does not mean every guild makes a GitHub request every minute. A guild is only evaluated when its configured interval is due.

GitHub release data is shared between guilds and temporarily cached to reduce unnecessary API requests.

---

### Display the latest eligible release

```text
[p]flux latest
```

Displays the newest release allowed by the guild's current release policy.

For example:

- If prereleases are enabled, the newest prerelease or full release may be displayed.
- If prereleases are disabled, only full releases are considered.
- Draft releases are never considered.

This command does not announce or save the release.

---

### Force a release check

```text
[p]flux check
```

Immediately checks GitHub using the current guild configuration.

The command will report whether:

- A new release was announced
- The latest release was already announced
- No eligible release was found
- A release is waiting for an APK
- GitHub returned an error
- The announcement failed

A successful announcement updates the stored release state.

---

### Test an announcement

```text
[p]flux test
```

Sends an announcement using the newest eligible release.

A test announcement:

- Uses the configured announcement channel
- Uses the normal release embed
- Respects the prerelease policy
- Does not alter the stored release ID
- Does not mark the release as announced

This makes it safe to use when testing channel permissions or embed formatting.

---

### Display guild status

```text
[p]flux status
```

Displays:

- Whether monitoring is enabled
- Announcement channel
- Prerelease policy
- APK requirement
- Verbose logging channel and state
- Polling interval
- Last announced release
- Last check time
- Last check result
- Background monitor state

---

### Owner diagnostics

```text
[p]fluxdiag
```

This owner-only command displays:

- Cog version
- Monitor state
- Cog readiness
- Number of connected guilds
- GitHub client state
- Last GitHub feed source
- Number of releases returned
- GitHub API rate-limit state
- Last feed retrieval time

---

## Release Selection Behaviour

FluxMobile uses the following decision process:

1. Fetch the GitHub release collection.
2. Parse each release and its assets.
3. Ignore draft releases.
4. Include or exclude prereleases according to guild configuration.
5. Select the newest eligible release by publication time.
6. Compare it with the guild's stored release state.
7. Reject duplicate or older release candidates.
8. Check for an APK when the APK requirement is enabled.
9. Build and send the announcement embed.
10. Save the release state only after the announcement succeeds.

This means a failed announcement is automatically eligible for a later retry.

---

## Duplicate Protection

FluxMobile stores the following information for each guild:

- Last announced GitHub release ID
- Last announced release tag
- Last announced publication time
- Last release-check time
- Last release-check result

The release ID is the primary duplicate-protection value.

Publication time is also stored so that an older stable release is not announced accidentally if prerelease monitoring is disabled after a newer beta has already been announced.

---

## Announcement Embeds

Release announcements include:

- Fluxer Mobile version
- Release type
- GitHub release notes
- Number of APK files
- Links to every APK asset
- GitHub release URL
- Publication timestamp

Universal APK files are preferred when identifying the primary APK, but all detected APK assets are listed in the embed.

---

## Configuration Persistence

FluxMobile uses Red's persistent per-guild Config system.

Stored configuration includes:

- Monitoring enabled state
- Announcement channel
- Verbose logging channel
- Verbose logging state
- Polling interval
- Prerelease policy
- APK requirement
- Last announced release state
- Last check state

Updating or reloading the cog does not intentionally clear existing guild settings.

New configuration options use registered defaults and are added without resetting existing channel IDs, monitoring settings or release state.

---

## Troubleshooting

### A prerelease was not announced

Check that prereleases are enabled:

```text
[p]flux prereleases true
```

Then force a check:

```text
[p]flux check
```

---

### A release is waiting for an APK

Check the current policy:

```text
[p]flux status
```

If the release should be announced without an APK:

```text
[p]flux requireapk false
```

Otherwise, leave the requirement enabled and FluxMobile will retry later.

---

### The announcement failed

Verify the configured channel:

```text
[p]flux status
```

Set it again if necessary:

```text
[p]flux channel #announcements
```

Ensure the bot has:

- View Channel
- Send Messages
- Embed Links

Use a test announcement:

```text
[p]flux test
```

---

### No diagnostic messages are appearing

Set a logging channel:

```text
[p]flux logs #fluxmobile-logs
```

Enable verbose logging:

```text
[p]flux verbose true
```

Force a check:

```text
[p]flux check
```

---

### GitHub requests are failing

Run the owner diagnostics:

```text
[p]fluxdiag
```

Check the verbose log channel for:

- HTTP status codes
- Timeout errors
- GitHub API rate-limit information
- Response parsing errors

FluxMobile automatically retries temporary network errors and retryable GitHub responses.

---

### The configured interval appears to be ignored

Check the current guild configuration:

```text
[p]flux status
```

The scheduler runs once per minute, so a due check may occur up to approximately one minute after the configured interval.

Failed checks and releases waiting for APK assets may be retried sooner than the normal interval.

---

## Updating

Check for cog updates:

```text
[p]cog checkforupdates
```

Install available updates:

```text
[p]cog update
```

Reload FluxMobile:

```text
[p]reload FluxMobile
```

After updating, verify the configuration:

```text
[p]flux status
```

Existing guild settings should remain intact.

---

## Requirements

- A Red-compatible Fluxer bot
- Python with `aiohttp`
- Access to the public GitHub API
- Permission to send messages and embeds in the configured announcement channel

---

## Version

Current cog version:

```text
1.1.0
```

---

## Licence

MIT

---

Developed by **Five** for the **AEGIS** community.
