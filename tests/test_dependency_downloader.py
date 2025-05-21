import sys
import os
import shutil
sys.path.append('.') # Ensure frida_tower_logger can be imported
from frida_tower_logger import config
from frida_tower_logger.dependency_downloader import DependencyDownloader

# Test parameters
DOWNLOAD_DIR = config.FRIDA_SERVER_DIR
ARCH = config.FRIDA_SERVER_ARCH
# To ensure a fresh download for the first run, and then test caching,
# we can optionally clear the download directory before the first run.
# For this test, we will rely on the downloader's logic to handle existing files.

print(f"--- Test Case 1: Initial Download (version: {config.FRIDA_SERVER_VERSION}) ---")
# Clean up frida_server directory for a clean test of download
if os.path.exists(DOWNLOAD_DIR):
    print(f"Cleaning up existing directory: {DOWNLOAD_DIR}")
    shutil.rmtree(DOWNLOAD_DIR)

downloader = DependencyDownloader(DOWNLOAD_DIR, ARCH, config.FRIDA_SERVER_VERSION)
server_path = downloader.check_and_download_frida_server()

if server_path:
    print(f"Frida server path: {server_path}")
    print(f"Does file exist? {os.path.exists(server_path)}")
    # Verify directory creation
    print(f"Download directory {DOWNLOAD_DIR} exists? {os.path.exists(DOWNLOAD_DIR)}")
else:
    print("Failed to download Frida server.")

print("\n--- Test Case 2: Attempt Download Again (should use cached) ---")
# Re-instantiate or use the same downloader instance
# downloader = DependencyDownloader(DOWNLOAD_DIR, ARCH, config.FRIDA_SERVER_VERSION)
server_path_again = downloader.check_and_download_frida_server()

if server_path_again:
    print(f"Frida server path (2nd attempt): {server_path_again}")
    print(f"Does file exist? {os.path.exists(server_path_again)}")
    if server_path == server_path_again:
        print("Path is the same as the first run, indicating cached version was likely used as expected.")
else:
    print("Failed to get Frida server path on 2nd attempt.")

print("\n--- Test Case 3: Specific Version Download (e.g., 16.0.0) ---")
# Note: This test will download another version if different from 'latest' or the one in config.
# To avoid re-downloading during automated tests, this could be made conditional
# or use a known older, small, specific version for testing purposes.
# For now, let's use a specific version known to exist to test that logic path.
# We will clean the directory again to ensure this specific version is downloaded.

SPECIFIC_VERSION_TO_TEST = "16.1.4" # A recent, specific version
print(f"Cleaning up existing directory for specific version test: {DOWNLOAD_DIR}")
if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR)

specific_downloader = DependencyDownloader(DOWNLOAD_DIR, ARCH, SPECIFIC_VERSION_TO_TEST)
specific_server_path = specific_downloader.check_and_download_frida_server()

if specific_server_path:
    print(f"Specific Frida server path ({SPECIFIC_VERSION_TO_TEST}): {specific_server_path}")
    print(f"Does file exist? {os.path.exists(specific_server_path)}")
    expected_binary_name = f"frida-server-{SPECIFIC_VERSION_TO_TEST}-android-{ARCH}"
    if expected_binary_name in specific_server_path:
        print(f"Binary name {expected_binary_name} matches path as expected.")
    else:
        print(f"Warning: Binary name {expected_binary_name} does not match path {specific_server_path}.")
else:
    print(f"Failed to download specific Frida server version {SPECIFIC_VERSION_TO_TEST}.") 