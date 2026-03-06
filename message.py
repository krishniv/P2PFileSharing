"""
message.py
----------
Serialisation / deserialisation of the BitTorrent-style P2P wire protocol.

Wire format
-----------
  ┌─────────────────────────┬────────────┬──────────────────┐
  │  4 bytes (big-endian)   │  1 byte    │  0..N bytes      │
  │  message length = 1+N   │  msg type  │  payload         │
  └─────────────────────────┴────────────┴──────────────────┘

For messages with no payload (CHOKE, UNCHOKE, INTERESTED, NOT_INTERESTED)
the length field equals 1 and there is no payload region.
"""

import socket as _socket
import struct

from p2p_messages import P2PMessages


class Message:
    """Represents a single P2P protocol message."""

    def __init__(self, msg_type: P2PMessages, payload: bytes = None):
        self.type    = msg_type
        self.payload = payload                                   # None for control messages
        self.length  = 1 + (len(payload) if payload else 0)    # wire length field

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_type(self) -> P2PMessages:
        return self.type

    def get_payload(self) -> bytes:
        return self.payload

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_message(self, sock: _socket.socket) -> None:
        """Serialize this message and write it atomically to *sock*."""
        # Build the frame: [length:4][type:1][payload:N]
        frame = struct.pack(">I", self.length) + bytes([self.type.value])
        if self.payload:
            frame += self.payload
        sock.sendall(frame)

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    @staticmethod
    def receive(sock: _socket.socket) -> "Message":
        """
        Block until a complete message arrives on *sock*.
        Handles TCP fragmentation internally.
        """
        # 1. Read the 4-byte length prefix
        raw_len = Message._recv_exactly(sock, 4)
        length  = struct.unpack(">I", raw_len)[0]

        # 2. Read `length` bytes: [type:1] + [payload:length-1]
        body     = Message._recv_exactly(sock, length)
        msg_type = P2PMessages.from_byte(body[0])
        payload  = body[1:] if length > 1 else None

        return Message(msg_type, payload)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    @staticmethod
    def _recv_exactly(sock: _socket.socket, n: int) -> bytes:
        """Read exactly *n* bytes from *sock*, looping over partial reads."""
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise IOError("Connection closed unexpectedly (EOF)")
            data += chunk
        return data
