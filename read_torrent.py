from bcoding import bdecode

def read_torrent(file_path):
    """
    Đọc và giải mã nội dung của file .torrent
    Args:
        file_path (str): Đường dẫn tới file .torrent
    """
    with open(file_path, "rb") as f:
        torrent_data = bdecode(f.read())

    # In nội dung file torrent
    print("Nội dung file .torrent:")
    for key, value in torrent_data.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    # Đường dẫn tới file .torrent
    torrent_file = "sample.torrent"  # Đổi thành tên file .torrent bạn muốn đọc
    read_torrent(torrent_file)
