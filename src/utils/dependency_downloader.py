import os
import requests
import lzma
import shutil
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, LogSource

class DependencyDownloader:
    def __init__(self, download_dir, arch, version="latest"):
        self.download_dir = download_dir
        self.arch = arch
        self.version = version
        self.actual_version = None # Will be set after fetching latest or using specific

    def _get_latest_frida_version(self):
        """Fetches the latest Frida release version from GitHub API."""
        log_info(LogSource.SYSTEM, "Fetching latest Frida version...")
        try:
            response = requests.get("https://api.github.com/repos/frida/frida/releases/latest", timeout=10)
            response.raise_for_status()
            self.actual_version = response.json()["tag_name"]
            log_info(LogSource.SYSTEM, f"Latest Frida version: {self.actual_version}")
            return self.actual_version
        except requests.RequestException as e:
            log_error(LogSource.SYSTEM, f"Error fetching latest Frida version: {e}")
            return None

    def _construct_frida_server_url(self, version_to_use):
        """Constructs the download URL for the Frida server."""
        if not version_to_use:
            return None
        url = f"https://github.com/frida/frida/releases/download/{version_to_use}/frida-server-{version_to_use}-android-{self.arch}.xz"
        log_info(LogSource.SYSTEM, f"Constructed Frida server URL: {url}")
        return url

    def _download_file(self, url, dest_path):
        """Downloads a file from a URL to a destination path."""
        log_info(LogSource.SYSTEM, f"Downloading from {url} to {dest_path}...")
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(dest_path, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)
            log_info(LogSource.SYSTEM, "Download complete.")
            return True
        except requests.RequestException as e:
            log_error(LogSource.SYSTEM, f"Error downloading file {url}: {e}")
            return False
        except IOError as e:
            log_error(LogSource.SYSTEM, f"Error writing file to {dest_path}: {e}")
            return False

    def _extract_xz(self, xz_path, output_path):
        """Extracts an .xz file using the lzma module."""
        log_info(LogSource.SYSTEM, f"Extracting {xz_path} to {output_path}...")
        try:
            with lzma.open(xz_path) as f_xz:
                with open(output_path, 'wb') as f_out:
                    shutil.copyfileobj(f_xz, f_out)
            # Set executable permission for the extracted server (important for Linux/macOS, good practice for all)
            # os.chmod(output_path, 0o755) # This might cause issues on Windows if not handled carefully
            log_info(LogSource.SYSTEM, "Extraction complete.")
            return True
        except lzma.LZMAError as e:
            log_error(LogSource.SYSTEM, f"Error extracting .xz file {xz_path}: {e}")
            return False
        except IOError as e:
            log_error(LogSource.SYSTEM, f"Error writing extracted file to {output_path}: {e}")
            return False

    def get_expected_server_binary_name(self, version_to_check):
        """Returns the expected name of the Frida server binary for a given version."""
        if not version_to_check:
            return None
        return f"frida-server-{version_to_check}-android-{self.arch}"

    def check_and_download_frida_server(self):
        """Checks if the correct Frida server binary exists, otherwise downloads and extracts it."""
        os.makedirs(self.download_dir, exist_ok=True)

        version_to_use = self.version
        if self.version.lower() == "latest":
            latest_ver = self._get_latest_frida_version()
            if not latest_ver:
                log_error(LogSource.SYSTEM, "Could not determine latest Frida version. Cannot proceed.")
                return None
            version_to_use = latest_ver
        else:
            self.actual_version = self.version # Use the specified version
            log_info(LogSource.SYSTEM, f"Using specified Frida version: {self.actual_version}")

        expected_binary_name = self.get_expected_server_binary_name(self.actual_version)
        if not expected_binary_name:
            return None
        
        final_binary_path = os.path.join(self.download_dir, expected_binary_name)

        if os.path.exists(final_binary_path):
            log_info(LogSource.SYSTEM, f"Frida server binary already exists: {final_binary_path}")
            return final_binary_path

        log_info(LogSource.SYSTEM, f"Frida server binary not found. Attempting download for version {self.actual_version}...")
        frida_server_url = self._construct_frida_server_url(self.actual_version)
        if not frida_server_url:
            return None

        temp_xz_filename = f"{expected_binary_name}.xz"
        temp_xz_path = os.path.join(self.download_dir, temp_xz_filename)

        if not self._download_file(frida_server_url, temp_xz_path):
            return None

        if not self._extract_xz(temp_xz_path, final_binary_path):
            # Cleanup failed extraction attempt
            if os.path.exists(final_binary_path):
                os.remove(final_binary_path)
            if os.path.exists(temp_xz_path):
                 os.remove(temp_xz_path)
            return None

        # Clean up the .xz file after successful extraction
        try:
            os.remove(temp_xz_path)
            log_info(LogSource.SYSTEM, f"Cleaned up {temp_xz_path}.")
        except OSError as e:
            log_warning(LogSource.SYSTEM, f"Warning: Could not remove temporary file {temp_xz_path}: {e}")

        log_info(LogSource.SYSTEM, f"Frida server successfully downloaded and extracted to: {final_binary_path}")
        return final_binary_path 