# FluxMobile

Automatically announces new **Fluxer Mobile** Android releases from GitHub.

Designed for the **AEGIS** community.

---

## Features

- Automatic monitoring of Fluxer Mobile releases
- Announces new APK releases
- Duplicate announcement protection
- Manual release checks
- Test announcements
- Verbose logging
- Diagnostics
- Config persistence across restarts

---

## Installation

```text
[p]repo add aegis <your_repo_url>
[p]repo install aegis FluxMobile
[p]load FluxMobile
```

---

## Configuration

Set the announcement channel.

```text
[p]flux channel #announcements
```

Set the verbose log channel.

```text
[p]flux logs #fluxmobile-logs
```

Enable verbose logging.

```text
[p]flux verbose true
```

Enable monitoring.

```text
[p]flux enable
```

---

## Commands

| Command | Description |
|---------|-------------|
| `[p]flux enable` | Enable monitoring |
| `[p]flux disable` | Disable monitoring |
| `[p]flux channel` | Set announcement channel |
| `[p]flux logs` | Set verbose log channel |
| `[p]flux verbose` | Toggle verbose logging |
| `[p]flux interval` | Set polling interval |
| `[p]flux latest` | Display latest release |
| `[p]flux check` | Force an immediate check |
| `[p]flux test` | Test an announcement |
| `[p]flux status` | Display configuration |
| `[p]fluxdiag` | Owner diagnostics |

---

## Monitored Repository

https://github.com/fluxerapp/flutter_client

---

## Licence

MIT

---

Developed by **Five** for the **AEGIS** community.
