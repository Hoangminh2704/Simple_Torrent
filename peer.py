import time

__author__ = 'alexisgallepe'

import socket
import struct
import bitstring
from pubsub import pub 
import logging
import requests
from bencodepy import decode as bdecode
import logging
import requests
from bcoding import bdecode, bencode
import hashlib
import urllib
import message
import os

peer_id = os.getenv("PEER_ID", "-PY0001-123456789000")
torrent_file = os.getenv("TORRENT_FILE", "sample.torrent")
tracker_url = os.getenv("TRACKER_URL", "http://192.168.1.169:8080/announce")


class Peer(object):
    def __init__(self, number_of_pieces, ip, port=6881):
        self.last_call = 0.0
        self.has_handshaked = False
        self.healthy = False
        self.read_buffer = b''
        self.socket = None
        self.ip = ip
        self.port = port
        self.number_of_pieces = number_of_pieces
        self.bit_field = bitstring.BitArray(number_of_pieces)
        self.state = {
            'am_choking': True,
            'am_interested': False,
            'peer_choking': True,
            'peer_interested': False,
        }

    def __hash__(self):
        return "%s:%d" % (self.ip, self.port)

    def connect(self):
        try:
            self.socket = socket.create_connection((self.ip, self.port), timeout=2)
            self.socket.setblocking(False)
            logging.debug("Connected to peer ip: {} - port: {}".format(self.ip, self.port))
            self.healthy = True

        except Exception as e:
            print("Failed to connect to peer (ip: %s - port: %s - %s)" % (self.ip, self.port, e.__str__()))
            return False

        return True

    def send_to_peer(self, msg):
        try:
            self.socket.send(msg)
            self.last_call = time.time()
        except Exception as e:
            self.healthy = False
            logging.error("Failed to send to peer : %s" % e.__str__())

    def is_eligible(self):
        now = time.time()
        return (now - self.last_call) > 0.2

    def has_piece(self, index):
        return self.bit_field[index]

    def am_choking(self):
        return self.state['am_choking']

    def am_unchoking(self):
        return not self.am_choking()

    def is_choking(self):
        return self.state['peer_choking']

    def is_unchoked(self):
        return not self.is_choking()

    def is_interested(self):
        return self.state['peer_interested']

    def am_interested(self):
        return self.state['am_interested']

    def handle_choke(self):
        logging.debug('handle_choke - %s' % self.ip)
        self.state['peer_choking'] = True

    def handle_unchoke(self):
        logging.debug('handle_unchoke - %s' % self.ip)
        self.state['peer_choking'] = False

    def handle_interested(self):
        logging.debug('handle_interested - %s' % self.ip)
        self.state['peer_interested'] = True

        if self.am_choking():
            unchoke = message.UnChoke().to_bytes()
            self.send_to_peer(unchoke)

    def handle_not_interested(self):
        logging.debug('handle_not_interested - %s' % self.ip)
        self.state['peer_interested'] = False

    def handle_have(self, have):
        """
        :type have: message.Have
        """
        logging.debug('handle_have - ip: %s - piece: %s' % (self.ip, have.piece_index))
        self.bit_field[have.piece_index] = True

        if self.is_choking() and not self.state['am_interested']:
            interested = message.Interested().to_bytes()
            self.send_to_peer(interested)
            self.state['am_interested'] = True

        # pub.sendMessage('RarestPiece.updatePeersBitfield', bitfield=self.bit_field)

    def handle_bitfield(self, bitfield):
        """
        :type bitfield: message.BitField
        """
        logging.debug('handle_bitfield - %s - %s' % (self.ip, bitfield.bitfield))
        self.bit_field = bitfield.bitfield

        if self.is_choking() and not self.state['am_interested']:
            interested = message.Interested().to_bytes()
            self.send_to_peer(interested)
            self.state['am_interested'] = True

        # pub.sendMessage('RarestPiece.updatePeersBitfield', bitfield=self.bit_field)

    def handle_request(self, request):
        """
        :type request: message.Request
        """
        logging.debug('handle_request - %s' % self.ip)
        if self.is_interested() and self.is_unchoked():
            pub.sendMessage('PiecesManager.PeerRequestsPiece', request=request, peer=self)

    def handle_piece(self, message):
        """
        :type message: message.Piece
        """
        pub.sendMessage('PiecesManager.Piece', piece=(message.piece_index, message.block_offset, message.block))

    def handle_cancel(self):
        logging.debug('handle_cancel - %s' % self.ip)

    def handle_port_request(self):
        logging.debug('handle_port_request - %s' % self.ip)

    def _handle_handshake(self):
        try:
            handshake_message = message.Handshake.from_bytes(self.read_buffer)
            self.has_handshaked = True
            self.read_buffer = self.read_buffer[handshake_message.total_length:]
            logging.debug('handle_handshake - %s' % self.ip)
            return True

        except Exception:
            logging.exception("First message should always be a handshake message")
            self.healthy = False

        return False

    def _handle_keep_alive(self):
        try:
            keep_alive = message.KeepAlive.from_bytes(self.read_buffer)
            logging.debug('handle_keep_alive - %s' % self.ip)
        except message.WrongMessageException:
            return False
        except Exception:
            logging.exception("Error KeepALive, (need at least 4 bytes : {})".format(len(self.read_buffer)))
            return False

        self.read_buffer = self.read_buffer[keep_alive.total_length:]
        return True

    def get_messages(self):
        while len(self.read_buffer) > 4 and self.healthy:
            if (not self.has_handshaked and self._handle_handshake()) or self._handle_keep_alive():
                continue

            payload_length, = struct.unpack(">I", self.read_buffer[:4])
            total_length = payload_length + 4

            if len(self.read_buffer) < total_length:
                break
            else:
                payload = self.read_buffer[:total_length]
                self.read_buffer = self.read_buffer[total_length:]

            try:
                received_message = message.MessageDispatcher(payload).dispatch()
                if received_message:
                    yield received_message
            except message.WrongMessageException as e:
                logging.exception(e.__str__())

    # def __init__(self, number_of_pieces, ip=None, port=6881):
    #     # Nếu không có IP được truyền vào, sử dụng IP của container hiện tại
    #     self.ip = ip or self._get_own_ip()
    #     self.port = port
    #     self.number_of_pieces = number_of_pieces
    def __init__(self, number_of_pieces, ip=None, port=6881):
        self.ip = ip or os.getenv("PEER_IP", "127.0.0.1")  # Đọc từ biến môi trường PEER_IP
        self.port = port
        self.number_of_pieces = number_of_pieces

    def _get_own_ip(self):
        # Tự động lấy IP của container hiện tại
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    
    def connect_to_tracker(self, torrent_file, peer_id, port):
        """
        Kết nối tới tracker để nhận danh sách các peer.
        """
        # Đọc nội dung file .torrent
        with open(torrent_file, 'rb') as f:
            torrent_data = bdecode(f.read())
            # logging.info(f"Torrent data: {torrent_data}")

        # print("Nội dung file .torrent:")
        # for key, value in torrent_data.items():
        #     print(f"{key}: {value}")

        # Lấy tracker URL
        tracker_url = torrent_data.get('announce', None)
        if not tracker_url:
            logging.error("No tracker URL found in the torrent file.")
            return []

        # Tính info_hash
        raw_info = bencode(torrent_data['info'])
        info_hash = hashlib.sha1(raw_info).digest()

        logging.info(f"Connecting to tracker at {tracker_url}")

        # Gửi yêu cầu tới tracker
        params = {
            "info_hash": info_hash,
            "peer_id": peer_id,
            "port": port,
            "uploaded": 0,
            "downloaded": 0,
            "left": torrent_data['info']['length'],  # File size còn lại
            "event": "started"
        }

        try:
            response = requests.get(tracker_url, params=params, timeout=5)
            if response.status_code == 200:
                tracker_response = bdecode(response.content)
                logging.info(f"Connected to tracker, response: {tracker_response}")

                # Giải nén danh sách peers
                peers_binary = tracker_response.get('peers', '')
                peers = []
                for i in range(0, len(peers_binary), 6):
                    ip = ".".join(map(str, peers_binary[i:i + 4]))
                    port = int.from_bytes(peers_binary[i + 4:i + 6], "big")
                    peers.append({"ip": ip, "port": port})

                # Loại bỏ chính địa chỉ của Leecher khỏi danh sách
                filtered_peers = [
                    peer for peer in peers
                    if peer["ip"] != self.ip or peer["port"] != self.port
                ]

                return filtered_peers
            else:
                logging.error(f"Failed to connect to tracker: HTTP {response.status_code}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error connecting to tracker: {e}")

        return []
    def connect(self):
        try:
            self.socket = socket.create_connection((self.ip, self.port), timeout=2)
            self.socket.setblocking(False)
            logging.info(f"Connected to peer: {self.ip}:{self.port}")
            self.healthy = True
            return True
        except Exception as e:
            logging.error(f"Failed to connect to peer {self.ip}:{self.port} - {e}")
            return False
def run_seeder(ip, port, file_path, torrent_file):
    logging.info("Initializing Seeder...")

    # Lấy thông tin từ file .torrent
    with open(torrent_file, "rb") as f:
        torrent_data = bdecode(f.read())

    # Tính toán info_hash từ nội dung file .torrent
    raw_info = bencode(torrent_data["info"])
    info_hash = hashlib.sha1(raw_info).digest()

    file_size = torrent_data["info"]["length"]

    # Gửi thông báo `started` tới tracker
    params = {
        "info_hash": info_hash,
        "peer_id": peer_id,
        "port": port,
        "uploaded": 0,
        "downloaded": 0,
        "left": file_size,
        "event": "started"
    }

    try:
        tracker_response = requests.get(tracker_url, params=params, timeout=5)
        if tracker_response.status_code == 200:
            logging.info("Seeder successfully announced to tracker.")
        else:
            logging.error(f"Failed to announce to tracker. Status code: {tracker_response.status_code}")
    except Exception as e:
        logging.error(f"Error announcing to tracker: {e}")
        return

    # Khởi tạo socket để lắng nghe kết nối từ các Leecher
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind((ip, port))
        server_socket.listen(5)
        logging.info(f"Seeder is ready to upload file {file_path} on port {port}")

        while True:
            client_socket, client_address = server_socket.accept()
            logging.info(f"Connected to Leecher: {client_address}")
            
            # Gửi dữ liệu tới Leecher
            try:
                with open(file_path, "rb") as f:
                    while chunk := f.read(1024):  # Đọc từng chunk dữ liệu 1KB
                        client_socket.sendall(chunk)
                logging.info("File upload completed.")
            except Exception as e:
                logging.error(f"Error while uploading file to Leecher: {e}")
            finally:
                client_socket.close()

    except KeyboardInterrupt:
        logging.info("Seeder shutting down.")
        server_socket.close()
    except Exception as e:
        logging.error(f"Error in Seeder: {e}")
        if server_socket:
            server_socket.close()


def run_leecher(ip, port, torrent_file, output_file="downloaded_file"):
    logging.info("Initializing Leecher...")
    leecher = Peer(number_of_pieces=100, ip=ip, port=port)

    # Kết nối tới tracker và nhận danh sách Seeder
    peers_from_tracker = leecher.connect_to_tracker(torrent_file, peer_id, port)
    if not peers_from_tracker:
        logging.error("No peers received from tracker. Exiting...")
        return  # Thoát khỏi hàm nếu không có peers

    logging.info(f"Received {len(peers_from_tracker)} peers from tracker")
    for peer_info in peers_from_tracker:
        seeder_ip = peer_info["ip"]
        seeder_port = peer_info["port"]
        logging.info(f"Connecting to Seeder {seeder_ip}:{seeder_port}")
        try:
            with socket.create_connection((seeder_ip, seeder_port), timeout=1) as s, open(output_file, "wb") as f:
                while chunk := s.recv(1024):  # Nhận từng chunk dữ liệu
                    f.write(chunk)
            logging.info(f"File downloaded successfully as {output_file}")
            break  # Dừng sau khi tải file thành công
        except Exception as e:
            logging.error(f"Failed to connect to Seeder {seeder_ip}:{seeder_port} - {e}")

# import argparse
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.INFO)

#     # Tự động lấy địa chỉ IP của container
#     hostname = socket.gethostname()
#     local_ip = socket.gethostbyname(hostname)

#     torrent_file = "sample.torrent"       # File .torrent
#     peer_id = "-PY0001-" + "123456789001"  # Peer ID
#     port = 6881  # Cổng mặc định

#     logging.info(f"Initializing Peer with IP: {local_ip} and Port: {port}")

#     # Kết nối tới tracker và nhận danh sách các peer
#     initial_peer = Peer(number_of_pieces=100, ip=local_ip, port=port)
#     peers_from_tracker = initial_peer.connect_to_tracker(torrent_file, peer_id, port)

#     if peers_from_tracker:
#         print(f"Received {len(peers_from_tracker)} peers from tracker:")
#         for i, peer_info in enumerate(peers_from_tracker, start=1):
#             ip = peer_info['ip']
#             port = peer_info['port']
#             print(f"Peer {i}: IP = {ip}, Port = {port}")
#     else:
#         print("No peers received from tracker.")

#     # Chờ và lắng nghe các hành động khác
#     try:
#         while True:
#             logging.info(f"Peer {local_ip}:{port} is running and waiting for connections...")
#             time.sleep(10)  # Peer chờ và có thể thực hiện các hành động khác
#     except KeyboardInterrupt:
#         print(f"Peer {local_ip}:{port} shutting down.")
import argparse

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Tự động lấy địa chỉ IP của container
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)

    # Thêm argparse để nhận các tham số từ dòng lệnh
    parser = argparse.ArgumentParser(description="Run Peer")
    parser.add_argument("--mode", choices=["seeder", "leecher"], required=True, help="Mode of the peer")
    parser.add_argument("--file", required=False, help="Path to the file (required for seeder)")
    parser.add_argument("--torrent", required=True, help="Path to the torrent file")
    args = parser.parse_args()

    peer_id = os.getenv("PEER_ID", "-PY0001-123456789001")
    port = int(os.getenv("PEER_PORT", 6881))  # Lấy port từ môi trường hoặc dùng mặc định

    logging.info(f"Initializing Peer with IP: {local_ip} and Port: {port}")

    if args.mode == "seeder":
        if not args.file:
            raise ValueError("File path is required for seeder mode")
        run_seeder(local_ip, port, args.file, args.torrent)  # Thêm `args.torrent` cho tham số `torrent_file`


    elif args.mode == "leecher":
        run_leecher(local_ip, port, args.torrent)
        try:
            while True:
                logging.info(f"Peer {local_ip}:{port} is running and waiting for connections...")
                time.sleep(10)  # Peer chờ và có thể thực hiện các hành động khác
        except KeyboardInterrupt:
            print(f"Peer {local_ip}:{port} shutting down.")
