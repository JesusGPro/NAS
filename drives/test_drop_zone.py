import os
import shutil
import tempfile
from io import BytesIO
from urllib.parse import quote
from unittest.mock import patch, MagicMock
from django.utils.translation import override
from django.conf import settings 
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import JsonResponse
from django.contrib.auth import get_user_model
User = get_user_model()

# Import the check_access function used for mocking
from drives.views import check_access

# --- TEST ENVIRONMENT SETUP ---

@override_settings(NAS_DRIVE_ROOT=tempfile.mkdtemp())
@override_settings(DRIVE_PERMISSIONS={
    'PrivateDrive': {'allowed_users': ['user_a'], 'is_public': False}
})
@patch('drives.views.check_access') 
@override(language='en-us')
class DropzoneUploadTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        """Creates a real user instance in the test database once."""
        cls.user_a = User.objects.create_user(username='user_a', password='testpassword')

    def setUp(self):
        # 1. Setup Client and Login
        self.client = Client()
        self.client.force_login(self.user_a) 

        # 2. Setup Paths
        self.root_path = settings.NAS_DRIVE_ROOT 
        self.user_a_drive = os.path.join(self.root_path, 'PrivateDrive')
        self.user_a_folder = os.path.join(self.user_a_drive, 'user_a')
        
        self.encoded_target_path = quote('PrivateDrive/user_a')

        os.makedirs(self.user_a_folder, exist_ok=True)
        
        # 3. Setup Mock File Content
        self.uploaded_file_name = "test_file.txt"
        self.uploaded_file_content = b"This is test file content."
        
        # 4. Define base POST data structure
        self.post_data_base = {
            'target_path': self.encoded_target_path,
            'relative_path': self.uploaded_file_name,
        }
        
        # 5. Define URLs
        self.upload_url = reverse('drives:dropzone_upload')


    def tearDown(self):
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    def _create_uploaded_file(self):
        """Helper to create a fresh SimpleUploadedFile object for each request."""
        return SimpleUploadedFile(
            self.uploaded_file_name,
            self.uploaded_file_content,
            content_type="text/plain"
        )

    # --- TEST CASES ---

    def test_only_post_allowed(self, mock_check_access):
        """
        Tests that only POST requests are allowed (expect 405).
        """
        # GET request
        response = self.client.get(self.upload_url)
        
        self.assertEqual(response.status_code, 405) # Expected 405 Method Not Allowed

        # Assert JSON content
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('Must be POST request', response.json().get('error', ''))
        
    def test_successful_file_upload(self, mock_check_access):
        """
        Tests successful file upload with full modification permission (expect 200).
        This test should pass once the NameError in views.py is fixed.
        """
        # 1. Setup Mock
        mock_check_access.return_value = (True, True) 
        
        # Prepare POST data, recreating the file object
        post_data = self.post_data_base.copy()
        post_data['file'] = self._create_uploaded_file()

        # 2. Action: Perform the POST request
        response = self.client.post(
            self.upload_url, 
            post_data,
            follow=False,
            enforce_csrf_checks=False 
        )
        
        # 3. Assert Response: Expect 200 OK
        self.assertEqual(response.status_code, 200) 
        self.assertEqual(response.json().get('message'), 'File uploaded successfully')
        
        # 4. Assert File System
        expected_path = os.path.join(self.user_a_folder, self.uploaded_file_name)
        self.assertTrue(os.path.exists(expected_path))
        
        # 5. Assert Content
        with open(expected_path, 'rb') as f:
            self.assertEqual(f.read(), self.uploaded_file_content)