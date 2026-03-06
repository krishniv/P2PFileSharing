"""
peer_configuration.py
---------------------
Data class that holds the static configuration for one peer entry read
from PeerInfo.cfg.
"""


class PeerConfiguration:
    """Immutable snapshot of a peer's address and initial file ownership."""

    def __init__(
        self,
        peer_id: int,
        host_name: str,
        port_number: int,
        peer_has_file: bool,
    ):
        self.ID           = peer_id       # Unique numeric peer identifier
        self.host_name    = host_name     # Hostname / IP address
        self.port_number  = port_number   # TCP port the peer listens on
        self.peer_has_file = peer_has_file # True = peer already has the complete file

    def __repr__(self) -> str:
        return (
            f"PeerConfiguration(id={self.ID}, host={self.host_name}, "
            f"port={self.port_number}, has_file={self.peer_has_file})"
        )
