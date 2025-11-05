import os
import shutil
import tempfile
from urllib.parse import quote, unquote
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
    # Define a drive structure that the user is allowed to access
    'TestDrive': {'allowed_users': ['test_user'], 'is_public': False}
})
@override(language='en-us')
class FileManipulationViewTests(TestCase):

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
        
        # The working folder where items will be created/manipulated
        self.working_folder_name = 'WorkingFolder'
        self.abs_working_dir = os.path.join(self.test_drive_path, self.working_folder_name)
        os.makedirs(self.abs_working_dir, exist_ok=True)
        
        # 3. Define Request Components
        # The path to the parent directory, used for redirection (e.g., 'TestDrive/WorkingFolder')
        self.parent_path_param = f'{self.drive_name}/{self.working_folder_name}'
        self.parent_path_encoded = quote(self.parent_path_param)
        
        # The expected redirect URL (to the current folder after operation)
        self.expected_redirect_url = reverse('drives:drive_content', kwargs={'path': self.parent_path_encoded})

        # Define URLs
        self.rename_url = reverse('drives:rename_item')
        self.delete_url = reverse('drives:delete_item')

    def tearDown(self):
        # Cleans up the temporary directory after each test
        shutil.rmtree(settings.NAS_DRIVE_ROOT)

    # ====================================================================
    # RENAME ITEM TESTS
    # ====================================================================

    def test_01_successful_rename_of_file(self, mock_check_access):
        """Tests that a file is successfully renamed and the correct feedback message is generated."""
        
        # Arrange 1: Setup Permissions
        # Renaming requires modify permission on the parent directory
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create the initial file
        old_file_name = 'draft_v1.txt'
        new_file_name = 'final_report.txt'
        
        abs_old_path = os.path.join(self.abs_working_dir, old_file_name)
        abs_new_path = os.path.join(self.abs_working_dir, new_file_name)
        
        with open(abs_old_path, 'w') as f:
            f.write("Initial content.")

        # The path parameter sent to the view, relative to NAS_DRIVE_ROOT and URL-encoded
        encoded_old_path_param = quote(f'{self.drive_name}/{self.working_folder_name}/{old_file_name}')
        
        # Act: Send the POST request
        response = self.client.post(self.rename_url, {
            'old_path': encoded_old_path_param,
            'new_name': new_file_name,
        })

        # Assert 1: Redirection (302) back to the parent directory
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify rename occurred)
        self.assertFalse(os.path.exists(abs_old_path), "Old file path should no longer exist.")
        self.assertTrue(os.path.exists(abs_new_path), "New file path should exist.")
        
        # Assert 3: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        expected_message = f"Item successfully renamed from '{old_file_name}' to '{new_file_name}'."
        self.assertTrue(any(expected_message in str(m) for m in messages), 
                        "Success message was not found or incorrect.")

    def test_02_rename_fails_on_permission_denial(self, mock_check_access):
        """Tests that renaming is blocked when the user lacks modify permission."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, False) # View=True, Modify=False

        # Arrange 2: Create the initial file (will not be renamed)
        old_file_name = 'secret_file.txt'
        new_file_name = 'new_name.txt'
        abs_old_path = os.path.join(self.abs_working_dir, old_file_name)
        with open(abs_old_path, 'w') as f:
            f.write("Content.")

        encoded_old_path_param = quote(f'{self.drive_name}/{self.working_folder_name}/{old_file_name}')
        
        # Act: Send the POST request
        response = self.client.post(self.rename_url, {
            'old_path': encoded_old_path_param,
            'new_name': new_file_name,
        })

        # Assert 1: Redirection (302)
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify no rename occurred)
        abs_new_path = os.path.join(self.abs_working_dir, new_file_name)
        self.assertTrue(os.path.exists(abs_old_path), "Original file should still exist.")
        self.assertFalse(os.path.exists(abs_new_path), "New file path should not exist.")
        
        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        self.assertTrue(any('Permission denied' in msg for msg in error_messages), 
                        "Permission denied error message was not found.")

    def test_03_rename_fails_if_target_exists(self, mock_check_access):
        """Tests that renaming to an already existing name is blocked."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create both the source and destination files
        source_name = 'source.txt'
        target_name = 'target.txt'
        
        abs_source_path = os.path.join(self.abs_working_dir, source_name)
        abs_target_path = os.path.join(self.abs_working_dir, target_name)
        
        with open(abs_source_path, 'w') as f: f.write("Source content.")
        with open(abs_target_path, 'w') as f: f.write("Target content.") # This file exists
        
        encoded_source_path_param = quote(f'{self.drive_name}/{self.working_folder_name}/{source_name}')
        
        # Act: Attempt to rename source.txt to target.txt
        response = self.client.post(self.rename_url, {
            'old_path': encoded_source_path_param,
            'new_name': target_name,
        })

        # Assert 1: Redirection (302)
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify no rename occurred)
        self.assertTrue(os.path.exists(abs_source_path), "Source file should still exist.")
        self.assertTrue(os.path.exists(abs_target_path), "Target file should still exist.")

        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        expected_error = f"An item named '{target_name}' already exists in this location."
        self.assertTrue(any(expected_error in msg for msg in error_messages), 
                        "Existing item error message was not found.")


    # ====================================================================
    # DELETE ITEM TESTS
    # ====================================================================

    def test_04_successful_deletion_of_folder_with_contents(self, mock_check_access):
        """Tests that a folder and all its contents are successfully deleted using shutil.rmtree."""
        
        # Arrange 1: Setup Permissions
        # Deletion requires modify permission on the parent directory
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create a folder with nested content
        folder_to_delete_name = 'DataBackup'
        abs_folder_path = os.path.join(self.abs_working_dir, folder_to_delete_name)
        abs_nested_file_path = os.path.join(abs_folder_path, 'temp.log')
        
        os.makedirs(abs_folder_path)
        with open(abs_nested_file_path, 'w') as f:
            f.write("Log content.")

        # The path parameter sent to the view, relative to NAS_DRIVE_ROOT and URL-encoded
        encoded_target_path_param = quote(f'{self.drive_name}/{self.working_folder_name}/{folder_to_delete_name}')
        
        # Act: Send the POST request
        response = self.client.post(self.delete_url, {
            'target_path': encoded_target_path_param,
        })

        # Assert 1: Redirection (302) back to the parent directory
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify folder and contents were deleted)
        self.assertFalse(os.path.exists(abs_folder_path), "Folder path should no longer exist.")
        self.assertFalse(os.path.exists(abs_nested_file_path), "Nested file path should no longer exist.")
        
        # Assert 3: Message Check (Success message)
        messages = list(get_messages(response.wsgi_request))
        expected_message = f"Folder '{folder_to_delete_name}' and its contents deleted successfully."
        self.assertTrue(any(expected_message in str(m) for m in messages), 
                        "Success message was not found or incorrect.")

    def test_05_delete_fails_on_permission_denial(self, mock_check_access):
        """Tests that deletion is blocked when the user lacks modify permission."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, False) # View=True, Modify=False

        # Arrange 2: Create the file that the user attempts to delete
        file_to_delete_name = 'protected_doc.pdf'
        abs_file_path = os.path.join(self.abs_working_dir, file_to_delete_name)
        with open(abs_file_path, 'w') as f:
            f.write("Protected content.")

        encoded_target_path_param = quote(f'{self.drive_name}/{self.working_folder_name}/{file_to_delete_name}')
        
        # Act: Send the POST request
        response = self.client.post(self.delete_url, {
            'target_path': encoded_target_path_param,
        })

        # Assert 1: Redirection (302)
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Verify no deletion occurred)
        self.assertTrue(os.path.exists(abs_file_path), "File should still exist.")
        
        # Assert 3: Message Check (Error message)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        self.assertTrue(any('Permission denied' in msg for msg in error_messages), 
                        "Permission denied error message was not found.")

    # ====================================================================
    # PASTE ITEM TESTS
    # ====================================================================

    def test_06_successful_copy_file(self, mock_check_access):
        """Tests successful 'copy' operation for a file."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) # Target Modify = True

        # Arrange 2: Create Source and Destination
        source_file_name = 'source.doc'
        target_folder_name = 'destination'
        abs_source_path = os.path.join(self.abs_working_dir, source_file_name)
        abs_target_dir = os.path.join(self.abs_working_dir, target_folder_name)
        os.makedirs(abs_target_dir, exist_ok=True)
        
        file_content = b'Original content.'
        with open(abs_source_path, 'wb') as f:
            f.write(file_content)

        # Arrange 3: Setup Session
        relative_source_path = f'{self.drive_name}/{self.working_folder_name}/{source_file_name}'
        encoded_source_path = quote(relative_source_path)
        
        session = self.client.session
        session['file_operation'] = {
            'source_items': [encoded_source_path],
            'type': 'copy',
        }
        session.save()

        # Act: Send POST request to paste
        target_path_param = f'{self.drive_name}/{self.working_folder_name}/{target_folder_name}'
        response = self.client.post(self.paste_url, {
            'target_path': target_path_param,
        })
        
        abs_destination_path = os.path.join(abs_target_dir, source_file_name)

        # Assert 1: Redirection
        expected_redirect = reverse('drives:drive_content', kwargs={'path': quote(target_path_param)})
        self.assertRedirects(response, expected_redirect, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Source must remain, Destination must exist)
        self.assertTrue(os.path.exists(abs_source_path), "Source file should still exist (Copy operation).")
        self.assertTrue(os.path.exists(abs_destination_path), "Destination file must exist.")
        
        # Assert 3: Message Check (Success)
        messages = list(get_messages(response.wsgi_request))
        expected_message = "Successfully 'copied' '1' item(s) to the new location."
        self.assertTrue(any(expected_message in str(m) for m in messages), "Copy success message missing.")
        
        # Assert 4: Session Check (Operation is cleared *from the session* pop)
        self.assertNotIn('file_operation', response.wsgi_request.session)

    def test_07_successful_cut_folder(self, mock_check_access):
        """Tests successful 'cut' (move) operation for a folder."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) # Target Modify = True

        # Arrange 2: Create Source Folder (with nested content) and Destination
        source_folder_name = 'SourceFolder'
        target_folder_name = 'DestinationFolder'
        abs_source_dir = os.path.join(self.abs_working_dir, source_folder_name)
        abs_target_dir = os.path.join(self.abs_working_dir, target_folder_name)
        os.makedirs(abs_source_dir, exist_ok=True)
        os.makedirs(abs_target_dir, exist_ok=True)
        
        abs_nested_file = os.path.join(abs_source_dir, 'nested.txt')
        with open(abs_nested_file, 'w') as f: f.write("Nested content.")

        # Arrange 3: Setup Session
        relative_source_path = f'{self.drive_name}/{self.working_folder_name}/{source_folder_name}'
        encoded_source_path = quote(relative_source_path)
        
        session = self.client.session
        session['file_operation'] = {
            'source_items': [encoded_source_path],
            'type': 'cut',
        }
        session.save()

        # Act: Send POST request to paste
        target_path_param = f'{self.drive_name}/{self.working_folder_name}/{target_folder_name}'
        response = self.client.post(self.paste_url, {
            'target_path': target_path_param,
        })
        
        abs_destination_path = os.path.join(abs_target_dir, source_folder_name)
        abs_nested_destination_path = os.path.join(abs_destination_path, 'nested.txt')

        # Assert 1: Redirection
        expected_redirect = reverse('drives:drive_content', kwargs={'path': quote(target_path_param)})
        self.assertRedirects(response, expected_redirect, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Source must be gone, Destination must exist)
        self.assertFalse(os.path.exists(abs_source_dir), "Source folder should be gone (Cut operation).")
        self.assertTrue(os.path.exists(abs_destination_path), "Destination folder must exist.")
        self.assertTrue(os.path.exists(abs_nested_destination_path), "Nested content must be moved.")
        
        # Assert 3: Message Check (Success)
        messages = list(get_messages(response.wsgi_request))
        expected_message = "Successfully 'moved' '1' item(s) to the new location."
        self.assertTrue(any(expected_message in str(m) for m in messages), "Cut success message missing.")
        
        # Assert 4: Session Check (Operation MUST be cleared on success)
        self.assertNotIn('file_operation', response.wsgi_request.session)
        
    def test_08_paste_fails_on_target_permission_denial(self, mock_check_access):
        """Tests that paste is blocked if the user lacks modify permission on the target directory."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, False) # Target Modify = False

        # Arrange 2: Create source file (it should not move/copy)
        source_file_name = 'source.txt'
        abs_source_path = os.path.join(self.abs_working_dir, source_file_name)
        with open(abs_source_path, 'w') as f: f.write("Content.")

        # Arrange 3: Setup Session
        relative_source_path = f'{self.drive_name}/{self.working_folder_name}/{source_file_name}'
        encoded_source_path = quote(relative_source_path)
        
        file_operation_data = { # Store this to check if it's restored
            'source_items': [encoded_source_path],
            'type': 'copy',
        }
        session = self.client.session
        session['file_operation'] = file_operation_data
        session.save()

        # Act: Send POST request to paste
        target_path_param = f'{self.drive_name}/{self.working_folder_name}' # Target is the working directory
        response = self.client.post(self.paste_url, {
            'target_path': target_path_param,
        })
        
        # Assert 1: Redirection
        expected_redirect = reverse('drives:drive_content', kwargs={'path': quote(target_path_param)})
        self.assertRedirects(response, expected_redirect, status_code=302, target_status_code=200)

        # Assert 2: File System Check (Source must remain, Destination does not exist)
        self.assertTrue(os.path.exists(abs_source_path), "Source file should still exist.")
        
        # Assert 3: Message Check (Error)
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        self.assertTrue(any('Permission denied: You cannot modify content in the target location.' in msg for msg in error_messages), 
                        "Permission denied error message missing.")
        
        # Assert 4: Session Check (Operation MUST be restored on permission failure)
        self.assertEqual(response.wsgi_request.session.get('file_operation'), file_operation_data)

    def test_09_paste_skips_item_due_to_conflict_and_continues(self, mock_check_access):
        """Tests that a conflicting item is skipped, a warning is issued, but other items are processed."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Setup Source and Target
        target_folder_name = 'destination'
        abs_target_dir = os.path.join(self.abs_working_dir, target_folder_name)
        os.makedirs(abs_target_dir, exist_ok=True)
        
        # Item 1: Conflict Item (already exists in target)
        conflict_name = 'existing.txt'
        abs_conflict_source = os.path.join(self.abs_working_dir, conflict_name)
        abs_conflict_target = os.path.join(abs_target_dir, conflict_name)
        
        with open(abs_conflict_source, 'w') as f: f.write("Source version.")
        with open(abs_conflict_target, 'w') as f: f.write("Existing target version.") # CONFLICT!

        # Item 2: Successful Item
        success_name = 'new_file.txt'
        abs_success_source = os.path.join(self.abs_working_dir, success_name)
        abs_success_target = os.path.join(abs_target_dir, success_name)
        with open(abs_success_source, 'w') as f: f.write("New file content.")

        # Arrange 3: Setup Session (Copy both items)
        relative_source_conflict = f'{self.drive_name}/{self.working_folder_name}/{conflict_name}'
        relative_source_success = f'{self.drive_name}/{self.working_folder_name}/{success_name}'
        
        session = self.client.session
        session['file_operation'] = {
            'source_items': [quote(relative_source_conflict), quote(relative_source_success)],
            'type': 'copy',
        }
        session.save()

        # Act: Send POST request to paste
        target_path_param = f'{self.drive_name}/{self.working_folder_name}/{target_folder_name}'
        response = self.client.post(self.paste_url, {'target_path': target_path_param})
        
        # Assert 1: File System Check
        self.assertTrue(os.path.exists(abs_conflict_target), "Conflicting target should still exist.")
        self.assertTrue(os.path.exists(abs_success_target), "Successful item must be copied.")

        # Assert 2: Message Check (Warning for mixed result)
        messages = list(get_messages(response.wsgi_request))
        
        # Check for the skip warning for the conflicting item
        skip_warning = f"Skipped: Item named '{conflict_name}' already exists in the target location."
        self.assertTrue(any(skip_warning in str(m) for m in messages), "Conflict skip warning missing.")
        
        # Check for the final mixed success/fail warning
        mixed_warning = "'1' item(s) successfully 'copied', but '1' item(s) failed."
        self.assertTrue(any(mixed_warning in str(m) for m in messages), "Mixed success/fail warning missing.")

    def test_10_paste_skips_recursive_move(self, mock_check_access):
        """Tests that moving a folder into its own subdirectory is correctly skipped."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Setup Source and Target (Target is a subdirectory of Source)
        source_folder_name = 'ParentFolder'
        target_folder_name = 'ParentFolder/ChildFolder'
        
        abs_source_dir = os.path.join(self.abs_working_dir, source_folder_name)
        abs_target_dir = os.path.join(self.abs_working_dir, target_folder_name)
        os.makedirs(abs_target_dir, exist_ok=True) # Creates ParentFolder/ChildFolder

        # Arrange 3: Setup Session (Attempt to cut ParentFolder into ChildFolder)
        relative_source_path = f'{self.drive_name}/{self.working_folder_name}/{source_folder_name}'
        encoded_source_path = quote(relative_source_path)
        
        session = self.client.session
        session['file_operation'] = {
            'source_items': [encoded_source_path],
            'type': 'cut',
        }
        session.save()

        # Act: Send POST request to paste
        target_path_param = f'{self.drive_name}/{self.working_folder_name}/{target_folder_name}'
        response = self.client.post(self.paste_url, {'target_path': target_path_param})
        
        # Assert 1: File System Check (Source must remain, operation must not occur)
        self.assertTrue(os.path.isdir(abs_source_dir), "Source folder should remain intact (recursive block).")
        
        # Assert 2: Message Check (Warning for recursive skip)
        messages = list(get_messages(response.wsgi_request))
        
        recursive_warning = f"Skipped: Cannot move/copy '{source_folder_name}' into itself or its sub-directory."
        self.assertTrue(any(recursive_warning in str(m) for m in messages), "Recursive skip warning missing.")
        
        # Assert 3: Final failure message
        final_failure = "The operation failed for all selected items."
        self.assertTrue(any(final_failure in str(m) for m in messages), "Final failure message missing.")
        
        # Assert 4: Session Check (Operation cleared on full failure)
        self.assertNotIn('file_operation', response.wsgi_request.session)

    # ====================================================================
    # BULK DELETE TESTS
    # ====================================================================

    def test_11_successful_bulk_delete(self, mock_check_access):
        """Tests successful bulk deletion of a file and a folder."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create multiple items
        file_to_delete_name = 'bulk_file.txt'
        folder_to_delete_name = 'bulk_folder'
        abs_file_path = os.path.join(self.abs_working_dir, file_to_delete_name)
        abs_folder_path = os.path.join(self.abs_working_dir, folder_to_delete_name)
        
        with open(abs_file_path, 'w') as f: f.write("Content.")
        os.makedirs(abs_folder_path)

        # Arrange 3: Setup payload
        relative_file_path = f'{self.drive_name}/{self.working_folder_name}/{file_to_delete_name}'
        relative_folder_path = f'{self.drive_name}/{self.working_folder_name}/{folder_to_delete_name}'
        
        items_paths_string = f'{quote(relative_file_path)},{quote(relative_folder_path)}'
        
        # Act: Send the POST request
        response = self.client.post(self.bulk_delete_url, {
            'items_ids': items_paths_string,
            'current_path': self.parent_path_param,
        })

        # Assert 1: Redirection
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check
        self.assertFalse(os.path.exists(abs_file_path), "File should be deleted.")
        self.assertFalse(os.path.exists(abs_folder_path), "Folder should be deleted.")
        
        # Assert 3: Message Check
        messages = list(get_messages(response.wsgi_request))
        expected_message = "Successfully deleted '2' item(s)."
        self.assertTrue(any(expected_message in str(m) for m in messages), "Bulk success message missing.")

    def test_12_bulk_delete_fails_on_permission_denial(self, mock_check_access):
        """Tests that bulk deletion is blocked when user lacks modify permission on the parent directory."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, False) # View=True, Modify=False on parent

        # Arrange 2: Create items (should remain after attempt)
        file_name = 'file.txt'
        abs_file_path = os.path.join(self.abs_working_dir, file_name)
        with open(abs_file_path, 'w') as f: f.write("Content.")
        
        relative_file_path = f'{self.drive_name}/{self.working_folder_name}/{file_name}'
        items_paths_string = quote(relative_file_path)
        
        # Act: Send the POST request
        response = self.client.post(self.bulk_delete_url, {
            'items_ids': items_paths_string,
            'current_path': self.parent_path_param,
        })

        # Assert 1: Redirection
        self.assertRedirects(response, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert 2: File System Check
        self.assertTrue(os.path.exists(abs_file_path), "File should NOT be deleted due to permission denial.")
        
        # Assert 3: Message Check
        messages = list(get_messages(response.wsgi_request))
        error_messages = [str(m) for m in messages if m.level_tag == 'error']
        
        self.assertTrue(any('Permission denied: You cannot modify content in this location.' in msg for msg in error_messages), 
                        "Permission denied error message was not found.")

    def test_13_bulk_delete_mixed_success_and_failure(self, mock_check_access):
        """Tests scenario where one item is deleted and another fails (e.g., item not found)."""
        
        # Arrange 1: Setup Permissions
        mock_check_access.return_value = (True, True) 

        # Arrange 2: Create a successful item and define a non-existent item
        success_name = 'to_delete.txt'
        fail_name = 'non_existent.doc'
        abs_success_path = os.path.join(self.abs_working_dir, success_name)
        
        with open(abs_success_path, 'w') as f: f.write("Content.")

        # Arrange 3: Setup payload
        relative_success_path = f'{self.drive_name}/{self.working_folder_name}/{success_name}'
        relative_fail_path = f'{self.drive_name}/{self.working_folder_name}/{fail_name}' # This path doesn't exist
        
        items_paths_string = f'{quote(relative_success_path)},{quote(relative_fail_path)}'
        
        # Act: Send the POST request
        response = self.client.post(self.bulk_delete_url, {
            'items_ids': items_paths_string,
            'current_path': self.parent_path_param,
        })

        # Assert 1: File System Check
        self.assertFalse(os.path.exists(abs_success_path), "Successful item should be deleted.")
        
        # Assert 2: Message Check (Should have both success and error)
        messages = list(get_messages(response.wsgi_request))
        
        success_message = "Successfully deleted '1' item(s)."
        failure_message = f"Failed to delete the following items: '{fail_name}'"
        
        self.assertTrue(any(success_message in str(m) for m in messages), "Success message missing.")
        self.assertTrue(any(failure_message in str(m) for m in messages), "Failure message missing.")

    # ====================================================================
    # BULK COPY/CUT TESTS (Session Management)
    # ====================================================================

    def test_14_bulk_copy_items_success(self, mock_check_access):
        """Tests that selected items are correctly stored in the session for a 'copy' operation."""
        mock_check_access.return_value = (True, True)
        
        # Arrange: Setup two mock paths
        item1_path = f'{self.drive_name}/file1.txt'
        item2_path = f'{self.drive_name}/folder2/'
        encoded_items = [quote(item1_path), quote(item2_path)]
        
        # Act: Send POST request
        response = self.client.post(self.bulk_copy_url, {
            'selected_items': encoded_items,
            'current_path': self.drive_name,
        })
        
        # Assert 1: Redirection
        expected_redirect = reverse('drives:drive_content', kwargs={'path': quote(self.drive_name)})
        self.assertRedirects(response, expected_redirect, status_code=302, target_status_code=200)

        # Assert 2: Session Check
        session_op = response.wsgi_request.session.get('file_operation')
        self.assertIsNotNone(session_op)
        self.assertEqual(session_op['type'], 'copy')
        self.assertEqual(session_op['source_items'], encoded_items)
        self.assertEqual(session_op['source_path'], self.drive_name)
        
        # Assert 3: Message Check
        messages = list(get_messages(response.wsgi_request))
        expected_message = "Ready to copy '2' item(s). Navigate to the target folder and click Paste."
        self.assertTrue(any(expected_message in str(m) for m in messages), "Info message missing.")

    def test_15_bulk_cut_items_success(self, mock_check_access):
        """Tests that selected items are correctly stored in the session for a 'cut' operation."""
        mock_check_access.return_value = (True, True)

        # Arrange: Setup two mock paths
        item1_path = f'{self.drive_name}/file1.txt'
        item2_path = f'{self.drive_name}/folder2/'
        encoded_items = [quote(item1_path), quote(item2_path)]
        
        # Act: Send POST request
        response = self.client.post(self.bulk_cut_url, {
            'selected_items': encoded_items,
            'current_path': self.drive_name,
        })
        
        # Assert 1: Redirection
        expected_redirect = reverse('drives:drive_content', kwargs={'path': quote(self.drive_name)})
        self.assertRedirects(response, expected_redirect, status_code=302, target_status_code=200)

        # Assert 2: Session Check
        session_op = response.wsgi_request.session.get('file_operation')
        self.assertIsNotNone(session_op)
        self.assertEqual(session_op['type'], 'cut')
        self.assertEqual(session_op['source_items'], encoded_items)
        self.assertEqual(session_op['source_path'], self.drive_name)
        
        # Assert 3: Message Check
        messages = list(get_messages(response.wsgi_request))
        expected_message = "Ready to move '2' item(s). Navigate to the target folder and click Paste."
        self.assertTrue(any(expected_message in str(m) for m in messages), "Info message missing.")

    def test_16_bulk_copy_cut_no_selection_warning(self, mock_check_access):
        """Tests that bulk copy/cut redirects and gives a warning if no items are selected."""
        mock_check_access.return_value = (True, True)

        # Act 1: Test Bulk Copy with no selection
        response_copy = self.client.post(self.bulk_copy_url, {
            'selected_items': [],
            'current_path': self.parent_path_param,
        })
        
        # Assert Copy 1: Redirection
        self.assertRedirects(response_copy, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert Copy 2: Message Check
        messages_copy = list(get_messages(response_copy.wsgi_request))
        expected_warning = "No items were selected for copying."
        self.assertTrue(any(expected_warning in str(m) for m in messages_copy), "Copy no selection warning missing.")
        self.assertNotIn('file_operation', response_copy.wsgi_request.session)

        # Act 2: Test Bulk Cut with no selection
        response_cut = self.client.post(self.bulk_cut_url, {
            'selected_items': [],
            'current_path': self.parent_path_param,
        })
        
        # Assert Cut 1: Redirection
        self.assertRedirects(response_cut, self.expected_redirect_url, status_code=302, target_status_code=200)

        # Assert Cut 2: Message Check
        messages_cut = list(get_messages(response_cut.wsgi_request))
        expected_warning_cut = "No items were selected for cutting."
        self.assertTrue(any(expected_warning_cut in str(m) for m in messages_cut), "Cut no selection warning missing.")
        self.assertNotIn('file_operation', response_cut.wsgi_request.session)