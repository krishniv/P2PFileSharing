import threading
import struct
import math
import os
import random
from message import Message, P2PMessages

class PeerConnectionHandler(threading.Thread):
    def __init__(self, sock, remote_peer_id, peer):
        super().__init__()
        self.sock = sock
        self.remote_peer_id = remote_peer_id
        self.peer = peer
        self.choked = True
        self.interested = False
        
        bf_size = math.ceil(peer.get_total_pieces() / 8)
        self.remote_peers_bitfield = bytearray(bf_size)
        
        self.track_download_rate = 0
        self.pieces_downloaded = 0
        self.total_bytes_received = 0
        self.marked_complete = False

    def run(self):
        try:
            if self.peer.get_bitfield() is not None:
                self.send_bitfield()
                
            while True:
                message = Message.receive_p2p_bittorrent_messages(self.sock)
                self.handle_message(message)
                
        except Exception as e:
            self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] connection closed with [{self.remote_peer_id}].")
            self.peer.remove_client_handler(self.remote_peer_id)

    def send_bitfield(self):
        bitfield = self.peer.get_bitfield()
        if bitfield is not None:
            msg = Message(P2PMessages.BITFIELD, bytes(bitfield))
            msg.send_message(self.sock)
            self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent BITFIELD message to [{self.remote_peer_id}].")

    def handle_message(self, message):
        msg_type = message.get_type()
        payload = message.get_payload()
        
        if msg_type == P2PMessages.BITFIELD:
            self.process_received_bitfield(payload)
        elif msg_type == P2PMessages.INTERESTED:
            self.handle_interested_message()
        elif msg_type == P2PMessages.NOT_INTERESTED:
            self.handle_not_interested_message()
        elif msg_type == P2PMessages.REQUEST:
            self.handle_request_message(payload)
        elif msg_type == P2PMessages.PIECE:
            self.handle_piece_message(payload)
        elif msg_type == P2PMessages.HAVE:
            self.handle_have_message(payload)
        elif msg_type == P2PMessages.CHOKE:
            self.handle_choke_message()
        elif msg_type == P2PMessages.UNCHOKE:
            self.handle_unchoke_message()
        else:
            print(f"Unknown message type received from peer {self.remote_peer_id}")

    def process_received_bitfield(self, payload):
        self.remote_peers_bitfield[:] = payload
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] received the BITFIELD message from [{self.remote_peer_id}].")
        
        is_interested = False
        for i in range(self.peer.get_total_pieces()):
            if not self.peer.has_piece(i) and self.has_piece(i):
                is_interested = True
                break
                
        if is_interested:
            self.send_interested()
            self.interested = True
        else:
            self.send_not_interested()
            self.interested = False
            
        if self.get_remote_peers_piece_count() == self.peer.get_total_pieces():
            self.peer.mark_peer_complete(self.remote_peer_id)
            
        self.log_peer_status_summary()

    def handle_interested_message(self):
        self.interested = True
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] received the 'interested' message from [{self.remote_peer_id}].")
        self.log_peer_status_summary()

    def handle_not_interested_message(self):
        self.interested = False
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] received the 'not interested' message from [{self.remote_peer_id}].")
        self.log_peer_status_summary()

    def handle_request_message(self, payload):
        piece_index = struct.unpack(">I", payload[:4])[0]
        if not self.choked and self.peer.has_piece(piece_index):
            self.send_piece(piece_index)
            self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent piece [{piece_index}] to [{self.remote_peer_id}].")

    def handle_piece_message(self, payload):
        piece_index = struct.unpack(">I", payload[:4])[0]
        piece_data = payload[4:]
        
        self.save_piece(piece_index, piece_data)
        self.peer.update_bitfield(piece_index)
        self.peer.check_and_set_completion()
        
        for handler in self.peer.get_client_handlers():
            handler.send_have(piece_index)
            
        self.pieces_downloaded += 1
        self.track_download_rate += 1
        self.total_bytes_received += len(piece_data)
        
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] has downloaded the piece [{piece_index}] from [{self.remote_peer_id}]. Now the number of pieces it has is [{self.get_number_of_pieces()}].")
        self.peer.get_logger().create_log(f"Total bytes received so far: {self.total_bytes_received} bytes.")
        
        self.request_piece()

    def handle_have_message(self, payload):
        piece_index = struct.unpack(">I", payload[:4])[0]
        self.set_piece_available(piece_index)
        
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] received the 'have' message from [{self.remote_peer_id}] for the piece [{piece_index}].")
        
        if not self.peer.has_piece(piece_index):
            self.send_interested()
            self.interested = True
            
        if self.get_remote_peers_piece_count() == self.peer.get_total_pieces():
            self.peer.mark_peer_complete(self.remote_peer_id)
            
        self.log_peer_status_summary()

    def get_remote_peers_piece_count(self):
        count = 0
        for i in range(self.peer.get_total_pieces()):
            if self.has_piece(i):
                count += 1
        return count

    def handle_choke_message(self):
        self.choked = True
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] is choked by [{self.remote_peer_id}].")
        self.log_peer_status_summary()

    def handle_unchoke_message(self):
        self.choked = False
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] is unchoked by [{self.remote_peer_id}].")
        self.log_peer_status_summary()
        self.request_piece()

    def send_interested(self):
        msg = Message(P2PMessages.INTERESTED)
        msg.send_message(self.sock)
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent INTERESTED message to [{self.remote_peer_id}].")

    def send_not_interested(self):
        msg = Message(P2PMessages.NOT_INTERESTED)
        msg.send_message(self.sock)
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent NOT INTERESTED message to [{self.remote_peer_id}].")

    def send_have(self, piece_index):
        payload = struct.pack(">I", piece_index)
        msg = Message(P2PMessages.HAVE, payload)
        msg.send_message(self.sock)
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent HAVE message for piece [{piece_index}] to [{self.remote_peer_id}].")

    def send_choke(self):
        msg = Message(P2PMessages.CHOKE)
        msg.send_message(self.sock)
        self.choked = True
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] is choking [{self.remote_peer_id}].")
        self.log_peer_status_summary()

    def send_unchoke(self):
        msg = Message(P2PMessages.UNCHOKE)
        msg.send_message(self.sock)
        self.choked = False
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] is unchoking [{self.remote_peer_id}].")
        self.log_peer_status_summary()
        self.request_piece()

    def send_piece(self, piece_index):
        piece_path = os.path.join(self.peer.get_peer_directory(), f"piece_{piece_index}")
        with open(piece_path, "rb") as f:
            piece_data = f.read()
            
        payload = struct.pack(">I", piece_index) + piece_data
        msg = Message(P2PMessages.PIECE, payload)
        msg.send_message(self.sock)

    def request_piece(self):
        if self.choked:
            return
            
        missing_pieces = []
        for i in range(self.peer.get_total_pieces()):
            if not self.peer.has_piece(i) and self.has_piece(i):
                missing_pieces.append(i)
                
        if not missing_pieces:
            self.send_not_interested()
            self.interested = False
            return
            
        piece_index = random.choice(missing_pieces)
        payload = struct.pack(">I", piece_index)
        msg = Message(P2PMessages.REQUEST, payload)
        msg.send_message(self.sock)
        self.peer.get_logger().create_log(f"Peer [{self.peer.get_peer_id()}] sent REQUEST for piece [{piece_index}] to [{self.remote_peer_id}].")

    def save_piece(self, piece_index, piece_data):
        piece_path = os.path.join(self.peer.get_peer_directory(), f"piece_{piece_index}")
        with open(piece_path, "wb") as f:
            f.write(piece_data)

    def has_piece(self, index):
        byte_index = index // 8
        bit_index = 7 - (index % 8)
        if byte_index >= len(self.remote_peers_bitfield):
            return False
        return (self.remote_peers_bitfield[byte_index] & (1 << bit_index)) != 0

    def set_piece_available(self, index):
        byte_index = index // 8
        bit_index = 7 - (index % 8)
        if byte_index < len(self.remote_peers_bitfield):
            self.remote_peers_bitfield[byte_index] |= (1 << bit_index)

    def is_interested(self):
        return self.interested

    def is_choked(self):
        return self.choked

    def get_remote_peer_id(self):
        return self.remote_peer_id

    def get_track_download_rate(self):
        rate = self.track_download_rate
        self.track_download_rate = 0
        return rate

    def get_number_of_pieces(self):
        count = 0
        for i in range(self.peer.get_total_pieces()):
            if self.peer.has_piece(i):
                count += 1
        return count

    def has_complete_file(self):
        return self.peer.has_complete_file()

    def log_peer_status_summary(self):
        choked_str = "Choked" if self.choked else "Unchoked"
        interested_str = "Interested" if self.interested else "Not Interested"
        status = f"Neighbor [{self.remote_peer_id}]: {choked_str}, {interested_str}"
        self.peer.get_logger().create_log(status)
