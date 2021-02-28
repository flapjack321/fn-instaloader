import sys
import os
import json
import instaloader
import logging
import smbclient

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("smbprotocol").setLevel(logging.WARNING)

class Downloader(object):
    def __init__(self, request_data):
        self._raw_request_data = request_data
        self._parsed_request_data = None
        self._smb_directory_path = None
        self.__instaloader_username = None
        self.__instaloader_password = None
        self.__instaloader_account = None
        self.__smb_username = None
        self.__smb_password = None
        self.__smb_server = None
        self.__smb_share = None
        self.__smb_directory = None

        self.return_message = ""
        self.return_code = 200
        self.L = None
        self.profile = None
        self.download_list = set()

    def set_error_message(self, msg, code=400):
        logging.critical(msg)
        self.return_message = msg
        self.return_code = code

    def get_return_data(self):
        return self.return_message, self.return_code

    def _is_image(self, name):
        if name.endswith(".jpg"):
            return True
        elif name.endswith(".png"):
            return True
        elif name.endswith(".jpeg"):
            return True

        return False

    def _login_instagram(self):
        self.L = instaloader.Instaloader(
            quiet=True,
            dirname_pattern="/tmp/{profile}",
            filename_pattern="{date_utc}",
            save_metadata=False,
            download_geotags=False,
            download_comments=False
        )
        try:
            self.L.login(self.__instaloader_username, self.__instaloader_password)
        except Exception as e:
            self.set_error_message(f"Failed to login to Instagram: {e}")
            return False

        try:
            self.profile = instaloader.Profile.from_username(self.L.context, self.__instaloader_account)
        except Exception as e:
            self.set_error_message(f"Failed to find profile for '{self.__instaloader_account}': {e}")
            return False

        return True

    def _logout_instagram(self):
        if self.L is not None:
            self.L.close()
            self.L = None

    def populate_class_vars(self):
        self.__instaloader_username = self._parsed_request_data["instaloader_username"]
        self.__instaloader_password = self._parsed_request_data["instaloader_password"]
        self.__instaloader_account = self._parsed_request_data["instaloader_account"]
        self.__smb_username = self._parsed_request_data["smb_username"]
        self.__smb_password = self._parsed_request_data["smb_password"]
        self.__smb_server = self._parsed_request_data["smb_server"]
        self.__smb_share = self._parsed_request_data["smb_share"]
        self.__smb_directory = self._parsed_request_data["smb_directory"]
        self._smb_directory_path = f"\\\\{self.__smb_server}\\{self.__smb_share}\\{self.__smb_directory}\\{self.__instaloader_account}"

    def validate_data(self):
        try:
            self._parsed_request_data = json.loads(self._raw_request_data)
        except Exception as e:
            self.set_error_message(f"Invalid JSON data passed to function: {e}")
            return False

        required_fields = [
            "instaloader_username", "instaloader_password", "instaloader_account",
            "smb_username", "smb_password", "smb_server", "smb_directory", "smb_share"
        ]
        for field in required_fields:
            if field not in self._parsed_request_data:
                self.set_error_message(f"Missing field '{field}' in JSON body")
                return False

        self.populate_class_vars()
        return True

    def scan_posts(self):
        logging.debug(f"Full filepath of account: {self._smb_directory_path}")
        smbclient.register_session(
            self.__smb_server,
            username=self.__smb_username,
            password=self.__smb_password
        )

        downloaded_files = []
        for file_info in smbclient.scandir(self._smb_directory_path):
            file_inode = file_info.inode()
            if file_info.is_file() and self._is_image(file_info.name):
                if "profile" in file_info.name:
                    # We don't need the profile picture
                    continue
                downloaded_files.append(file_info.name)

        logging.debug(f"{len(downloaded_files)} posts already downloaded")
        downloaded_files.sort()

        last_post = downloaded_files[-1]
        last_post_date = last_post.split(".")[0]
        last_post_date = last_post_date.split("_")[:-1]
        last_post_date[1] = last_post_date[1].replace("-", ":")
        last_post_date = " ".join(last_post_date).strip()
        if self.L is None:
            if not self._login_instagram():
                return False

        for post in self.profile.get_posts():
            if last_post_date == str(post.date_utc).strip():
                logging.debug("Found last downloaded post")
                break
            else:
                self.download_list.add(post)
                logging.debug(f"Adding post from {post.date_utc} to download list")

            if 50 < len(self.download_list):
                # If the length of the download list is too large this should
                # not be an automated task
                self.set_error_message(f"Too many posts to download. Something may have gone wrong")
                return False

        logging.debug(f"{len(self.download_list)} posts queued for download")
        return True


    def download(self):
        # Ensure the download directory exists
        temp_download_dir = os.path.join("/tmp", self.__instaloader_account)
        if not os.path.isdir(temp_download_dir):
            try:
                os.mkdir(temp_download_dir)
            except OSError as e:
                self.set_error_message(f"Failed to create temporary download directory: {e}")
                return False

        for post in self.download_list:
            logging.debug(f"Downloading post from {post.date_utc}")
            if self.L.download_post(post, target=temp_download_dir):
                logging.debug(f"Successfully download post {post.date_utc}")
            else:
                logging.debug(f"Failed to download post {post.date_utc}. Attempting to continue")

        downloaded_posts = os.listdir(temp_download_dir)
        for post_file in downloaded_posts:
            logging.debug(f"Copying file '{post_file}' to SMB share")
            # Copy file over SMB
            filename = self._smb_directory_path + "\\" + post_file
            with open(os.path.join(temp_download_dir, post_file), "rb") as pf:
                # Given the file sizes just read it into memory
                pf_content = pf.read()
            with smbclient.open_file(filename, mode="wb") as fd:
                fd.write(pf_content)

def handle(req):
    """handle a request to the function
    Args:
        req (str): request body
    """
    downloader = Downloader(req)

    if not downloader.validate_data():
        return downloader.get_return_data()

    if not downloader.scan_posts():
        return downloader.get_return_data()

    if not downloader.download():
        return downloader.get_return_data()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(verbose=True)
    data = {
        "instaloader_username": os.getenv("instaloader_username"),
        "instaloader_password": os.getenv("instaloader_password"),
        "instaloader_account": os.getenv("instaloader_account"),
        "smb_server": os.getenv("smb_server"),
        "smb_username": os.getenv("smb_username"),
        "smb_password": os.getenv("smb_password"),
        "smb_share": os.getenv("smb_share"),
        "smb_directory": os.getenv("smb_directory")
    }
    print(data)
    sys.exit(0)
    print(handle(json.dumps(data)))
