"""
peer_connection_handler.py
--------------------------
Manages full-duplex communication with ONE remote peer over a TCP socket.

Each instance runs as its own daemon thread (started by Peer).
It handles every incoming message and generates the appropriate outgoing
response, while also being called externally to send CHOKE / UNCHOKE / HAVE
messages driven by the Peer's scheduling logic.

State diagram (from this peer's perspective)
--------------------------------------------
  choked    = True   → remote peer cannot download from us
  interested = False → we don't want pieces from the remote peer
  After UNCHOKE arrives → choked = False → we immediately try to request a piece
  After PIECE arrives  → update bitfield, announce HAVE to all, try next piece
"""

import os
import random
import struct
import threading

from message import Message
from p2p_messages import P2PMessages


class PeerConnectionHandler(threading.Thread):
    """One thread per active peer connection."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, connection, remote_peer_id: int, peer):
        super().__init__(daemon=True)
        self.socket         = connection
        self.remote_peer_id = remote_peer_id
        self.peer           = peer          # back-reference to the Peer instance

        # Choking / interest state
        self.choked    = True    # default: remote cannot request from us
        self.interested = False  # default: we don't yet know if we want remote's pieces

        # Remote peer's bitfield (which pieces it owns)
        bitfield_bytes = -(-peer.total_pieces // 8)   # ceil(total_pieces / 8)
        self.remote_bitfield = bytearray(bitfield_bytes)

        # Download-rate tracking (reset each unchoking interval)
        self._rate_lock          = threading.Lock()
        self.track_download_rate = 0
        self.pieces_downloaded   = 0
        self.total_bytes_received = 0

        # Prevent marking the remote peer complete more than once
        self._marked_complete = False

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            # Send our own bitfield right after handshake
            self._send_bitfield()

            # Main receive loop — runs until socket is closed
            while True:
                msg = Message.receive(self.socket)
                self._dispatch(msg)

        except (IOError, OSError):
            self.peer.logger.create_log(
                f"Peer [{self.peer.peer_id}] connection closed with [{self.remote_peer_id}]."
            )
            self.peer.remove_client_handler(self.remote_peer_id)

    # ------------------------------------------------------------------
    # Outgoing messages (public — called from Peer's scheduler threads)
    # ------------------------------------------------------------------

    def send_choke(self) -> None:
        """Instruct the remote peer to stop requesting pieces from us."""
        Message(P2PMessages.CHOKE).send_message(self.socket)
        self.choked = True
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] is choking [{self.remote_peer_id}]."
        )
        self._log_status()

    def send_unchoke(self) -> None:
        """Allow the remote peer to request pieces from us again."""
        Message(P2PMessages.UNCHOKE).send_message(self.socket)
        self.choked = False
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] is unchoking [{self.remote_peer_id}]."
        )
        self._log_status()
        self._request_piece()   # Immediately take advantage of being unchoked

    def send_have(self, piece_index: int) -> None:
        """Broadcast to this peer that we just downloaded piece *piece_index*."""
        payload = struct.pack(">I", piece_index)
        Message(P2PMessages.HAVE, payload).send_message(self.socket)
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] sent HAVE[{piece_index}] to [{self.remote_peer_id}]."
        )

    # ------------------------------------------------------------------
    # Accessors used by Peer's scheduling / selection logic
    # ------------------------------------------------------------------

    def is_interested(self) -> bool:
        return self.interested

    def is_choked(self) -> bool:
        return self.choked

    def get_remote_peer_id(self) -> int:
        return self.remote_peer_id

    def get_track_download_rate(self) -> int:
        """Return pieces-downloaded count since last call, then reset it to zero."""
        with self._rate_lock:
            rate = self.track_download_rate
            self.track_download_rate = 0
        return rate

    # ------------------------------------------------------------------
    # Message dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, msg: Message) -> None:
        t = msg.get_type()
        if   t == P2PMessages.BITFIELD:      self._on_bitfield(msg.get_payload())
        elif t == P2PMessages.INTERESTED:    self._on_interested()
        elif t == P2PMessages.NOT_INTERESTED: self._on_not_interested()
        elif t == P2PMessages.REQUEST:       self._on_request(msg.get_payload())
        elif t == P2PMessages.PIECE:         self._on_piece(msg.get_payload())
        elif t == P2PMessages.HAVE:          self._on_have(msg.get_payload())
        elif t == P2PMessages.CHOKE:         self._on_choke()
        elif t == P2PMessages.UNCHOKE:       self._on_unchoke()
        else:
            print(f"[{self.peer.peer_id}] Unknown message type from {self.remote_peer_id}")

    # ------------------------------------------------------------------
    # Incoming message handlers
    # ------------------------------------------------------------------

    def _on_bitfield(self, payload: bytes) -> None:
        """Store the remote peer's bitfield and send INTERESTED / NOT_INTERESTED."""
        self.remote_bitfield[:len(payload)] = payload
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] received BITFIELD from [{self.remote_peer_id}]."
        )

        # Are we interested in any piece the remote has that we lack?
        want = any(
            not self.peer.has_piece(i) and self._remote_has(i)
            for i in range(self.peer.total_pieces)
        )
        if want:
            self._send_interested()
            self.interested = True
        else:
            self._send_not_interested()
            self.interested = False

        # If the remote already has every piece, mark it complete immediately
        if self._remote_piece_count() == self.peer.total_pieces:
            self.peer.mark_peer_complete(self.remote_peer_id)

        self._log_status()

    def _on_interested(self) -> None:
        self.interested = True
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] received INTERESTED from [{self.remote_peer_id}]."
        )
        self._log_status()

    def _on_not_interested(self) -> None:
        self.interested = False
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] received NOT_INTERESTED from [{self.remote_peer_id}]."
        )
        self._log_status()

    def _on_request(self, payload: bytes) -> None:
        """Send the requested piece if we have it and the peer is unchoked."""
        piece_index = struct.unpack(">I", payload)[0]
        if not self.choked and self.peer.has_piece(piece_index):
            self._send_piece(piece_index)
            self.peer.logger.create_log(
                f"Peer [{self.peer.peer_id}] sent piece [{piece_index}] to [{self.remote_peer_id}]."
            )

    def _on_piece(self, payload: bytes) -> None:
        """Save received piece, update bookkeeping, propagate HAVE, request next piece."""
        piece_index = struct.unpack(">I", payload[:4])[0]
        piece_data  = payload[4:]

        # Persist to disk
        self._save_piece(piece_index, piece_data)

        # Update this peer's bitfield and check for completion
        self.peer.update_bitfield(piece_index)
        self.peer.check_and_set_completion()

        # Advertise to all other handlers that we now own this piece
        for handler in self.peer.get_client_handlers():
            handler.send_have(piece_index)

        # Update rate counters
        with self._rate_lock:
            self.pieces_downloaded    += 1
            self.track_download_rate  += 1
            self.total_bytes_received += len(piece_data)

        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] downloaded piece [{piece_index}] from "
            f"[{self.remote_peer_id}]. Have {self._local_piece_count()} / "
            f"{self.peer.total_pieces} pieces."
        )
        self.peer.logger.create_log(
            f"Total bytes received from [{self.remote_peer_id}]: "
            f"{self.total_bytes_received} bytes."
        )

        # Immediately try to fetch the next needed piece
        self._request_piece()

    def _on_have(self, payload: bytes) -> None:
        """Update the remote bitfield and express interest if the new piece is useful."""
        piece_index = struct.unpack(">I", payload)[0]
        self._set_remote_has(piece_index)
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] received HAVE[{piece_index}] from [{self.remote_peer_id}]."
        )

        if not self.peer.has_piece(piece_index):
            self._send_interested()
            self.interested = True

        # Check again whether the remote now has a full copy
        if self._remote_piece_count() == self.peer.total_pieces:
            self.peer.mark_peer_complete(self.remote_peer_id)

        self._log_status()

    def _on_choke(self) -> None:
        self.choked = True
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] was choked by [{self.remote_peer_id}]."
        )
        self._log_status()

    def _on_unchoke(self) -> None:
        self.choked = False
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] was unchoked by [{self.remote_peer_id}]."
        )
        self._log_status()
        self._request_piece()

    # ------------------------------------------------------------------
    # Private send helpers
    # ------------------------------------------------------------------

    def _send_bitfield(self) -> None:
        bf = self.peer.get_bitfield()
        if bf:
            Message(P2PMessages.BITFIELD, bf).send_message(self.socket)
            self.peer.logger.create_log(
                f"Peer [{self.peer.peer_id}] sent BITFIELD to [{self.remote_peer_id}]."
            )

    def _send_interested(self) -> None:
        Message(P2PMessages.INTERESTED).send_message(self.socket)
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] sent INTERESTED to [{self.remote_peer_id}]."
        )

    def _send_not_interested(self) -> None:
        Message(P2PMessages.NOT_INTERESTED).send_message(self.socket)
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] sent NOT_INTERESTED to [{self.remote_peer_id}]."
        )

    def _send_piece(self, piece_index: int) -> None:
        piece_path = os.path.join(self.peer.peer_directory, f"piece_{piece_index}")
        with open(piece_path, "rb") as f:
            data = f.read()
        payload = struct.pack(">I", piece_index) + data
        Message(P2PMessages.PIECE, payload).send_message(self.socket)

    def _request_piece(self) -> None:
        """Pick a random piece we need from the remote and send a REQUEST."""
        if self.choked:
            return
        missing = [
            i for i in range(self.peer.total_pieces)
            if not self.peer.has_piece(i) and self._remote_has(i)
        ]
        if not missing:
            self._send_not_interested()
            self.interested = False
            return
        choice = random.choice(missing)
        Message(P2PMessages.REQUEST, struct.pack(">I", choice)).send_message(self.socket)
        self.peer.logger.create_log(
            f"Peer [{self.peer.peer_id}] sent REQUEST[{choice}] to [{self.remote_peer_id}]."
        )

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def _save_piece(self, piece_index: int, data: bytes) -> None:
        path = os.path.join(self.peer.peer_directory, f"piece_{piece_index}")
        with open(path, "wb") as f:
            f.write(data)

    # ------------------------------------------------------------------
    # Remote bitfield helpers
    # ------------------------------------------------------------------

    def _remote_has(self, index: int) -> bool:
        byte_i = index // 8
        bit_i  = 7 - (index % 8)
        return bool(self.remote_bitfield[byte_i] & (1 << bit_i))

    def _set_remote_has(self, index: int) -> None:
        byte_i = index // 8
        bit_i  = 7 - (index % 8)
        self.remote_bitfield[byte_i] |= (1 << bit_i)

    def _remote_piece_count(self) -> int:
        return sum(1 for i in range(self.peer.total_pieces) if self._remote_has(i))

    def _local_piece_count(self) -> int:
        return sum(1 for i in range(self.peer.total_pieces) if self.peer.has_piece(i))

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_status(self) -> None:
        self.peer.logger.create_log(
            f"  Neighbor [{self.remote_peer_id}]: "
            f"{'Choked' if self.choked else 'Unchoked'} | "
            f"{'Interested' if self.interested else 'Not Interested'}"
        )
