import os
import shutil
import tempfile
from urllib.parse import quote
from unittest.mock import patch, MagicMock

from django.conf import settings 
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils.translation import override
from django.core.files.uploadedfile import SimpleUploadedFile # Crucial for simulating files
from django.contrib.messages import get_messages

User = get_user_model()

# Assuming check_access is imported from drives.views
# We patch it to control permissions easily.
@override_settings(NAS_DRIVE_ROOT=tempfile.mkdtemp())
@override_settings(DRIVE_PERMISSIONS={
    # Define a drive structure that the user is allowed to access
    'TestDrive': {'allowed_users': ['test_user'], 'is_public': False}
})
@patch('drives.views.check_access') 
@override(language='en-us')
class UploadFileViewTests(TestCase):

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
        self.test_drive_path = os.path.join(self.root_path, 'TestDrive')
        self.target_folder = os.path.join(self.test_drive_path, 'uploads')
        
        # Create the directory where the file will be uploaded
        os.makedirs(self.target_folder, exist_ok=True)
        
        # 3. Define Request Components
        # The path parameter sent in the POST request (encoded)
        self.target_path_param = 'TestDrive/uploads'
        self.target_path_encoded = quote(self.target_path_param) 
        
        # The URL for the upload view
        self.url = reverse('drives:upload_file')
        
        # The expected redirect location upon success (the content view for the target path)
        self.expected_redirect_url = reverse('drives:drive_content', kwargs={'path': self.target_path_encoded})

        # 4. Create a dummy file object
        self.file_content = b'This is the content of the test file.'
        self.test_file_name = 'test_upload.txt'
        self.uploaded_file = SimpleUploadedFile(
            self.test_file_name,
            self.file_content,
            content_type='text/plain'
        )

    def tearDown(self):
        # Clean up the temporary directory after each test
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    # --- Test Cases ---

    def test_01_successful_file_upload(self, mock_check_access):
        """Tests a successful file upload to a directory where the user has modify access."""
        
        # Arrange: Set mock to grant modify access (True, True)
        mock_check_access.return_value = (True, True) 

        # The path where we expect the file to land on the file system
        expected_fs_path = os.path.join(self.target_folder, self.test_file_name)

        # Act: Send the POST request with the file and target path
        response = self.client.post(self.url, {
            'file_upload': self.uploaded_file,
            'target_path': self.target_path_encoded, # Use the URL encoded path
        })

        # Assert 1: Redirection (302)
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check
        self.assertTrue(os.path.exists(expected_fs_path), "File was not created on the file system.")
        with open(expected_fs_path, 'rb') as f:
            self.assertEqual(f.read(), self.file_content, "File content mismatch after upload.")

        # Assert 3: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any('uploaded successfully' in str(m) for m in messages), "Success message was not found.")

    def test_02_permission_denied_blocks_upload(self, mock_check_access):
        """
        Tests that a user lacking modify permission is blocked from uploading.
        Expected: Redirect (302) and no file written to disk.
        """
        # Arrange 1: Set mock to deny modify access (View=True, Modify=False)
        # This triggers the 'if not can_modify' block in the view.
        mock_check_access.return_value = (True, False)

        # Arrange 2: Define expected path (where the file should NOT be)
        expected_fs_path = os.path.join(self.target_folder, self.test_file_name)

        # Act: Send the POST request with the file and target path
        response = self.client.post(self.url, {
            'file_upload': self.uploaded_file,
            'target_path': self.target_path_encoded,  # Use the URL encoded path
        })

        # Assert 1: Redirection (302) back to the original content page
        # This verifies the view executed its 'return redirect(...)' logic.
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check
        self.assertFalse(os.path.exists(expected_fs_path), "File should not be created on the file system.")

        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        self.assertTrue(any('Permission denied' in msg for msg in error_messages), 
                    "Permission denied error message was not found.")


    def test_03_directory_traversal_attack_is_blocked(self, mock_check_access):
        """Tests the security check against directory traversal attempts (e.g., using '../..')."""
        
        # Arrange 1: Set mock to grant modify access (Test boundary check, not permission check)
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create a malicious path that tries to escape the root directory
        malicious_path_param = 'TestDrive/uploads/../../malicious/path'
        malicious_path_encoded = quote(malicious_path_param)
        
        # The calculated path where the file would land if the attack succeeded
        expected_fs_path = os.path.join(self.root_path, 'malicious', 'path', self.test_file_name)

        # Act: Send the POST request with the malicious path
        response = self.client.post(self.url, {
            'file_upload': self.uploaded_file,
            'target_path': malicious_path_encoded, 
        })
        
        # Assert 1: Redirection (302)
        invalid_redirect_url = reverse('drives:drive_content', kwargs={'path': malicious_path_encoded})
        self.assertRedirects(response, invalid_redirect_url, status_code=302, target_status_code=200)
        
        # Assert 2: File System Check (Crucial: Assert the file was NOT written)
        self.assertFalse(os.path.exists(expected_fs_path), "Security: File was written outside the drive boundary.")

        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        self.assertTrue(any('outside the drive boundary' in msg for msg in error_messages), 
                        "Security error message was not found.")