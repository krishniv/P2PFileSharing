# P2P File Sharing System — Python

A BitTorrent-inspired peer-to-peer file sharing system written entirely in Python 3.
Inspired by the original Java implementation at [SaiPande/P2PFileSharing](https://github.com/SaiPande/P2PFileSharing).

---

## Features

| Feature | Details |
|---------|---------|
| Protocol | Custom BitTorrent-style over TCP |
| Piece transfer | Fixed-size chunks (configurable) |
| Peer selection | Tit-for-tat choke / unchoke |
| Fairness | Optimistic unchoking every N seconds |
| Concurrency | One thread per peer connection |
| Logging | Timestamped file + stdout logs |
| Dependencies | **Python 3 standard library only** — no pip installs needed |

---

## Project Structure

```
P2PFileSharing_Python/
├── peer.py                    # Main peer node & entry point
├── peer_connection_handler.py # Per-connection message handler (one thread each)
├── message.py                 # Wire-format serialisation / deserialisation
├── p2p_messages.py            # Enum of all 8 message types
├── peer_configuration.py      # Data class for a single peer's config
├── logger.py                  # Thread-safe timestamped logger
├── Common.cfg                 # Global parameters (intervals, file info)
├── PeerInfo.cfg               # Network topology (peer IDs, hosts, ports)
├── peer_1001/                 # Data directory for peer 1001
├── peer_1002/                 # Data directory for peer 1002
└── peer_1003/                 # Data directory for peer 1003
```

---

## Quick Start

### 1. Place the shared file

Copy the file you want to distribute into the **seeder's** directory:

```bash
cp /path/to/tree.jpg  peer_1001/tree.jpg
```

> The seeder is the peer whose fourth column in `PeerInfo.cfg` is `1`.

### 2. Edit configuration (if needed)

**`Common.cfg`**

```
NumberOfPreferredNeighbors 3       # K peers to unchoke at a time
UnchokingInterval 5                # Seconds between neighbour recalculations
OptimisticUnchokingInterval 10     # Seconds between optimistic unchokes
FileName tree.jpg                  # File to share
FileSize 24301568                  # File size in bytes
PieceSize 1638400                  # ~1.6 MB per piece
```

**`PeerInfo.cfg`** — one line per peer:

```
<PeerID>  <Hostname>  <Port>  <HasFile(1/0)>

1001 localhost 6003 1    ← seeder (has the file)
1002 localhost 6004 0    ← leecher
1003 localhost 6005 0    ← leecher
```

### 3. Run (start seeder first, then leechers)

Open three separate terminals **from the `P2PFileSharing_Python/` directory**:

```bash
# Terminal 1 — seeder
python peer.py 1001

# Terminal 2 — leecher
python peer.py 1002

# Terminal 3 — leecher
python peer.py 1003
```

Each peer exits automatically once every peer in the network has the complete file.

---

## Protocol Details

### Handshake (32 bytes)

```
[18 bytes] b'P2PFILESHARINGPROJ'   ← fixed magic header
[10 bytes] 0x00 × 10               ← reserved / padding
[ 4 bytes] peer_id (uint32 BE)     ← sender's numeric ID
```

### Message Wire Format

```
[4 bytes] length (uint32 BE)  ← 1 + len(payload)
[1 byte ] type                ← one of 0-7 (see table below)
[N bytes] payload             ← optional, type-dependent
```

### Message Types

| Type | ID | Payload | Description |
|------|----|---------|-------------|
| CHOKE | 0 | — | Stop sending requests to the sender |
| UNCHOKE | 1 | — | You may resume sending requests |
| INTERESTED | 2 | — | I want pieces you have |
| NOT_INTERESTED | 3 | — | I don't need any of your pieces right now |
| HAVE | 4 | `uint32` piece index | I just downloaded this piece |
| BITFIELD | 5 | bitmask bytes | My current piece ownership map |
| REQUEST | 6 | `uint32` piece index | Please send me this piece |
| PIECE | 7 | `uint32` index + raw data | Here is the piece data |

---

## Algorithms

### Preferred-Neighbour Selection (every `UnchokingInterval` seconds)

```
if seeder:
    shuffle interested peers, pick top K  # fair random rotation
else:
    sort interested peers by download rate, pick top K  # tit-for-tat

Send UNCHOKE to selected, CHOKE to the rest.
```

### Optimistic Unchoking (every `OptimisticUnchokingInterval` seconds)

```
Pick one random choked+interested peer outside the preferred set.
Send UNCHOKE to it.

Purpose: let new / slow peers prove themselves; prevent starvation.
```

### Piece Selection

Random selection from `{pieces remote has} ∩ {pieces we lack}`.
Prevents all peers from racing for the same piece simultaneously.

---

## Log Files

Each peer writes to its own log file (also mirrored to stdout):

```
log_peer_1001.log
log_peer_1002.log
log_peer_1003.log
```

Sample log line:
```
[Thu Mar 06 14:22:01 2026]: Peer [1002] downloaded piece [3] from [1001]. Have 1 / 15 pieces.
```

---

## Adding More Peers

1. Add a new line to `PeerInfo.cfg`:
   ```
   1004 localhost 6006 0
   ```
2. Create the directory:
   ```bash
   mkdir peer_1004
   ```
3. Run:
   ```bash
   python peer.py 1004
   ```

---

## Requirements

- **Python 3.8+**
- No external packages — uses only the Python standard library.
