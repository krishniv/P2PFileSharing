"""
p2p_messages.py
---------------
Enumeration of all message types used in the BitTorrent-style P2P protocol.

Wire representation: each type is a single byte (0-7) sent on the TCP stream.
"""

from enum import Enum


class P2PMessages(Enum):
    CHOKE        = 0   # Stop the remote peer from requesting pieces
    UNCHOKE      = 1   # Allow the remote peer to request pieces again
    INTERESTED   = 2   # Sender wants pieces the receiver has
    NOT_INTERESTED = 3 # Sender has no use for the receiver's pieces right now
    HAVE         = 4   # Sender just obtained piece <index>
    BITFIELD     = 5   # Sender's bitmask of which pieces it currently owns
    REQUEST      = 6   # Sender wants piece <index> from the receiver
    PIECE        = 7   # Sender is delivering the raw data for piece <index>

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_value(self) -> int:
        """Return the raw integer value used on the wire."""
        return self.value

    @staticmethod
    def from_byte(type_byte: int) -> "P2PMessages":
        """Decode a raw byte received from the network into the matching enum member."""
        for member in P2PMessages:
            if member.value == type_byte:
                return member
        raise ValueError(f"Unknown P2P message type byte: {type_byte!r}")
