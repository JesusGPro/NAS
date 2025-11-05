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

@patch('drives.views.check_access') 
@override_settings(NAS_DRIVE_ROOT=tempfile.mkdtemp())
@override_settings(DRIVE_PERMISSIONS={
    'TestDrive': {'allowed_users': ['test_user'], 'is_public': False}
})
@override(language='en-us')
class UncompressViewTests(TestCase):

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
        
        # The target folder where the ZIP content will be extracted
        self.destination_folder_name = 'destination'
        self.abs_destination_dir = os.path.join(self.test_drive_path, self.destination_folder_name)
        os.makedirs(self.abs_destination_dir, exist_ok=True)
        
        # The source folder where the ZIP file lives
        self.source_folder_name = 'source'
        self.abs_source_dir = os.path.join(self.test_drive_path, self.source_folder_name)
        os.makedirs(self.abs_source_dir, exist_ok=True)
        
        # 3. Define Request Components
        # Path of the destination directory (e.g., 'TestDrive/destination')
        self.current_path_param = f'{self.drive_name}/{self.destination_folder_name}'
        self.current_path_encoded = quote(self.current_path_param) 
        
        # The URL for the uncompression view
        self.url = reverse('drives:uncompress_item')
        
        # The expected redirect URL (to the destination folder)
        self.expected_redirect_url = reverse('drives:drive_content', kwargs={'path': self.current_path_encoded})

    def tearDown(self):
        # Cleans up the temporary directory after each test
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    # --- Utility to create a temporary ZIP file ---
    def create_test_zip(self, zip_filename, content_structure):
        """
        Creates a ZIP file at the source directory.
        content_structure: A dictionary mapping desired archive path to content.
        """
        abs_zip_path = os.path.join(self.abs_source_dir, zip_filename)
        with zipfile.ZipFile(abs_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for arcname, content in content_structure.items():
                # Create a temporary file to write content before adding to zip
                temp_file = os.path.join(tempfile.gettempdir(), os.path.basename(arcname))
                with open(temp_file, 'wb') as f:
                    f.write(content)
                # Add the temporary file to the zip with the desired arcname
                zipf.write(temp_file, arcname=arcname)
        return abs_zip_path

    # --- Test Cases ---

    def test_01_successful_uncompression_of_single_folder_zip(self, mock_check_access):
        """Tests successful extraction of a ZIP containing a single, nested folder structure."""
        
        # Arrange 1: Setup Permissions
        # Extraction requires modify permission (True, True)
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create the ZIP file structure
        zip_filename = 'Archive_Project.zip'
        extracted_root_name = 'ProjectData'
        
        # Define the structure inside the ZIP
        zip_content_structure = {
            f'{extracted_root_name}/README.txt': b'Project documentation.',
            f'{extracted_root_name}/src/main.py': b'print("Hello")',
            f'{extracted_root_name}/config/settings.json': b'{}',
        }
        
        # Create the ZIP file and get its full path relative to NAS_DRIVE_ROOT
        abs_zip_path = self.create_test_zip(zip_filename, zip_content_structure)
        relative_zip_path = os.path.join(self.source_folder_name, zip_filename)
        
        # Expected files in the destination directory after extraction
        expected_readme_path = os.path.join(self.abs_destination_dir, extracted_root_name, 'README.txt')
        expected_main_py_path = os.path.join(self.abs_destination_dir, extracted_root_name, 'src', 'main.py')
        
        # Act: Send the POST request
        response = self.client.post(self.url, {
            'current_path': self.current_path_param,
            'zip_path': relative_zip_path, # Path of the ZIP file to extract
        })

        # Assert 1: Redirection (302) back to the content view
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify the files were extracted correctly)
        self.assertTrue(os.path.exists(expected_readme_path), "README.txt was not extracted correctly.")
        self.assertTrue(os.path.exists(expected_main_py_path), "Nested main.py was not extracted correctly.")
        
        # Assert 3: Content Check (Verify one file's content)
        with open(expected_readme_path, 'rb') as f:
            self.assertEqual(f.read(), b'Project documentation.', "Extracted file content is incorrect.")

        # Assert 4: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        
        expected_message_part = f"Successfully extracted 3 files/folders from '{zip_filename}' into the folder: {extracted_root_name}."
        
        self.assertTrue(any(expected_message_part in str(m) for m in messages), 
                        f"Success message not found or incorrect. Expected part: '{expected_message_part}'")