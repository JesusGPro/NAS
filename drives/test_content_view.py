import os
import shutil
import tempfile
from urllib.parse import quote
from unittest.mock import patch
from django.utils.translation import override
from django.conf import settings 
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
User = get_user_model()

# Import the check_access function used for mocking (assuming it's in drives.views)
from drives.views import check_access # This is the target of the patch

# --- TEST ENVIRONMENT SETUP ---

@override_settings(NAS_DRIVE_ROOT=tempfile.mkdtemp())
@override_settings(DRIVE_PERMISSIONS={
    # Define a drive structure that the user is allowed to access
    'TestDrive': {'allowed_users': ['test_user'], 'is_public': False}
})
@patch('drives.views.check_access') 
@override(language='en-us')
class DriveContentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        """Creates a user instance in the test database."""
        # Create a test user
        cls.user = User.objects.create_user(username='test_user', password='testpassword')

    def setUp(self):
        # 1. Setup Client and Login
        self.client = Client()
        self.client.force_login(self.user) 

        # 2. Setup Paths
        self.root_path = settings.NAS_DRIVE_ROOT 
        self.test_drive_path = os.path.join(self.root_path, 'TestDrive')
        self.test_user_folder = os.path.join(self.test_drive_path, 'test_user_folder')
        
        # Create necessary directories
        os.makedirs(self.test_user_folder, exist_ok=True)
        
        # Create a file and a sub-folder for content testing
        self.test_file_name = "report.txt"
        self.test_subfolder_name = "data_backup"
        
        with open(os.path.join(self.test_user_folder, self.test_file_name), 'w') as f:
            f.write("Test content")
        
        os.makedirs(os.path.join(self.test_user_folder, self.test_subfolder_name), exist_ok=True)
        
        # 3. Define Base URL Components
        self.base_path = 'TestDrive/test_user_folder'
        self.base_path_encoded = quote(self.base_path)
        self.url = reverse('drives:drive_content', kwargs={'path': self.base_path_encoded})

    def tearDown(self):
        # Clean up the temporary directory after each test
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    def test_01_successful_view_and_content_listing(self, mock_check_access):
        """Tests that the view renders correctly and lists files/folders inside the directory."""
        
        # Arrange: Set mock to grant both view and modify access
        mock_check_access.return_value = (True, True) 

        # Act: Make the request
        response = self.client.get(self.url)

        # Assert 1: Status Code and Template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'drive_content.html')
        
        # Assert 2: Context Data (File/Folder List)
        # The view should find 1 file ('report.txt') and 1 folder ('data_backup')
        self.assertIn('items', response.context)
        items = response.context['items']
        
        # Verify the count
        self.assertEqual(len(items), 2)
        
        # Verify specific items are present and categorized correctly
        item_names = [item['name'] for item in items]
        
        # Check for the folder
        self.assertIn(self.test_subfolder_name, item_names)
        folder_item = next(item for item in items if item['name'] == self.test_subfolder_name)
        self.assertEqual(folder_item['type'], 'dir')
        
        # Check for the file
        self.assertIn(self.test_file_name, item_names)
        file_item = next(item for item in items if item['name'] == self.test_file_name)
        self.assertEqual(file_item['type'], 'file')

    def test_02_permission_denied_for_view_access(self, mock_check_access):
        """Tests that a 403 Forbidden is returned when user lacks view permission."""
        
        # Arrange: Set mock to deny both view and modify access
        mock_check_access.return_value = (False, False) 

        # Act: Make the request
        response = self.client.get(self.url)

        # Assert: Expect 403 Forbidden (PermissionDenied exception handling)
        self.assertEqual(response.status_code, 403)

    def test_03_accessing_a_file_redirects_to_download(self, mock_check_access):
        """Tests that attempting to view a file path redirects (302), assuming redirection to a download view."""
        
        # Arrange 1: Set mock to grant access
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Define the URL for the file (not the folder)
        file_path = f'{self.base_path}/{self.test_file_name}' # e.g., 'TestDrive/test_user_folder/report.txt'
        file_path_encoded = quote(file_path)
        file_url = reverse('drives:drive_content', kwargs={'path': file_path_encoded})

        # Act: Make the request
        # We use follow=False to stop the client from following the redirect,
        # allowing us to check the initial 302 status code.
        response = self.client.get(file_url, follow=False)

        # Assert: Expect a 302 Redirect
        self.assertEqual(response.status_code, 302)
        
        # Optional: Assert the redirection target (where Django is telling the client to go)
        # Assuming your file download view is called 'file_download' or similar
        # If your URL structure uses a separate view, you can check the 'Location' header
        # self.assertTrue('Location' in response, "Response should contain a Location header for redirect.")

    def test_04_non_existent_path_returns_404(self, mock_check_access):
        """Tests that accessing a path that doesn't exist returns a 404 Not Found error."""
        
        # Arrange 1: Set mock to grant access (ensuring failure is due to path, not permission)
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Define a URL for a non-existent directory
        non_existent_path = f'{self.base_path}/does_not_exist_xyz' 
        non_existent_path_encoded = quote(non_existent_path)
        non_existent_url = reverse('drives:drive_content', kwargs={'path': non_existent_path_encoded})

        # Act: Make the request
        response = self.client.get(non_existent_url)

        # Assert: Expect 404 Not Found
        self.assertEqual(response.status_code, 404)

    def test_05_unauthenticated_access_redirects_to_login(self, mock_check_access):
        """
        Tests that an unauthenticated user attempting to access the view is 
        redirected (302) to the login page.
        """
        
        # Arrange: Use a fresh, unauthenticated client instance
        unauthenticated_client = Client()
        
        # We don't need to mock check_access because @login_required intercepts first.
        # However, the mock argument is still required due to the class decorator.
        
        # Act: Make the request using the unauthenticated client
        response = unauthenticated_client.get(self.url, follow=False)

        # Assert 1: Expect 302 Redirect
        self.assertEqual(response.status_code, 302)
        
        # Assert 2 (Optional but Recommended): Check the redirection target
        # Django redirects unauthenticated users to settings.LOGIN_URL, 
        # usually appending the 'next' parameter.
        expected_redirect_url = settings.LOGIN_URL + f'?next={self.url}'
        
         # Use assertRedirects to verify the redirect URL
        self.assertRedirects(response, expected_redirect_url, status_code=302, target_status_code=200)
