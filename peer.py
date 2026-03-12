
import math
import os
import random
import socket
import struct
import sys
import threading
import time

from logger import Logger
from peer_configuration import PeerConfiguration
# from peer_connection_handler import PeerConnectionHandler

# ---------------------------------------------------------------------------
# Config file names (resolved relative to cwd at runtime)
# ---------------------------------------------------------------------------
COMMON_CONFIG   = "Common.cfg"
PEER_INFO_CONFIG = "PeerInfo.cfg"


# ---------------------------------------------------------------------------
# Helper: repeating daemon timer
# ---------------------------------------------------------------------------

def _repeat(func, interval: float) -> threading.Thread:
    """Call *func* every *interval* seconds in a daemon thread (initial delay = interval)."""
    def _loop():
        time.sleep(interval)
        while True:
            try:
                func()
            except Exception as exc:
                print(f"[scheduler] Error in {func.__name__}: {exc}", file=sys.stderr)
            time.sleep(interval)
    t = threading.Thread(target=_loop, daemon=True, name=f"scheduler-{func.__name__}")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Peer
# ---------------------------------------------------------------------------

class Peer:

    def __init__(self, peer_id: str):
        self.peer_id         = str(peer_id)
        self.peer_directory  = os.path.join(os.getcwd(), f"peer_{peer_id}")
        self.logger          = Logger(f"log_peer_{peer_id}.log")

        # Populated by _parse_common_config()
        self.file_name:                   str = None
        self.file_size:                   int = None
        self.piece_size:                  int = None
        self.total_pieces:                int = None
        self.number_of_preferred_neighbors: int = None
        self.unchoking_interval:          int = None
        self.optimistic_unchoking_interval: int = None

        # Populated by _parse_peer_info_config()
        self.host_name:     str  = None
        self.port:          int  = None
        self.peer_has_file: bool = False
        self.peers_info:    dict = {}   # peer_id(int) → PeerConfiguration

        # Runtime state — all guarded by _lock
        self.bitfield:           bytearray = None
        self._has_complete_file: bool      = False


        self.preferred_neighbors:           set = set()
        self.optimistic_unchoked_neighbor:  int = -1
            # Removed redundant attributes related to PeerConnectionHandler and completion tracking

        # Reentrant lock: guards all mutable shared fields above
        self._lock          = threading.RLock()
        self._server_socket: socket.socket = None
        self._server_ready  = threading.Event()

    # ======================================================================
    # Public interface (called by PeerConnectionHandler and the scheduler)
    # ======================================================================

    def get_bitfield(self) -> bytes:
        """Return a snapshot of the current bitfield (thread-safe copy)."""
        with self._lock:
            return bytes(self.bitfield) if self.bitfield is not None else None

    def has_piece(self, piece_index: int) -> bool:
        """True if we currently own piece *piece_index*."""
        with self._lock:
            byte_i = piece_index // 8
            bit_i  = piece_index % 8
            return bool(self.bitfield[byte_i] & (1 << (7 - bit_i)))

    def update_bitfield(self, piece_index: int) -> None:
        """Mark piece *piece_index* as owned in our bitfield."""
        with self._lock:
            byte_i = piece_index // 8
            bit_i  = piece_index % 8
            self.bitfield[byte_i] |= (1 << (7 - bit_i))

    def has_complete_file(self) -> bool:
        with self._lock:
            return self._has_complete_file

    def get_client_handlers(self) -> list:
        """Return a snapshot list of all active PeerConnectionHandlers."""
        with self._lock:
            return list(self.client_handlers.values())

    def remove_client_handler(self, remote_peer_id: int) -> None:
        with self._lock:
            self.client_handlers.pop(remote_peer_id, None)
            self.neighbor_sockets.pop(remote_peer_id, None)

    def mark_peer_complete(self, remote_peer_id: int) -> None:
        """Called by a handler when it determines the remote peer has every piece."""
        with self._lock:
            self.peer_completion_map[remote_peer_id] = True
        self.logger.create_log(
            f"Peer [{self.peer_id}] marked peer [{remote_peer_id}] as complete."
        )

    def check_and_set_completion(self) -> None:
        """After a new piece arrives, check if we now have everything and merge."""
        with self._lock:
            if not self._has_complete_file and self._all_pieces_owned():
                self._has_complete_file = True
                self.logger.create_log(
                    f"Peer [{self.peer_id}] has downloaded the complete file."
                )
                try:
                    self._merge_file_pieces()
                except OSError as exc:
                    print(f"Error merging file pieces: {exc}", file=sys.stderr)

    # ======================================================================
    # Start-up
    # ======================================================================

    def start(self) -> None:
        """Parse configs, initialise state, start server + peer connections."""
        self._parse_common_config()
        self._parse_peer_info_config()

        # Make sure the peer's working directory exists
        os.makedirs(self.peer_directory, exist_ok=True)

        # Compute total number of pieces (ceiling division)
        self.total_pieces   = math.ceil(self.file_size / self.piece_size)
        bitfield_bytes       = math.ceil(self.total_pieces / 8)
        self.bitfield        = bytearray(bitfield_bytes)

        if self.peer_has_file:
            # Seeder: all bits set, split the file into pieces if not done yet
            for i in range(bitfield_bytes):
                self.bitfield[i] = 0xFF
            with self._lock:
                self._has_complete_file = True
            src = os.path.join(self.peer_directory, self.file_name)
            if not os.path.isfile(src):
                raise FileNotFoundError(
                    f"Seeder file not found: {src}\n"
                    f"Place {self.file_name!r} inside {self.peer_directory!r} before starting."
                )
            self._split_file_into_pieces(src)
        else:
            # Leecher: no pieces yet
            for i in range(bitfield_bytes):
                self.bitfield[i] = 0x00

        # Start listening for inbound connections
        self._start_server()
        # Block briefly until the socket is actually bound
        self._server_ready.wait(timeout=5.0)

        # Connect outbound to every peer with a lower ID
        self._connect_to_peers()

        # Periodic scheduling

        # Background completion watcher
            # Removed redundant scheduling and completion watcher

    # ======================================================================
    # Configuration parsing
    # ======================================================================

    def _parse_common_config(self) -> None:
        cfg = {}
        with open(COMMON_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2:
                    cfg[parts[0]] = parts[1]

        self.number_of_preferred_neighbors   = int(cfg["NumberOfPreferredNeighbors"])
        self.unchoking_interval              = int(cfg["UnchokingInterval"])
        self.optimistic_unchoking_interval   = int(cfg["OptimisticUnchokingInterval"])
        self.file_name                        = cfg["FileName"]
        self.file_size                        = int(cfg["FileSize"])
        self.piece_size                       = int(cfg["PieceSize"])

        self.logger.create_log(
            f"Parsed {COMMON_CONFIG}: neighbors={self.number_of_preferred_neighbors}, "
            f"unchoke_interval={self.unchoking_interval}s, "
            f"opt_unchoke_interval={self.optimistic_unchoking_interval}s, "
            f"file={self.file_name} ({self.file_size} bytes), "
            f"piece_size={self.piece_size} bytes"
        )

    def _parse_peer_info_config(self) -> None:
        self.logger.create_log(f"Parsing {PEER_INFO_CONFIG} ...")
        found_self = False
        with open(PEER_INFO_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                tokens = line.strip().split()
                if not tokens:
                    continue
                pid          = int(tokens[0])
                host_name    = tokens[1]
                port         = int(tokens[2])
                has_file     = tokens[3] == "1"
                cfg          = PeerConfiguration(pid, host_name, port, has_file)
                self.peers_info[pid] = cfg
                self.logger.create_log(
                    f"  Loaded peer: id={pid}, host={host_name}, port={port}, "
                    f"has_file={has_file}"
                )
                if str(pid) == self.peer_id:
                    self.host_name     = host_name
                    self.port          = port
                    self.peer_has_file = has_file
                    found_self         = True

        if not found_self:
            raise ValueError(
                f"Peer ID {self.peer_id!r} not found in {PEER_INFO_CONFIG}"
            )
        self.logger.create_log(
            f"This peer: id={self.peer_id}, host={self.host_name}, "
            f"port={self.port}, has_file={self.peer_has_file}"
        )

    # ======================================================================
    # TCP networking
    # ======================================================================

    def _start_server(self) -> None:
        """Bind the server socket and accept inbound connections in a daemon thread."""
        def _accept_loop():
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind(("", self.port))
            self._server_socket.listen(10)
            self._server_ready.set()
            self.logger.create_log(
                f"Peer [{self.peer_id}] listening on port {self.port} ..."
            )
            while True:
                try:
                    conn, addr = self._server_socket.accept()
                    threading.Thread(
                        target=self._handle_inbound,
                        args=(conn,),
                        daemon=True,
                        name=f"inbound-{addr}",
                    ).start()
                except OSError:
                    break   # socket closed

        threading.Thread(target=_accept_loop, daemon=True, name="server").start()

    def _handle_inbound(self, conn: socket.socket) -> None:
        """Perform handshake for an accepted connection, then start a handler thread."""
        try:
            remote_id_str = self._do_handshake(conn)
            remote_id     = int(remote_id_str)
            self.logger.create_log(
                f"TCP connection: P{remote_id} → P{self.peer_id} (inbound)"
            )
            with self._lock:
                self.neighbor_sockets[remote_id] = conn
        except (IOError, OSError) as exc:
            print(f"[{self.peer_id}] Error on inbound connection: {exc}", file=sys.stderr)

    def _connect_to_peers(self) -> None:
        """Initiate outbound TCP connections to all peers listed before us in PeerInfo.cfg."""
        for pid in sorted(self.peers_info.keys()):
            if pid == int(self.peer_id):
                break   # only connect to peers with lower IDs
            cfg = self.peers_info[pid]
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((cfg.host_name, cfg.port_number))
                self._do_handshake(s)
                self.logger.create_log(
                    f"TCP connection: P{self.peer_id} → P{pid} (outbound)"
                )
                with self._lock:
                    self.neighbor_sockets[pid] = s
            except (IOError, OSError) as exc:
                print(f"[{self.peer_id}] Cannot connect to peer {pid}: {exc}", file=sys.stderr)

    def _do_handshake(self, sock: socket.socket) -> str:
        """
        Exchange the 32-byte handshake and return the remote peer's ID as a string.

        Format
        ------
          [18 bytes] b'P2PFILESHARINGPROJ'
          [10 bytes] 0x00 padding
          [ 4 bytes] peer-id (big-endian uint32)
        """
        header  = b"P2PFILESHARINGPROJ"
        padding = b"\x00" * 10
        my_id   = struct.pack(">I", int(self.peer_id))
        sock.sendall(header + padding + my_id)
        self.logger.create_log(
            f"Peer [{self.peer_id}] sent handshake."
        )

        buf = b""
        while len(buf) < 32:
            chunk = sock.recv(32 - len(buf))
            if not chunk:
                raise IOError("Connection closed during handshake")
            buf += chunk

        if buf[:18] != b"P2PFILESHARINGPROJ":
            raise IOError(f"Bad handshake header: {buf[:18]!r}")

        remote_id = struct.unpack(">I", buf[28:32])[0]
        self.logger.create_log(
            f"Peer [{self.peer_id}] received handshake from peer [{remote_id}]."
        )
        return str(remote_id)

    # ======================================================================
    # Choke / unchoke algorithm (BitTorrent tit-for-tat)
    # ======================================================================

    def _update_preferred_neighbors(self) -> None:
        """
        Select the top K preferred neighbours based on download contribution
        (or randomly for seeders) and send CHOKE / UNCHOKE accordingly.
        Runs every *unchoking_interval* seconds.
        """
        try:
            with self._lock:
                handlers  = dict(self.client_handlers)
                has_file  = self._has_complete_file

            interested = [h for h in handlers.values() if h.is_interested()]
            if not interested:
                with self._lock:
                    self.preferred_neighbors.clear()
                self.logger.create_log(
                    f"Peer [{self.peer_id}] has no interested neighbours — nothing to do."
                )
                return

            self.logger.create_log(
                f"Peer [{self.peer_id}] recalculating preferred neighbours "
                f"(interval={self.unchoking_interval}s) ..."
            )

            # Seeder → random rotation; Leecher → highest download rate wins
            if has_file:
                random.shuffle(interested)
            else:
                interested.sort(key=lambda h: h.get_track_download_rate(), reverse=True)

            top_k   = set(h.get_remote_peer_id() for h in interested[: self.number_of_preferred_neighbors])

            with self._lock:
                self.preferred_neighbors = top_k

            for h in handlers.values():
                if h.get_remote_peer_id() in top_k:
                    if h.is_choked():
                        h.send_unchoke()
                else:
                    if not h.is_choked():
                        h.send_choke()

            self.logger.create_log(
                f"Peer [{self.peer_id}] preferred neighbours: "
                f"[{', '.join(str(p) for p in sorted(top_k))}]"
            )
            self._log_all_neighbour_states(handlers)

        except Exception as exc:
            print(f"[{self.peer_id}] Error in _update_preferred_neighbors: {exc}", file=sys.stderr)






            # Removed redundant unchoke logic

    def _log_all_neighbour_states(self, handlers: dict) -> None:
        lines = [f"Peer [{self.peer_id}] neighbour states:"]
        for h in handlers.values():
            lines.append(
                f"  [{h.get_remote_peer_id()}] "
                f"{'choked' if h.is_choked() else 'unchoked'} | "
                f"{'interested' if h.is_interested() else 'not-interested'}"
            )
        self.logger.create_log("\n".join(lines))

    # ======================================================================
    # Completion detection
    # ======================================================================

    def _watch_completion(self) -> None:
        """
        Daemon thread: poll until every peer we know about has the complete file,
        then call sys.exit(0).
        """
        while True:
            try:
                with self._lock:
                    cmap      = dict(self.peer_completion_map)
                    num_peers = len(self.peers_info)
                    has_file  = self._has_complete_file

                expected = num_peers - 1        # all others except ourselves

                if len(cmap) < expected:
                    self.logger.create_log(
                        f"Peer [{self.peer_id}] waiting for connections "
                        f"({len(cmap)}/{expected} known so far) ..."
                    )
                    time.sleep(2)
                    continue

                if has_file and all(cmap.values()):
                    self.logger.create_log(
                        f"Peer [{self.peer_id}]: all peers have the complete file. "
                        f"Shutting down."
                    )
                    sys.exit(0)

            except SystemExit:
                raise
            except Exception as exc:
                print(f"[{self.peer_id}] Error in _watch_completion: {exc}", file=sys.stderr)

            time.sleep(2)

    # ======================================================================
    # Bitfield helpers
    # ======================================================================

    def _all_pieces_owned(self) -> bool:
        """True if every piece bit is set (caller must hold _lock or use RLock)."""
        return all(self.has_piece(i) for i in range(self.total_pieces))

    # ======================================================================
    # File I/O
    # ======================================================================

    def _split_file_into_pieces(self, src_path: str) -> None:
        """Split the source file into fixed-size piece files inside peer_directory."""
        idx = 0
        with open(src_path, "rb") as f:
            while True:
                chunk = f.read(self.piece_size)
                if not chunk:
                    break
                dest = os.path.join(self.peer_directory, f"piece_{idx}")
                with open(dest, "wb") as pf:
                    pf.write(chunk)
                idx += 1
        self.logger.create_log(
            f"Peer [{self.peer_id}] split {self.file_name!r} into {idx} pieces."
        )

    def _merge_file_pieces(self) -> None:
        """Concatenate all piece files in order to reconstruct the original file."""
        dest = os.path.join(self.peer_directory, self.file_name)
        with open(dest, "wb") as out:
            for i in range(self.total_pieces):
                piece_path = os.path.join(self.peer_directory, f"piece_{i}")
                with open(piece_path, "rb") as pf:
                    out.write(pf.read())
        self.logger.create_log(
            f"Peer [{self.peer_id}] merged pieces → {dest!r}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python peer.py <peerID>", file=sys.stderr)
        sys.exit(1)

    peer = Peer(sys.argv[1])
    peer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\nPeer {sys.argv[1]} shutting down.")


if __name__ == "__main__":
    main()
