import struct
from enum import Enum

class P2PMessages(Enum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7

    @classmethod
    def from_byte(cls, value):
        for msg in cls:
            if msg.value == value:
                return msg
        raise ValueError(f"Invalid message type: {value}")
    
    def get_value(self):
        return self.value

class Message:
    def __init__(self, msg_type, payload=None):
        self.type = msg_type
        self.payload = payload
        self.length = 1 + (len(payload) if payload else 0)

    def get_type(self):
        return self.type
        
    def get_payload(self):
        return self.payload

    @staticmethod
    def receive_p2p_bittorrent_messages(sock):
        def read_exact(sock, count):
            data = bytearray()
            while len(data) < count:
                packet = sock.recv(count - len(data))
                if not packet:
                    raise ConnectionError("End of stream")
                data.extend(packet)
            return bytes(data)

        len_bytes = read_exact(sock, 4)
        length = struct.unpack(">I", len_bytes)[0]
        
        msg_bytes = read_exact(sock, length)
        type_byte = msg_bytes[0]
        msg_type = P2PMessages.from_byte(type_byte)
        
        payload = None
        if length > 1:
            payload = msg_bytes[1:]
            
        return Message(msg_type, payload)

    def send_message(self, sock):
        data = struct.pack(">I", self.length)
        data += bytes([self.type.value])
        if self.payload:
            data += self.payload
        sock.sendall(data)
