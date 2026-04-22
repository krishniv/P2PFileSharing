# Peer-to-Peer File Sharing System

## Video Demo

**Video URL:** `[INSERT YOUR ONEDRIVE/CANVAS VIDEO URL HERE]`

> The video demonstrates all features as listed in the project rubric, including config parsing,
> TCP connection setup, handshake, bitfield, choke/unchoke, optimistic unchoking, and file assembly.

---

## Group Information

**Group 11 — COP5518 Computer Networks**

| Member | Contributions |
|--------|--------------|
| Satvik | Core peer logic (`peer.py`), handshake implementation, preferred neighbor selection (choke/unchoke), optimistic unchoking scheduler, completion detection and shutdown logic |
| Bhumi Jain | Message handling (`message.py`, `peer_connection_handler.py`), bitfield exchange, request/have/piece message processing, file piece assembly |
| Krishna | Configuration parsing (`peer_config.py`, `Common.cfg`, `PeerInfo.cfg` reading), logging infrastructure (`logger.py`), testing and debugging |

---

## Overview

This project implements a decentralized **Peer-to-Peer (P2P) File Sharing System** modeled after BitTorrent. Multiple peers exchange file pieces over TCP connections, using choking/unchoking algorithms to optimize bandwidth and promote fairness.

### Implemented Features (Rubric Coverage)

| Rubric Item | Status |
|-------------|--------|
| Reads `Common.cfg` / `PeerInfo.cfg` on startup | ✅ |
| Each peer connects via TCP to all prior peers | ✅ |
| Peer terminates when ALL peers have complete file | ✅ |
| Handshake message on every connection | ✅ |
| Bitfield exchange after handshake | ✅ |
| Interested / Not Interested messages | ✅ |
| k preferred neighbors unchoked every p seconds | ✅ |
| Optimistically unchoked neighbor every m seconds | ✅ |
| Request / Have / Piece message exchange | ✅ |
| Bitfield updated on receiving 'have' | ✅ |
| File reassembled from pieces after download | ✅ |
| Graceful shutdown after all peers complete | ✅ |

---

## Configuration Files

### `Common.cfg`
```
NumberOfPreferredNeighbors 3
UnchokingInterval 5
OptimisticUnchokingInterval 10
FileName tree.jpg
FileSize 24301568
PieceSize 1638400
```

- **NumberOfPreferredNeighbors** (`k`): Number of peers to unchoke each interval
- **UnchokingInterval** (`p`): Seconds between preferred-neighbor recalculations
- **OptimisticUnchokingInterval** (`m`): Seconds between optimistic unchoke changes
- **FileName**: File being shared
- **FileSize**: Total file size in bytes
- **PieceSize**: Size of each piece in bytes → produces `ceil(FileSize/PieceSize)` = 15 pieces

### `PeerInfo.cfg`
```
1001 localhost 6003 1
1002 localhost 6004 0
1003 localhost 6005 0
```

Format: `PeerID Hostname Port HasFile`
- `1`: peer starts with the complete file
- `0`: peer starts with no pieces and must download

---

## Requirements

- Python 3.8+
- No external libraries (uses only built-in modules: `socket`, `threading`, `struct`, `os`, `math`, `random`)

---

## Running the Application

Start peers **in order** (peer 1001 first, then 1002, then 1003). Each peer must be started in a separate terminal from the project directory:

```bash
# Terminal 1
python peer.py 1001

# Terminal 2 (after 1001 is running)
python peer.py 1002

# Terminal 3 (after 1001 and 1002 are running)
python peer.py 1003
```

> **Important:** `peer_1001/` must contain `tree.jpg` before starting peer 1001. Peer 1001 will split it into 15 pieces automatically.

### Log Files

Each peer writes a log file (`log_peer_XXXX.log`) showing all protocol events. These logs are used to verify rubric compliance.

---

## Project Structure

```
P2PFileSharing/
├── peer.py                   # Main peer process: startup, scheduling, TCP server
├── peer_connection_handler.py # Per-connection thread: message handling, piece exchange
├── message.py                # Message framing/parsing (all 8 message types)
├── peer_config.py            # PeerConfiguration data class
├── logger.py                 # Thread-safe timestamped logger
├── Common.cfg                # Shared configuration
├── PeerInfo.cfg              # Peer registry
├── peer_1001/                # Peer 1001's working directory (has tree.jpg)
├── peer_1002/                # Peer 1002's working directory (empty)
└── peer_1003/                # Peer 1003's working directory (empty)
```

---

## Protocol Summary

```
[Connection established]
    → Peer A sends HANDSHAKE to Peer B
    → Peer B sends HANDSHAKE to Peer A
    → Each sends BITFIELD
    → Each sends INTERESTED or NOT_INTERESTED based on remote bitfield

[Every p seconds]
    → Peer recalculates k preferred neighbors by download rate
    → Sends UNCHOKE to new preferred neighbors
    → Sends CHOKE to demoted neighbors

[Every m seconds]
    → Peer randomly selects one choked+interested neighbor as optimistic unchoke

[When unchoked by a neighbor]
    → Peer sends REQUEST for a random needed piece

[Upon receiving REQUEST]
    → If unchoked: send PIECE data

[Upon receiving PIECE]
    → Save piece, update bitfield, send HAVE to all neighbors
    → Request next needed piece

[When all peers complete]
    → Each peer detects completion and exits gracefully
```
