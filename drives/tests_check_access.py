import os
from django.test import TestCase
from unittest.mock import patch, MagicMock

# --- THIS IS THE CRITICAL CHANGE ---
# Import the function directly from the views file
from drives.views import check_access 
# -----------------------------------

# Define our mock settings and users (keep this section the same)
MOCK_NAS_ROOT = '/mnt/nas_data'
MOCK_DRIVE_PERMISSIONS = {
    'PublicDrive': {
        'allowed_users': ['user_a', 'user_b', 'superuser_s'],
        'is_public': True
    },
    'PrivateDrive': {
        'allowed_users': ['user_a', 'superuser_s'],
        'is_public': False
    }
}

# Helper function to create a mock user object (keep this section the same)
def create_mock_user(username, is_superuser=False):
    user = MagicMock()
    user.username = username
    user.is_superuser = is_superuser
    return user

@patch.dict('django.conf.settings.DRIVE_PERMISSIONS', MOCK_DRIVE_PERMISSIONS)
@patch('django.conf.settings.NAS_DRIVE_ROOT', MOCK_NAS_ROOT)
# The class name must inherit from TestCase and is typically named ending with 'Tests'
class CheckAccessTests(TestCase): 
    def setUp(self):
        # Standard User
        self.user_a = create_mock_user('user_a', is_superuser=False)
        # Unauthorized User
        self.user_c = create_mock_user('user_c', is_superuser=False)
        # Superuser
        self.superuser = create_mock_user('superuser_s', is_superuser=True)
        
        # Define base drive path
        self.base_drive = 'PrivateDrive'
        self.user_a_dedicated_folder = os.path.join(MOCK_NAS_ROOT, self.base_drive, 'user_a')
        self.other_user_folder = os.path.join(MOCK_NAS_ROOT, self.base_drive, 'user_b')
        self.drive_root_path = os.path.join(MOCK_NAS_ROOT, self.base_drive)

    # All methods to be executed must start with 'test_'

    ## A. Superuser Test
    def test_superuser_access_is_always_granted(self):
        can_view, can_modify = check_access(self.superuser, self.other_user_folder)
        self.assertTrue(can_view and can_modify)

    ## B. Standard User - Dedicated Folder Access (Full Access)
    def test_standard_user_full_access_at_dedicated_folder(self):
        can_view, can_modify = check_access(self.user_a, self.user_a_dedicated_folder)
        self.assertTrue(can_view and can_modify)

    ## C. Standard User - Drive Root Access (View Only)
    def test_standard_user_view_only_at_drive_root(self):
        can_view, can_modify = check_access(self.user_a, self.drive_root_path)
        self.assertTrue(can_view and not can_modify)

    ## D. Standard User - Restricted Access (Deny)
    def test_standard_user_denied_access_to_other_users_folder(self):
        can_view, can_modify = check_access(self.user_a, self.other_user_folder)
        self.assertFalse(can_view or can_modify)


