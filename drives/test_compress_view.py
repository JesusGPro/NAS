import os
import shutil
import tempfile
import zipfile
from urllib.parse import quote
from unittest.mock import patch

from django.conf import settings 
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils.translation import override
from django.contrib.messages import get_messages

User = get_user_model()

# --- TEST ENVIRONMENT SETUP ---

# Define a decorator to mock the access permissions check.
@patch('drives.views.check_access') 
@override_settings(NAS_DRIVE_ROOT=tempfile.mkdtemp())
@override_settings(DRIVE_PERMISSIONS={
    # Define a simple drive structure for testing
    'TestDrive': {'allowed_users': ['test_user'], 'is_public': False}
})
@override(language='en-us')
class BulkCompressViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        """Creates a user instance in the test database."""
        cls.user = User.objects.create_user(username='test_user', password='testpassword')

    def setUp(self):
        # 1. Setup Client and Login
        self.client = Client()
        self.client.force_login(self.user) 
        
        # 2. Setup Paths
        self.root_path = settings.NAS_DRIVE_ROOT 
        self.drive_name = 'TestDrive'
        self.test_drive_path = os.path.join(self.root_path, self.drive_name)
        
        # The directory where the ZIP will be created (e.g., 'TestDrive/projects')
        self.target_folder_name = 'projects'
        self.target_folder_fs = os.path.join(self.test_drive_path, self.target_folder_name)
        os.makedirs(self.target_folder_fs, exist_ok=True)
        
        # 3. Define Request Components
        # The path parameter passed in the URL for the redirect (e.g., 'TestDrive/projects')
        self.target_path_param = f'{self.drive_name}/{self.target_folder_name}'
        self.target_path_encoded = quote(self.target_path_param) 
        
        # The URL for the compression view
        self.url = reverse('drives:compress_items')
        
        # The expected redirect URL (to the current folder)
        self.expected_redirect_url = reverse('drives:drive_content', kwargs={'path': self.target_path_encoded})


    def tearDown(self):
        # Cleans up the temporary directory after each test
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    # --- Test Cases ---

    def test_01_successful_compression_of_single_file(self, mock_check_access):
        """Tests the successful compression of a single file, including handling of spaces."""
        
        # Arrange 1: Setup Permissions
        # Compression requires modify permission (True, True)
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create a file with spaces in the name
        file_name = 'My Important File.pdf'
        file_content = b'Content of the PDF file.'
        
        # Absolute path on the file system
        abs_file_path = os.path.join(self.target_folder_fs, file_name)
        # Relative path to NAS_DRIVE_ROOT, which is sent from the front-end (URL-encoded)
        item_path_encoded = quote(f'{self.drive_name}/{self.target_folder_name}/{file_name}')

        # Write the actual file to disk so os.path.exists() can find it
        with open(abs_file_path, 'wb') as f:
            f.write(file_content)

        # Arrange 3: Expected ZIP file details
        # The expected ZIP name should be based on the file name and not be encoded.
        expected_zip_name = 'My Important File.pdf.zip'
        expected_zip_path = os.path.join(self.target_folder_fs, expected_zip_name)
        
        # Act: Send the POST request
        response = self.client.post(self.url, {
            'current_path': self.target_path_param,
            'item_paths': item_path_encoded, # The encoded path sent
        })

        # Assert 1: Redirection (302) back to the content view
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify the ZIP file exists)
        self.assertTrue(os.path.exists(expected_zip_path), f"ZIP file was not created: {expected_zip_path}")
        
        # Assert 3: ZIP Content Check
        with zipfile.ZipFile(expected_zip_path, 'r') as zipf:
            # There should be exactly one entry inside the ZIP with the original name
            self.assertEqual(len(zipf.namelist()), 1, "ZIP file should contain exactly one entry.")
            # The name inside the ZIP should be just the file's basename
            self.assertIn(file_name, zipf.namelist())
            
            # Verify the content of the file inside the ZIP
            with zipf.open(file_name, 'r') as extracted_file:
                self.assertEqual(extracted_file.read(), file_content, "Content inside ZIP is incorrect.")

        # Assert 4: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Successfully compressed 1 item(s) into 'My Important File.pdf.zip'." in str(m) for m in messages), 
                        "Success message was not found or incorrect.")

    def test_02_successful_bulk_compression_of_multiple_items(self, mock_check_access):
        """Tests successful bulk compression of two files and one folder, ensuring correct ZIP structure."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Setup file system structure inside target_folder_fs
        
        # Item 1: Simple File
        file_a_name = 'Document A.txt'
        file_a_content = b'Content of Document A.'
        abs_file_a_path = os.path.join(self.target_folder_fs, file_a_name)
        with open(abs_file_a_path, 'wb') as f: f.write(file_a_content)

        # Item 2: Another Simple File
        file_b_name = 'Image B.jpg'
        file_b_content = b'Content of Image B.'
        abs_file_b_path = os.path.join(self.target_folder_fs, file_b_name)
        with open(abs_file_b_path, 'wb') as f: f.write(file_b_content)

        # Item 3: Folder with a nested file
        folder_c_name = 'Data Folder'
        nested_file_name = 'config.ini'
        nested_file_content = b'[Settings] Test=1'
        
        abs_folder_c_path = os.path.join(self.target_folder_fs, folder_c_name)
        abs_nested_file_path = os.path.join(abs_folder_c_path, nested_file_name)
        os.makedirs(abs_folder_c_path)
        with open(abs_nested_file_path, 'wb') as f: f.write(nested_file_content)

        # Arrange 3: Define the list of item paths to send (URL-encoded)
        # All paths are relative to the NAS_DRIVE_ROOT
        item_paths_list = [
            f'{self.drive_name}/{self.target_folder_name}/{file_a_name}',
            f'{self.drive_name}/{self.target_folder_name}/{file_b_name}',
            f'{self.drive_name}/{self.target_folder_name}/{folder_c_name}',
        ]
        
        # Join and URL-encode the list for the POST request
        item_paths_encoded_str = ','.join(quote(p) for p in item_paths_list)

        # Arrange 4: Expected ZIP file details
        # For bulk compression, the ZIP name uses the timestamp prefix
        zip_prefix = 'archive_' 
        expected_zip_path_prefix = os.path.join(self.target_folder_fs, zip_prefix)
        
        # Act: Send the POST request
        response = self.client.post(self.url, {
            'current_path': self.target_path_param,
            'item_paths': item_paths_encoded_str, # The encoded, comma-separated paths
        })

        # Assert 1: Redirection
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Find the dynamically named ZIP file)
        # Check the directory for a file starting with 'archive_'
        zip_files = [f for f in os.listdir(self.target_folder_fs) if f.startswith(zip_prefix) and f.endswith('.zip')]
        self.assertEqual(len(zip_files), 1, "Exactly one bulk ZIP file should be created.")
        actual_zip_name = zip_files[0]
        actual_zip_path = os.path.join(self.target_folder_fs, actual_zip_name)
        
        self.assertTrue(os.path.exists(actual_zip_path), f"ZIP file not found at: {actual_zip_path}")
        
        # Assert 3: ZIP Content Check
        with zipfile.ZipFile(actual_zip_path, 'r') as zipf:
            # Expected contents: File A, File B, Folder C (as directory entry), and Nested File
            expected_names = {
                file_a_name, 
                file_b_name, 
                f'{folder_c_name}/', # Folder entry
                f'{folder_c_name}/{nested_file_name}', # Nested file entry
            }
            
            # The names returned by zipf.namelist() might include the folder entry (with trailing /)
            actual_names = set(zipf.namelist())

            # We expect a total of 4 entries (2 files, 1 folder entry, 1 nested file)
            self.assertEqual(len(actual_names), 4, f"Expected 4 entries in ZIP, got {len(actual_names)}: {actual_names}")
            self.assertEqual(actual_names, expected_names, "ZIP contents do not match expected items.")
            
            # Verify the content of a nested file
            with zipf.open(f'{folder_c_name}/{nested_file_name}', 'r') as extracted_file:
                self.assertEqual(extracted_file.read(), nested_file_content, "Nested file content is incorrect.")
            
        # Assert 4: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any(f"Successfully compressed 3 item(s) into '{actual_zip_name}'." in str(m) for m in messages), 
                        "Success message was not found or did not reflect 3 items compressed.")

    def test_03_permission_denied_blocks_compression(self, mock_check_access):
        """Tests that compression is blocked and no ZIP file is created if the user lacks modify permission."""
        
        # Arrange 1: Set mock to deny modify access (View=True, Modify=False)
        mock_check_access.return_value = (True, False) 

        # Arrange 2: Create a file that the user attempts to compress
        file_name = 'forbidden_file.log'
        file_content = b'This should not be zipped.'
        abs_file_path = os.path.join(self.target_folder_fs, file_name)
        with open(abs_file_path, 'wb') as f:
            f.write(file_content)

        # Define the item path (encoded) and the expected ZIP name
        item_path_encoded = quote(f'{self.drive_name}/{self.target_folder_name}/{file_name}')
        expected_zip_name = 'forbidden_file.log.zip'
        expected_zip_path = os.path.join(self.target_folder_fs, expected_zip_name)
        
        # Act: Send the POST request
        response = self.client.post(self.url, {
            'current_path': self.target_path_param,
            'item_paths': item_path_encoded, 
        })

        # Assert 1: Redirection (302) back to the content view
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify the ZIP file was NOT created)
        self.assertFalse(os.path.exists(expected_zip_path), 
                         "Security violation: ZIP file was created despite lacking modify permission.")
        
        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        self.assertTrue(any('Permission denied' in msg for msg in error_messages), 
                        "Permission denied error message was not found.")