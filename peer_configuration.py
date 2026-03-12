class PeerConfiguration:
    def __init__(self, peer_id, host_name, port_number, peer_has_file):
        self.id = peer_id
        self.host_name = host_name
        self.port_number = port_number
        self.peer_has_file = peer_has_file
