# Peer-to-Peer File Sharing System

## Overview
This project implements a decentralized Peer-to-Peer (P2P) File Sharing System, drawing inspiration from BitTorrent. It enables multiple peers to exchange file segments over TCP connections, with features like peer prioritization and dynamic choking/unchoking to optimize performance and fairness.


## Getting Started

### Requirements
- Python 3.8+ 
- No external libraries required (uses only built-in modules).

### Running the Application
Start each peer with its assigned ID from the configuration file. For example:
```bash
python peer.py 1001
python peer.py 1002
python peer.py 1003
```


## Configuration Files

### `Common.cfg`
Defines the global settings for the P2P system:
```
NumberOfPreferredNeighbors 3
UnchokingInterval 5
OptimisticUnchokingInterval 10
FileName tree.jpg
FileSize 24301568
PieceSize 1638400
```

### `PeerInfo.cfg`
Lists all peers with their connection details and initial file ownership:
```
1001 localhost 6003 1
1002 localhost 6004 0
1003 localhost 6005 0
```

## Core Components to be Implemented

- **`update_preferred_neighbors()`**: Dynamically selects preferred neighbors based on their download contribution.
- **`optimistically_unchoke_neighbor()`**: Randomly selects one additional peer to unchoke, promoting fairness.
- **`merge_file_pieces()`**: Reassembles the original file after all pieces are received.


## Team Members
**Group 11**
- Satvik LNU - 49893400
- Krishna Niveditha Sudeep Kumar -  63557608
- Bhumi jain - 73961370
  

