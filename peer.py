import sys
import os
import threading
import socket
import struct
import time
import math
import random
from peer_config import PeerConfiguration
from logger import Logger
from peer_connection_handler import PeerConnectionHandler

COMMON_CONFIG = "Common.cfg"
PEER_INFO_CONFIG = "PeerInfo.cfg"

class Peer:
    def __init__(self, peer_id):
        self.peer_id = str(peer_id)
        self.working_directory = os.getcwd()
        self.peer_directory = os.path.join(self.working_directory, f"peer_{self.peer_id}")
        self.logger = Logger(f"log_peer_{self.peer_id}.log")
        
        self.host_name = ""
        self.port = 0
        self.peer_has_file = False
        
        self.peer_completion_map = {}
        self.peers_info = {}
        self.neighbor_sockets = {}
        self.client_handlers = {}
        self.preferred_neighbors = set()
        self.optimistic_unchoked_neighbor = -1
        
        self.bitfield = None
        self.file_name = ""
        self.file_size = 0
        self.total_pieces = 0
        self.piece_size = 0
        
        self._has_complete_file = False
        self.completion_lock = threading.Lock()
        self.bitfield_lock = threading.Lock()
        
        self.number_of_preferred_neighbors = 0
        self.unchoking_interval = 0
        self.optimistic_unchoking_interval = 0

    def get_peer_directory(self):
        return self.peer_directory
        
    def get_file_name(self):
        return self.file_name
        
    def get_piece_size(self):
        return self.piece_size
        
    def get_total_pieces(self):
        return self.total_pieces
        
    def get_logger(self):
        return self.logger
        
    def get_peer_id(self):
        return self.peer_id
        
    def has_complete_file(self):
        with self.completion_lock:
            return self._has_complete_file
            
    def get_client_handlers(self):
        return list(self.client_handlers.values())
        
    def remove_client_handler(self, remote_peer_id):
        if remote_peer_id in self.client_handlers:
            del self.client_handlers[remote_peer_id]
        if remote_peer_id in self.neighbor_sockets:
            try:
                self.neighbor_sockets[remote_peer_id].close()
            except Exception:
                pass
            del self.neighbor_sockets[remote_peer_id]

    def start(self):
        self.process_common_config_file()
        self.process_peer_info_config_file()
        
        if not os.path.exists(self.peer_directory):
            os.makedirs(self.peer_directory)
            
        self.total_pieces = math.ceil(self.file_size / self.piece_size)
        bf_size = math.ceil(self.total_pieces / 8)
        self.bitfield = bytearray(bf_size)
        
        if self.peer_has_file:
            # set all valid bits in bitfield to 1
            for i in range(self.total_pieces):
                byte_index = i // 8
                bit_index = 7 - (i % 8)
                self.bitfield[byte_index] |= (1 << bit_index)
                
            with self.completion_lock:
                self._has_complete_file = True
                
            input_file_path = os.path.join(self.peer_directory, self.file_name)
            if not os.path.exists(input_file_path):
                print(f"File {self.file_name} not found in {self.peer_directory}")
                
            self.split_file_into_pieces(input_file_path)
            
        self.start_server()
        self.connect_to_peers()
        
        threading.Thread(target=self.schedule_unchoking, daemon=True).start()
        threading.Thread(target=self.schedule_optimistic_unchoking, daemon=True).start()
        threading.Thread(target=self.check_completion, daemon=True).start()

        # Prevent main thread from exiting immediately
        while True:
            time.sleep(1)

    def process_common_config_file(self):
        with open(COMMON_CONFIG, 'r') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                parts = line.split()
                key, val = parts[0], parts[1]
                if key == "NumberOfPreferredNeighbors":
                    self.number_of_preferred_neighbors = int(val)
                elif key == "UnchokingInterval":
                    self.unchoking_interval = int(val)
                elif key == "OptimisticUnchokingInterval":
                    self.optimistic_unchoking_interval = int(val)
                elif key == "FileName":
                    self.file_name = val
                elif key == "FileSize":
                    self.file_size = int(val)
                elif key == "PieceSize":
                    self.piece_size = int(val)
                    
        self.logger.create_log(f"Parsed Common.cfg: PreferredNeighbors={self.number_of_preferred_neighbors}, "
                               f"UnchokingInterval={self.unchoking_interval}, "
                               f"OptimisticUnchokingInterval={self.optimistic_unchoking_interval}, "
                               f"FileName={self.file_name}, FileSize={self.file_size}, PieceSize={self.piece_size}")

    def process_peer_info_config_file(self):
        self.logger.create_log("Reading peer configuration from PeerInfo.cfg...")
        found_self = False
        with open(PEER_INFO_CONFIG, 'r') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                tokens = line.split()
                peer_id = int(tokens[0])
                hostname = tokens[1]
                port = int(tokens[2])
                has_file = (tokens[3] == "1")
                
                self.peers_info[peer_id] = PeerConfiguration(peer_id, hostname, port, has_file)
                self.logger.create_log(f"Loaded peer: ID={peer_id}, Hostname={hostname}, Port={port}, HasFile={has_file}")
                
                if str(peer_id) == self.peer_id:
                    self.host_name = hostname
                    self.port = port
                    self.peer_has_file = has_file
                    found_self = True
                    self.logger.create_log(f"This peer [{self.peer_id}] has Hostname={self.host_name}, Port={self.port}, HasFile={self.peer_has_file}")
                    
        if not found_self:
            raise ValueError(f"Peer ID {self.peer_id} not found in PeerInfo.cfg")

    def mark_peer_complete(self, remote_peer_id):
        self.peer_completion_map[remote_peer_id] = True
        self.logger.create_log(f"Peer [{self.peer_id}] marked Peer [{remote_peer_id}] as complete.")

    def start_server(self):
        def server_loop():
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.bind(('', self.port))
            server_socket.listen(50)
            print(f"Peer {self.peer_id} is listening on port {self.port}")
            while True:
                client_socket, _ = server_socket.accept()
                threading.Thread(target=self.handle_newly_accepted_connection, args=(client_socket,), daemon=True).start()
                
        threading.Thread(target=server_loop, daemon=True).start()

    def handle_newly_accepted_connection(self, client_socket):
        try:
            remote_peer_id = self.perform_handshake(client_socket)
            self.logger.create_log(f"TCP connection is built between P{remote_peer_id} and P{self.peer_id}")
            
            remote_id = int(remote_peer_id)
            self.neighbor_sockets[remote_id] = client_socket
            
            handler = PeerConnectionHandler(client_socket, remote_id, self)
            self.client_handlers[remote_id] = handler
            self.peer_completion_map[remote_id] = False
            
            handler.start()
        except Exception as e:
            print(f"Error handling incoming connection: {e}")

    def connect_to_peers(self):
        for peer_info in self.peers_info.values():
            if peer_info.id == int(self.peer_id):
                break
                
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((peer_info.host_name, peer_info.port_number))
                self.perform_handshake(sock)
                
                self.logger.create_log(f"TCP connection is built between P{self.peer_id} and P{peer_info.id}")
                self.neighbor_sockets[peer_info.id] = sock
                
                handler = PeerConnectionHandler(sock, peer_info.id, self)
                self.client_handlers[peer_info.id] = handler
                self.peer_completion_map[peer_info.id] = False
                
                handler.start()
            except Exception as e:
                print(f"Error connecting to peer {peer_info.id}: {e}")

    def perform_handshake(self, sock):
        header = b"P2PFILESHARINGPROJ"
        zero_bits = bytes(10)
        peer_id_bytes = struct.pack(">I", int(self.peer_id))
        
        handshake_msg = header + zero_bits + peer_id_bytes
        sock.sendall(handshake_msg)
        self.logger.create_log(f"Peer [{self.peer_id}] sent handshake to the remote peer.")
        
        received_handshake = bytearray()
        while len(received_handshake) < 32:
            packet = sock.recv(32 - len(received_handshake))
            if not packet:
                raise ConnectionError("Stream closed during handshake")
            received_handshake.extend(packet)
            
        received_header = received_handshake[:18]
        if received_header != b"P2PFILESHARINGPROJ":
            raise ValueError("Invalid handshake header")
            
        remote_peer_id = struct.unpack(">I", received_handshake[28:32])[0]
        self.logger.create_log(f"Peer [{self.peer_id}] received valid handshake from Peer [{remote_peer_id}].")
        
        return str(remote_peer_id)

    def schedule_unchoking(self):
        while True:
            time.sleep(self.unchoking_interval)
            self.update_preferred_neighbors()

    def schedule_optimistic_unchoking(self):
        while True:
            time.sleep(self.optimistic_unchoking_interval)
            self.optimistically_unchoke_neighbor()

    def update_preferred_neighbors(self):
        try:
            interested_neighbors = []
            for handler in self.client_handlers.values():
                if handler.is_interested():
                    interested_neighbors.append(handler.get_remote_peer_id())
                    
            if not interested_neighbors:
                self.preferred_neighbors.clear()
                self.logger.create_log(f"Peer [{self.peer_id}] has no interested neighbors.")
                return
                
            self.logger.create_log(f"Every {self.unchoking_interval} seconds, Peer [{self.peer_id}] recalculates preferred neighbors and sends CHOKE/UNCHOKE messages.")
            
            if self.has_complete_file():
                random.shuffle(interested_neighbors)
                self.preferred_neighbors = set(interested_neighbors[:self.number_of_preferred_neighbors])
            else:
                sorted_neighbors = sorted(interested_neighbors, 
                                          key=lambda pid: self.client_handlers[pid].get_track_download_rate(), 
                                          reverse=True)
                self.preferred_neighbors = set(sorted_neighbors[:self.number_of_preferred_neighbors])
                
            for handler in self.client_handlers.values():
                remote_id = handler.get_remote_peer_id()
                if remote_id in self.preferred_neighbors:
                    if handler.is_choked():  # only send unchoke if currently choked (avoid duplicate)
                        handler.send_unchoke()
                else:
                    if not handler.is_choked():  # only send choke if currently unchoked
                        handler.send_choke()
                        
            neighbor_list = ", ".join(map(str, self.preferred_neighbors))
            self.logger.create_log(f"Peer [{self.peer_id}] has the preferred neighbors [{neighbor_list}].")
            
        except Exception as e:
            print(f"Error updating preferred neighbors: {e}")

    def optimistically_unchoke_neighbor(self):
        try:
            candidates = []
            for handler in self.client_handlers.values():
                if handler.is_interested() and handler.is_choked() and handler.get_remote_peer_id() not in self.preferred_neighbors:
                    candidates.append(handler.get_remote_peer_id())
                    
            if not candidates:
                return
                
            selected_peer_id = random.choice(candidates)
            self.optimistic_unchoked_neighbor = selected_peer_id
            
            self.client_handlers[selected_peer_id].send_unchoke()
            self.logger.create_log(f"Peer [{self.peer_id}] has the optimistically unchoked neighbor [{self.optimistic_unchoked_neighbor}].")
            
        except Exception as e:
            print(f"Error in optimistically unchoking neighbor: {e}")

    def has_piece(self, piece_index):
        with self.bitfield_lock:
            byte_index = piece_index // 8
            bit_index = 7 - (piece_index % 8)
            return (self.bitfield[byte_index] & (1 << bit_index)) != 0

    def get_bitfield(self):
        with self.bitfield_lock:
            return bytearray(self.bitfield)

    def update_bitfield(self, piece_index):
        with self.bitfield_lock:
            byte_index = piece_index // 8
            bit_index = 7 - (piece_index % 8)
            self.bitfield[byte_index] |= (1 << bit_index)

    def is_completed(self):
        for i in range(self.total_pieces):
            if not self.has_piece(i):
                return False
        return True

    def check_and_set_completion(self):
        with self.completion_lock:
            if self.is_completed() and not self._has_complete_file:
                self._has_complete_file = True
                self.logger.create_log(f"Peer [{self.peer_id}] has downloaded the complete file.")
                self.logger.create_log(f"Peer [{self.peer_id}] is reassembling {self.total_pieces} pieces into '{self.file_name}'.")
                try:
                    self.merge_file_pieces()
                    self.logger.create_log(f"Peer [{self.peer_id}] successfully wrote '{self.file_name}' to disk.")
                except Exception as e:
                    print(f"Error merging file pieces: {e}")

    def merge_file_pieces(self):
        output_file_path = os.path.join(self.peer_directory, self.file_name)
        with open(output_file_path, "wb") as fos:
            for i in range(self.total_pieces):
                piece_path = os.path.join(self.peer_directory, f"piece_{i}")
                with open(piece_path, "rb") as fis:
                    fos.write(fis.read())

    def split_file_into_pieces(self, input_file_path):
        with open(input_file_path, "rb") as fis:
            piece_index = 0
            while True:
                data = fis.read(self.piece_size)
                if not data:
                    break
                piece_path = os.path.join(self.peer_directory, f"piece_{piece_index}")
                with open(piece_path, "wb") as fos:
                    fos.write(data)
                piece_index += 1
        self.logger.create_log(f"Peer [{self.peer_id}] has split the file into {piece_index} pieces.")

    def check_completion(self):
        while True:
            try:
                if len(self.peer_completion_map) < len(self.peers_info) - 1:
                    time.sleep(2)
                    continue
                    
                if self.has_complete_file():
                    all_complete = True
                    for pid, is_complete in self.peer_completion_map.items():
                        if not is_complete:
                            all_complete = False
                            break
                            
                    if all_complete:
                        self.logger.create_log(
                            f"Peer [{self.peer_id}] detects that all peers have the complete file. "
                            f"Shutting down gracefully."
                        )
                        # close all neighbor sockets cleanly before exit
                        for sock in list(self.neighbor_sockets.values()):
                            try:
                                sock.close()
                            except Exception:
                                pass
                        time.sleep(1)
                        os._exit(0)
                        
                time.sleep(2)
            except Exception as e:
                print(f"Error in completion checker: {e}")
                time.sleep(2)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python peer.py <peerID>")
        sys.exit(1)
    
    peer_id = sys.argv[1]
    peer = Peer(peer_id)
    peer.start()
