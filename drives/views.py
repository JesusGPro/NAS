from django.shortcuts import render, redirect, reverse
from django.http import FileResponse, Http404, HttpResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext as _
from django.core.exceptions import PermissionDenied
from urllib.parse import unquote, quote
import psutil
import uuid
import logging
import mimetypes
import os
import platform
from datetime import datetime
import shutil
import tempfile
# for upload folders with js
from django.views.decorators.csrf import csrf_exempt # CRUCIAL for AJAX uploads
from django.http import JsonResponse
import json
import zipfile


def check_access(user, current_path):
    """
    Checks if the user has permission to access (view/modify) the given path.
    Rule: Superuser can do everything. Standard user can only VIEW and MODIFY
    in their dedicated folder (drive_name/username).
    Returns: (Can View: bool, Can Modify: bool)
    """
    if user.is_superuser:
        return True, True # Superusers can do everything

    def normalize_to_posix(p):
        """Converts system path separators to POSIX '/' for consistent comparison."""
        # Use os.path.normpath to clean the path, then replace the system separator with '/'
        return os.path.normpath(p).replace(os.sep, '/')

    # 1. Handle NAS Root View
    try:
        # Get the path relative to the NAS root using system-native separators (os.sep)
        relative_path = os.path.relpath(current_path, settings.NAS_DRIVE_ROOT)
        
        # If the path is '.', it means the user is viewing the root of the NAS
        if relative_path == '.':
            return True, False 

        drive_name = relative_path.split(os.sep)[0]
    except ValueError:
        return False, False
    
    # 2. Check general access and user permission
    permission = settings.DRIVE_PERMISSIONS.get(drive_name)
    username = user.username

    # Deny if: drive not configured OR user not in allowed list
    if not permission or not permission.get('allowed_users') or username not in permission['allowed_users']:
        return False, False
    
    # --- Enforce the "Dedicated Folder Only" Rule for Standard Users ---
    
    # Calculate the ONLY path where this user is allowed full access
    expected_full_access_path = os.path.join(drive_name, username)

    # Convert both paths to POSIX style for comparison
    normalized_relative_path = normalize_to_posix(relative_path)
    normalized_expected_path = normalize_to_posix(expected_full_access_path)
    
    # A. Check if the current relative path is AT or INSIDE the user's dedicated folder
    
    # 1. AT the folder: e.g., 'HardDrive-3/Francisco' == 'HardDrive-3/Francisco'
    if normalized_relative_path == normalized_expected_path:
        return True, True
    
    # 2. INSIDE the folder:
    # Check if the current path starts with the dedicated path + a separator ('/')
    prefix_with_sep = normalized_expected_path + '/'

    if normalized_relative_path.startswith(prefix_with_sep):
        return True, True
    
    # B. Check if the user is asking about the drive link itself (e.g., HardDrive-3)
    if normalized_relative_path == normalize_to_posix(drive_name):
        return True, False 
        
    # C. All other paths (on an allowed drive but outside their folder) -> Deny access
    return False, False

@login_required
def create_folder(request):
    """
    Handles the creation of a new folder, reporting the absolute path for debugging.
    """
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # 1. Get data and resolve paths
    new_folder_name = request.POST.get('folder_name', '').strip()
    target_path_encoded = request.POST.get('target_path', '')
    
    # Decode the current directory path
    root_path = settings.NAS_DRIVE_ROOT
    decoded_target_path = unquote(target_path_encoded)
    
    # Get the absolute path of the current directory
    current_abs_path = os.path.abspath(os.path.join(root_path, decoded_target_path))
    new_folder_abs_path = os.path.join(current_abs_path, new_folder_name)
    
    # 2. Validation (Basic checks remain)
    if not new_folder_name or '..' in new_folder_name or new_folder_name.startswith('.'):
        messages.error(request, _("Invalid folder name."))
        return redirect('drives:drive_content', path=target_path_encoded)
        
    # Security Check
    if not new_folder_abs_path.startswith(root_path):
        messages.error(request, _("Operation denied: Path traversal detected."))
        return redirect('drives:drive_content', path=target_path_encoded)

    # 3. PERMISSION CHECK RE-ENABLED
    can_view, can_modify = check_access(request.user, current_abs_path)
    
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot create a folder in this location."))
        return redirect('drives:drive_content', path=target_path_encoded)

    # 4. Security Check
    if not new_folder_abs_path.startswith(root_path):
        messages.error(request, _("Operation denied: Path traversal detected."))
        return redirect('drives:drive_content', path=target_path_encoded)

    # 5. Creation (Bypassing permission check for now)
    try:
        if os.path.exists(new_folder_abs_path):
            messages.error(request, _("A folder named '%(name)s' already exists.") % {'name': new_folder_name})
            return redirect('drives:drive_content', path=target_path_encoded)
            
        os.makedirs(new_folder_abs_path) 
        
        # --- DEBUG SUCCESS MESSAGE ---
        # Report the exact path used to confirm the issue
        success_msg = _("Folder '%(name)s' created successfully at: '%(path)s'") % {'name': new_folder_name, 'path': new_folder_abs_path}
        messages.success(request, success_msg)
        
    except Exception as e:
        messages.error(request, _("Failed to create folder: '%(e)s'") % {'e': e})

    return redirect('drives:drive_content', path=target_path_encoded)

@login_required
def download_folder(request, path):
    """
    Zips the specified folder and streams it back to the user for download.
    """
    root_path = settings.NAS_DRIVE_ROOT
    decoded_path = unquote(path)
    abs_folder_path = os.path.join(root_path, decoded_path)
    folder_name = os.path.basename(abs_folder_path)

    # 1. Security & Permission Check
    # Ensure the user has permission to view this folder before allowing download
    can_view, can_modify = check_access(request.user, abs_folder_path)
    
    if not can_view:
        messages.error(request, _("Permission denied: You cannot download this folder."))
        # Redirect back to the parent directory or root
        parent_path = os.path.dirname(path)
        return redirect('drives:drive_content', path=parent_path)
        
    # Ensure the path is a directory and is within the root
    if not os.path.isdir(abs_folder_path) or not abs_folder_path.startswith(root_path):
        messages.error(request, _("Invalid folder path or item does not exist."))
        return redirect('drives:drive_root')

    # 2. Create Temporary ZIP Archive
    # Use a temporary directory to store the ZIP file safely
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create the full path for the zip file (e.g., /tmp/someid/Francisco)
        # shutil.make_archive creates 'Francisco.zip' in temp_dir
        zip_base_name = os.path.join(temp_dir, folder_name)
        
        # 'zip' is the format, zip_base_name is the output prefix, abs_folder_path is the source directory
        archive_path = shutil.make_archive(
            base_name=zip_base_name,
            format='zip',
            root_dir=os.path.dirname(abs_folder_path), # Start archive from the parent directory
            base_dir=folder_name                     # Archive only the target folder
        )

        # 3. Stream the File to the User
        try:
            # Open the generated ZIP file
            response = FileResponse(open(archive_path, 'rb'), as_attachment=True)
            
            # Set the download file name
            download_name = f'{folder_name}.zip'
            response['Content-Disposition'] = f'attachment; filename="{download_name}"'
            
            # The FileResponse stream automatically closes the file and deletes the temp dir/file
            # after the response is sent, thanks to Python's 'with' statement and Django's handling.
            return response

        except Exception as e:
            messages.error(request, _("Failed to create or stream the download: '%(e)s'") % {'e': e})
            parent_path = os.path.dirname(path)
            return redirect('drives:drive_content', path=parent_path)

    # 4. Cleanup (Handled automatically by tempfile.TemporaryDirectory)
    # The temporary ZIP file and directory are automatically removed when the 'with' block exits.
    



    return redirect('drives:drive_content', path=target_path_encoded)

@csrf_exempt
@login_required
def dropzone_upload(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Must be POST request'}, status=405)

    try:        
        target_path_encoded = request.POST.get('target_path', '')
        file_relative_path = request.POST.get('relative_path', '') 
        uploaded_file = request.FILES.get('file') 

        if not uploaded_file:
            return JsonResponse({'error': 'No file received.'}, status=400)

        root_path = settings.NAS_DRIVE_ROOT
        decoded_target_path = unquote(target_path_encoded)
        current_abs_path = os.path.abspath(os.path.join(root_path, decoded_target_path))
       
        final_file_path = os.path.join(current_abs_path, file_relative_path)
        final_dir_path = os.path.dirname(final_file_path)
        
        # Check permissions on the target directory (current_abs_path)
        can_view, can_modify = check_access(request.user, current_abs_path)
        if not can_modify:
             # Dropzone expects a 403 or 400 on error
             return JsonResponse({'error': _("Permission denied.")}, status=403)
        
        # Create ALL necessary intermediate directories recursively
        os.makedirs(final_dir_path, exist_ok=True)
        
        # --- ATOMIC SAVE ---
        # Use UUID to prevent conflicts during concurrent uploads
        temp_file_name = str(uuid.uuid4())
        
        temp_file_path = os.path.join(final_dir_path, temp_file_name + ".tmp")
        
        with open(temp_file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        # Rename the temp file to the final file name
        os.rename(temp_file_path, final_file_path)

        # Dropzone requires a 200 OK response to mark the file as successful
        return JsonResponse({'message': 'File uploaded successfully'}, status=200)

    except Exception as e:
        return JsonResponse({'error': f'Server error: {e}'}, status=500)


def convert_bytes(num_bytes):
    """Utility function to convert bytes to human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def get_disk_stats():
    """
    Gathers detailed disk usage statistics for all partitions mounted
    on the current system using the psutil library.
    """
    disk_data = []

    # psutil.disk_partitions(all=False) returns all mounted partitions
    for partition in psutil.disk_partitions(all=True):
        mountpoint = partition.mountpoint
        # --- Cross-Platform Filtering Logic ---
        # 1. Skip system/special/virtual filesystems (common on Linux/WSL)
        if partition.fstype in ('tmpfs', 'devtmpfs', 'proc', 'sysfs', 'none', 'squashfs', 
                      'fuse.gvfsd-fuse', 'gvfs-fuse-daemon') or \
           mountpoint.startswith(('/snap', '/sys', '/dev', '/proc', '/run/user')):
            continue
        
        try:
            # psutil.disk_usage(path) returns disk usage statistics
            usage = psutil.disk_usage(mountpoint)

            # Check for zero total space, which indicates a virtual/special filesystem
            if usage.total == 0:
                continue

            # Convert to GB (using the utility function for cleaner code)
            total_gb = round(usage.total / (1024 ** 3), 2)
            used_gb = round(usage.used / (1024 ** 3), 2)
            free_gb = round(usage.free / (1024 ** 3), 2)
            
            disk_data.append({
                'device': partition.device,
                'mountpoint': mountpoint,
                'fstype': partition.fstype,
                'total': total_gb,
                'used': used_gb,
                'free': free_gb,
                'percent': usage.percent
            })
            
        except (PermissionError, FileNotFoundError, OSError):
            # These exceptions often occur on virtual/unreadable mountpoints, 
            # or Windows network/system drives. We safely skip them.
            continue
        
    return disk_data

def get_root_redirect(request):
    """
    Redirects the user to the file browsing view for the NAS_DRIVE_ROOT.
    """
    # Simply redirect to the start of the defined 'drive_content' view path, which is the empty path
    return redirect(reverse('drives:drive_content', kwargs={'path': ''}))

@login_required(login_url='login_request')
def index(request):
    # drive_status = HardDrive.objects.all() # Fetch data from DB
    return render(request, 'drives/index.html')

@login_required
def disk_status(request):
    """
    Renders the system disk status page.
    """
    disk_stats = get_disk_stats()
    
    context = {
        'disk_stats': disk_stats,
        'os_name': platform.system(),
        'processor': platform.processor() or platform.machine()
    }
    return render(request, 'drives/disk_status.html', context)


# --- File Explorer View Logic ---
def get_file_info(path, root_path, can_modify):
    """
    Gathers details about a single file or directory for the template.
    Returns a dictionary of file info.
    """
    # Fix UnboundLocalError by explicitly ensuring _ is defined
    from django.utils.translation import gettext as _ 

    is_dir = os.path.isdir(path)
    
    # Calculate the relative path used for URL routing
    relative_path = os.path.relpath(path, root_path)
    
    # Ensure relative path is empty string if it's the root directory
    if relative_path == '.':
        relative_path = ''
    
    try:
        stat = os.stat(path)
        
        # Get last modified date
        last_modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
        
        # Determine size display
        size_display = '-'
        if not is_dir:
            size_display = convert_bytes(stat.st_size)

    except Exception:
        # Fallback for inaccessible files
        size_display = _('Error')
        last_modified = _('N/A')

    return {
        # The 'name' is the base name of the file/folder
        'name': os.path.basename(path) if relative_path else os.path.basename(root_path) or _('Root'), 
        # URL-safe encoded path for use in templates/URLs
        'path': quote(relative_path), 
        'is_dir': is_dir,
        'size': size_display,
        'last_modified': last_modified,
        'can_modify': can_modify, # Pass the modification status to the item
    }

@login_required
def drive_content(request, path=''):
    """
    Allows browsing of the drive content and handling of file downloads.
    """
    # --- START FIX ---
    # FIX 1: Explicitly define _ to solve UnboundLocalError in views.py functions 
    from django.utils.translation import gettext as _ 

    # FIX 2: Handle 'None' string path gracefully (often passed from broken redirects)
    if path == 'None':
        messages.error(request, _("Invalid path provided. Redirected to root."))
        return redirect('drives:drive_content', path='')
    # --- END FIX ---

    root_path = settings.NAS_DRIVE_ROOT
    
    # --- Path Resolution ---
    decoded_path = unquote(path)
    absolute_path = os.path.abspath(os.path.join(root_path, decoded_path))
    
    # 1. Security Check: Ensure the requested path is within the allowed root
    if not absolute_path.startswith(root_path):
        raise PermissionDenied(_("Access outside of the designated root directory is forbidden.")) # Changed to use _()
    
    # 2. Permission Check: Check if the user can even view this path
    can_view, can_modify = check_access(request.user, absolute_path)

    # --- Calculate Parent Path for Breadcrumb ---
    parent_path_encoded = None
    if decoded_path and decoded_path != '.' and decoded_path != os.path.basename(root_path):
        parent_path = os.path.dirname(decoded_path)
        if parent_path in ('', '.', '\\'):
            parent_path = ''
        parent_path_encoded = quote(parent_path)
    
    if not can_view:
        messages.error(request, _("Access denied: You do not have permission to view this content."))
        # Redirect to the previous directory or the safe root.
        return redirect('drives:drive_content', path=parent_path_encoded if parent_path_encoded else '')

    # --- Handle File Download (GET request for a file) ---
    if os.path.isfile(absolute_path):
        # Determine MIME type for the response header
        mime_type, _ = mimetypes.guess_type(absolute_path)
        if mime_type is None:
            mime_type = 'application/octet-stream'

        try:
            # Use FileResponse for efficient file serving
            response = FileResponse(open(absolute_path, 'rb'), content_type=mime_type)
            # Set header to force download with original file name
            file_name = os.path.basename(absolute_path)
            response['Content-Disposition'] = f'attachment; filename="{file_name}"'
            return response
        except FileNotFoundError:
            raise Http404(_("File not found"))
        except Exception:
            messages.error(request, _("Could not download file."))
            return redirect('drives:drive_content', path=path)

    # --- Handle Directory Browsing (GET request for a directory) ---
    if os.path.isdir(absolute_path):
        
        # Determine the current view state for filtering logic
        is_root_view = (absolute_path == root_path)
        # Check if we are at the root of a drive (i.e., immediate child of NAS_DRIVE_ROOT)
        is_at_drive_root = (os.path.dirname(absolute_path) == root_path) and not is_root_view

        try:
            # 1. Get all items (folders and files), filter out hidden files/folders (starting with '.')
            file_names = [f for f in os.listdir(absolute_path) if not f.startswith('.')]

            # 2. Filter the list based on specific access rules
            final_list = []
            
            for name in file_names:
                
                # A. If at the NAS root (/media/jesus), only show allowed drives
                if is_root_view:
                    item_abs_path = os.path.join(absolute_path, name)
                    drive_access, _ = check_access(request.user, item_abs_path)
                    if drive_access or request.user.is_superuser:
                        final_list.append(name)

                # B. If inside the root of ANY drive (e.g., /media/jesus/HardDrive-3)
                elif is_at_drive_root:
                    # Dynamically get permission details for the current drive being browsed
                    drive_name = os.path.basename(absolute_path) # e.g., 'HardDrive-1'
                    permission = settings.DRIVE_PERMISSIONS.get(drive_name, {})

                    # B1. If dedicated, only show the user's folder
                    if permission.get('dedicated_folder'):
                        if name == request.user.username or request.user.is_superuser:
                            final_list.append(name)
                    # B2. If NOT dedicated (shared), show ALL contents
                    else:
                        final_list.append(name)

                # C. In any other subdirectory (inside a drive, but not its root)
                else:
                    # Permission granted for parent folder, list all contents
                    final_list.append(name)
                
            # 3. Build item info and separate Dirs/Files
            items = []
            dirs = []
            files = []

            for name in final_list:
                item_abs_path = os.path.join(absolute_path, name)

                # can_modify is the permission status of the current folder, applied to all items within it
                item_info = get_file_info(item_abs_path, root_path, can_modify)

                if item_info['is_dir']:
                    dirs.append(item_info)
                else:
                    files.append(item_info)
            
            # Sort alphabetically (folders and files separatelly)
            dirs.sort(key=lambda x: x['name'].lower())
            files.sort(key=lambda x: x['name'].lower())

            items.extend(dirs)
            items.extend(files)

        except PermissionError:
            messages.error(request, _("Permission denied to access this directory."))
            # Redirect to the parent directory on permission error
            return redirect('drives:drive_content', path=parent_path_encoded if parent_path_encoded else '')
        except Exception as e:
            # CORRECTED: Use translation placeholder for error message
            messages.error(request, _("Error listing directory content: '%(e)s'") % {'e': e})
            items = [] # Clear items on error

        # --- Context Update ---
        context = {
            'current_path': decoded_path,         # Path for display
            'current_path_encoded': path,         # Path for form submissions
            'parent_path_encoded': parent_path_encoded, 
            'items': items,
            'current_folder_name': os.path.basename(absolute_path) or _('Root'),
            'can_modify': can_modify, # Pass modify status for upload button
            'file_operation': request.session.get('file_operation') # ADDED to support template logic
        }
        return render(request, 'drives/drive_content.html', context)

    # --- Handle Path Not Found ---
    raise Http404(_("Path not found"))


# --- File Upload Logic ---

@login_required
def upload_file(request):
    """Handles file uploads."""
    if request.method == 'POST':
        # Assuming the form has an input named 'file' and a hidden input 'target_path'
        uploaded_file = request.FILES.get('file_upload') # Fixed name to match template
        target_path = request.POST.get('target_path', '')


        # Resolve path using NAS_DRIVE_ROOT consistently
        root_path = settings.NAS_DRIVE_ROOT
        decoded_path = unquote(target_path)
        upload_dir = os.path.abspath(os.path.join(root_path, decoded_path))

        # Check if the user has permission to modify the target directory
        can_view, can_modify = check_access(request.user, upload_dir)
        if not can_modify:
            messages.error(request, _("Permission denied: You cannot modify content in this location."))
            # Redirect to the safe, highest-level root.
            return redirect('drives:drive_content', path=target_path)
        
        # Security check: Ensure upload path is within allowed project bounds
        if not upload_dir.startswith(root_path):
            messages.error(request, _("Invalid upload path: Attempted to upload outside the drive boundary."))
            return redirect('drives:drive_content', path=target_path)

        if uploaded_file and os.path.isdir(upload_dir):
            destination_path = os.path.join(upload_dir, uploaded_file.name)
            
            # Save the file to the destination
            try:
                with open(destination_path, 'wb+') as destination:
                    for chunk in uploaded_file.chunks():
                        destination.write(chunk)
                # CORRECTED: Use translation placeholder for uploaded file name
                messages.success(request, _("File '%(file_name)s' uploaded successfully.") % {'file_name': uploaded_file.name})
            except Exception as e:
                # CORRECTED: Use translation placeholder for error message
                messages.error(request, _("Failed to save file: '%(e)s'") % {'e': e})
            
            return redirect('drives:drive_content', path=target_path)
        else:
            messages.error(request, _("Upload failed: Target directory is invalid or file is missing."))
        
    # If not POST or if upload failed, redirect to a safe page 
    return redirect('drives:drive_content', path='') # Redirect to root if upload view is accessed directly

# --- File Operations (Rename/Delete) ---

@login_required
def rename_item(request):
    """Handles renaming a file or folder."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # 1. Get necessary data and ensure basic security
    encoded_old_path = request.POST.get('old_path')
    new_name = request.POST.get('new_name', '').strip()
    current_dir_url = os.path.dirname(encoded_old_path).replace('\\', '/')
    
    if not new_name:
        messages.error(request, _("New name cannot be empty."))
        return redirect('drives:drive_content', path=current_dir_url)

    # 2. Resolve paths
    root_path = settings.NAS_DRIVE_ROOT
   
    decoded_old_path = unquote(encoded_old_path)
    abs_old_path = os.path.abspath(os.path.join(root_path, decoded_old_path))
    abs_new_path = os.path.join(os.path.dirname(abs_old_path), new_name)

    # Check permission on the parent directory (where the rename operation happens)
    parent_dir = os.path.dirname(abs_old_path)
    can_view, can_modify = check_access(request.user, parent_dir)
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in this location."))
        return redirect('drives:drive_content', path=current_dir_url)

    # 3. Final Security Checks
    if '..' in new_name or new_name.startswith('.'):
        messages.error(request, _("New name contains illegal characters (e.g., '..' or starts with '.')."))
        return redirect('drives:drive_content', path=current_dir_url)
    if not abs_new_path.startswith(root_path):
        messages.error(request, _("Target path is outside the allowed drive boundary."))
        return redirect('drives:drive_content', path=current_dir_url)
    if os.path.exists(abs_new_path):
        # CORRECTED: Use translation placeholder for existing item name
        messages.error(request, _("An item named '%(new_name)s' already exists in this location.") % {'new_name': new_name})
        return redirect('drives:drive_content', path=current_dir_url)

    # 4. Perform the rename operation
    try:
        old_name = os.path.basename(abs_old_path)
        os.rename(abs_old_path, abs_new_path)
        # CORRECTED: Use translation placeholders for old and new names
        messages.success(request, _("Item successfully renamed from '%(old_name)s' to '%(new_name)s'.") % {'old_name': old_name, 'new_name': new_name})
    except Exception as e:
        # CORRECTED: Use translation placeholder for error message
        messages.error(request, _("Failed to rename item: '%(e)s'") % {'e': e})

    return redirect('drives:drive_content', path=current_dir_url)

@login_required
def delete_item(request):
    """Handles deleting a file or folder."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # 1. Get necessary data and resolve paths
    encoded_target_path = request.POST.get('target_path')
    current_dir_url = os.path.dirname(encoded_target_path).replace('\\', '/')

    root_path = settings.NAS_DRIVE_ROOT
    
    decoded_target_path = unquote(encoded_target_path)
    abs_target_path = os.path.abspath(os.path.join(root_path, decoded_target_path))

    # Check permission on the parent directory(where the deletion operation happens)
    parent_dir = os.path.dirname(abs_target_path)
    can_view, can_modify = check_access(request.user, parent_dir)
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in this location."))
        return redirect('drives:drive_content', path=current_dir_url)

    # Security Check: Ensure the target path is within the allowed root and not itself
    if not abs_target_path.startswith(root_path) or abs_target_path == root_path:
        messages.error(request, _("Deletion of the root directory or outside boundary is not allowed."))
        return redirect('drives:drive_content', path=current_dir_url)

    # 3. Perform Deletion
    try:
        item_name = os.path.basename(abs_target_path)
        
        if os.path.isfile(abs_target_path):
            os.remove(abs_target_path)
            # CORRECTED: Use translation placeholder for deleted file name
            messages.success(request, _("File '%(item_name)s' deleted successfully.") % {'item_name': item_name})
        elif os.path.isdir(abs_target_path):
            # Use shutil.rmtree to recursively delete the directory and its contents
            shutil.rmtree(abs_target_path)
            # CORRECTED: Use translation placeholder for deleted folder name
            messages.success(request, _("Folder '%(item_name)s' and its contents deleted successfully.") % {'item_name': item_name})
        else:
            # CORRECTED: Use translation placeholder for path
            messages.warning(request, _("Item at '%(path)s' does not exist or is not a file/directory.") % {'path': decoded_target_path})
            
    except PermissionError:
        # CORRECTED: Use translation placeholder for item name
        messages.error(request, _("Permission denied: Could not delete '%(item_name)s'.") % {'item_name': item_name})
    except Exception as e:
        # CORRECTED: Use translation placeholder for error message
        messages.error(request, _("Failed to delete item: '%(e)s'") % {'e': e})

    return redirect('drives:drive_content', path=current_dir_url)

@login_required
def bulk_delete_items(request):
    """Handles bulk deletion of selected files/folders. Reads items from items_ids hidden field."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # Get the comma-separated string of paths from the hidden input field
    item_paths_string = request.POST.get('items_ids', '')
    current_path = request.POST.get('current_path', '')
    
    # CRITICAL GUARDRAIL: SPLIT the string and check if the list is valid
    selected_items = [p for p in item_paths_string.split(',') if p.strip()]

    # If the list is empty (i.e., nothing was selected) -> STOP DELETION!
    if not selected_items:
        messages.warning(request, _("No items were selected for deletion."))
        return redirect('drives:drive_content', path=current_path)

    # 1. Check permission on the current directory (where the deletion is initiated)
    root_path = settings.NAS_DRIVE_ROOT
    decoded_current_path = unquote(current_path)
    abs_current_dir = os.path.abspath(os.path.join(root_path, decoded_current_path))
    
    can_view, can_modify = check_access(request.user, abs_current_dir)
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in this location."))
        return redirect('drives:drive_content', path=current_path)

    deleted_count = 0
    failed_items = []
    
    # 2. Iterate and delete each selected item
    for encoded_item_path in selected_items:
        try:
            # Item path is already URL-quoted (from template)
            decoded_item_path = unquote(encoded_item_path)
            abs_item_path = os.path.abspath(os.path.join(root_path, decoded_item_path))
            item_name = os.path.basename(abs_item_path)
            
            # Security Check: Ensure the item is within the allowed root and not the root itself
            if not abs_item_path.startswith(root_path) or abs_item_path == root_path:
                 failed_items.append(item_name)
                 continue

            if os.path.isfile(abs_item_path):
                os.remove(abs_item_path)
                deleted_count += 1
            elif os.path.isdir(abs_item_path):
                shutil.rmtree(abs_item_path)
                deleted_count += 1
            else:
                failed_items.append(item_name) # Item not found
                
        except PermissionError:
            failed_items.append(item_name)
        except Exception:
            failed_items.append(item_name)

    # 3. Post-deletion Feedback
    if deleted_count > 0:
        messages.success(request, _("Successfully deleted '%(count)s' item(s).") % {'count': deleted_count})
        
    if failed_items:
        failed_names = ", ".join(failed_items[:5]) # Show first 5 failed items
        messages.error(request, _("Failed to delete the following items: '%(names)s'") % {'names': failed_names})
        
    return redirect('drives:drive_content', path=current_path)

@login_required
def bulk_copy_items(request):
    """Stores selected items in the session for a copy operation."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # Items are passed as checkbox values (multiple 'selected_items' fields)
    selected_items = request.POST.getlist('selected_items')
    current_path = request.POST.get('current_path', '')
    
    if not selected_items:
        messages.warning(request, _("No items were selected for copying."))
        return redirect('drives:drive_content', path=current_path)

    # Store the operation details in the session
    request.session['file_operation'] = {
        'type': 'copy',
        'source_items': selected_items,
        'source_path': current_path,
    }
    
    messages.info(request, _("Ready to copy '%(count)s' item(s). Navigate to the target folder and click Paste.") % {'count': len(selected_items)})
    return redirect('drives:drive_content', path=current_path)

@login_required
def bulk_cut_items(request):
    """Stores selected items in the session for a cut (move) operation."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')

    # Items are passed as checkbox values (multiple 'selected_items' fields)
    selected_items = request.POST.getlist('selected_items')
    current_path = request.POST.get('current_path', '')

    if not selected_items:
        messages.warning(request, _("No items were selected for cutting."))
        return redirect('drives:drive_content', path=current_path)

    # Store the operation details in the session
    request.session['file_operation'] = {
        'type': 'cut',
        'source_items': selected_items,
        'source_path': current_path,
    }
    
    messages.info(request, _("Ready to move '%(count)s' item(s). Navigate to the target folder and click Paste.") % {'count': len(selected_items)})
    return redirect('drives:drive_content', path=current_path)

@login_required
def paste_item(request):
    """Performs the copy or move operation using items stored in the session."""
    if request.method != 'POST':
        return redirect('drives:drive_content', path='')
        
    # Retrieve and clear the operation from the session
    file_operation = request.session.pop('file_operation', None)
    target_path = request.POST.get('target_path', '') # The path where the paste button was clicked

    # --- 1. Validation and Setup ---
    if not file_operation or not file_operation['source_items']:
        messages.error(request, _("No items were selected for paste operation or session expired."))
        return redirect('drives:drive_content', path=target_path)
    
    source_items = file_operation['source_items']
    operation_type = file_operation['type']
    
    root_path = settings.NAS_DRIVE_ROOT
    decoded_target_path = unquote(target_path)
    abs_target_dir = os.path.abspath(os.path.join(root_path, decoded_target_path))
    
    # --- 2. Target Permission Check ---
    can_view, can_modify = check_access(request.user, abs_target_dir)
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in the target location."))
        # Restore operation to session since the paste failed
        request.session['file_operation'] = file_operation
        return redirect('drives:drive_content', path=target_path)

    # --- 3. Process Items ---
    success_count = 0
    fail_count = 0
    
    for encoded_source_path in source_items:
        try:
            # The source path is relative to the NAS_DRIVE_ROOT
            decoded_source_path = unquote(encoded_source_path)
            abs_source_path = os.path.abspath(os.path.join(root_path, decoded_source_path))
            item_name = os.path.basename(abs_source_path)
            abs_destination_path = os.path.join(abs_target_dir, item_name)
            
            # Prevent pasting an item into itself or its subdirectory (e.g., copying FolderA into FolderA/SubDir)
            if abs_target_dir.startswith(abs_source_path):
                 messages.warning(request, _("Skipped: Cannot move/copy '%(name)s' into itself or its sub-directory.") % {'name': item_name})
                 fail_count += 1
                 continue
                 
            # Security Check: Source must be within the allowed root
            if not abs_source_path.startswith(root_path):
                 fail_count += 1
                 continue
            
            # Check for existing file/folder conflict
            if os.path.exists(abs_destination_path):
                 messages.warning(request, _("Skipped: Item named '%(name)s' already exists in the target location.") % {'name': item_name})
                 fail_count += 1
                 continue

            if operation_type == 'copy':
                # shutil.copy2 for files, shutil.copytree for directories
                if os.path.isdir(abs_source_path):
                    shutil.copytree(abs_source_path, abs_destination_path)
                else:
                    shutil.copy2(abs_source_path, abs_destination_path) # copy2 preserves metadata
                success_count += 1
                
            elif operation_type == 'cut':
                # Use shutil.move for cut/move operation
                # Note: This is an atomic move for files on the same filesystem
                shutil.move(abs_source_path, abs_destination_path)
                success_count += 1
                
        except Exception:
            fail_count += 1

    # --- 4. Final Feedback ---
    op_name = _('copied') if operation_type == 'copy' else _('moved')
    
    if success_count > 0:
        if fail_count == 0:
            messages.success(request, _("Successfully '%(operation)s' '%(count)s' item(s) to the new location.") % {'operation': op_name, 'count': success_count})
        else:
            messages.warning(request, _("'%(success_count)s' item(s) successfully '%(operation)s', but '%(fail_count)s' item(s) failed.") % {'success_count': success_count, 'operation': op_name, 'fail_count': fail_count})
    elif fail_count > 0:
        messages.error(request, _("The operation failed for all selected items."))
    
    # If the operation was a 'cut', clear the session flag permanently upon success
    if 'file_operation' in request.session and operation_type == 'cut' and success_count > 0:
        del request.session['file_operation']
    
    # If there was a failure on a 'cut', we might want to restore the session for retry, 
    # but since we already did that at the start, we'll ensure it's clean on full failure.
    if 'file_operation' in request.session and success_count == 0:
         del request.session['file_operation'] # Clear on full failure to avoid confusing sticky paste button

    return redirect('drives:drive_content', path=target_path)


@login_required
def compress_items_view(request):
    from django.utils.translation import gettext as _
    """
    Handles compressing selected files/folders into a ZIP archive.
    """
    if request.method != 'POST':
        # You should redirect to the drive content view, not drive_root
        return redirect('drives:drive_content', path='') 

    current_path = request.POST.get('current_path', '')
    item_paths_str = request.POST.get('item_paths', '').strip()
    item_paths = [p.strip() for p in item_paths_str.split(',') if p.strip()]

    # --- DEBUG PRINT: START (Mantenido para el flujo de trabajo) ---
    print("------------------------------------------------------------------")
    print(f"DEBUG (Compress View): Received current_path: '{current_path}'")
    print(f"DEBUG (Compress View): Received item_paths_str (raw): '{item_paths_str}'")
    print(f"DEBUG (Compress View): Processed item_paths (list): {item_paths}")
    print("------------------------------------------------------------------")
    # --- DEBUG PRINT: END ---

    if not item_paths:
        messages.warning(request, _("No valid files or folders were selected for compression."))
        return redirect('drives:drive_content', path=quote(current_path))

    # Get user access and root
    try:
        can_view, can_modify = check_access(request.user, current_path)
    except NameError:
        messages.error(request, _("Internal Error: Permission check not available."))
        return redirect('drives:drive_content', path=quote(current_path))
        
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in this location."))
        return redirect('drives:drive_content', path=quote(current_path))
    
    # Define the output zip file name and path
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # If compressing only one item, use its name for the ZIP file (more user-friendly)
    if len(item_paths) == 1:
        decoded_base_name = os.path.basename(unquote(item_paths[0]))
        zip_filename = f"{decoded_base_name}.zip"
    else:
        zip_filename = f"Compressed_folder_{now_str}.zip"
    
    # Absolute path for the zip file (in the current directory)
    abs_output_path = os.path.join(settings.NAS_DRIVE_ROOT, current_path, zip_filename)

    total_files_added_to_zip = 0 

    try:
        with zipfile.ZipFile(abs_output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item_path in item_paths:
                
                # --- FIX: URL-DECODE THE PATH ---
                decoded_item_path = unquote(item_path) 
                abs_item_path = os.path.join(settings.NAS_DRIVE_ROOT, decoded_item_path)
                
                # --- DEBUG PRINT: Decoded Path Check (Mantenido) ---
                print(f"DEBUG (Compress Logic): Checking decoded path: '{decoded_item_path}'")
                
                if not os.path.exists(abs_item_path):
                    print(f"DEBUG (Compress Logic): Path NOT found on disk: '{abs_item_path}'")
                    continue

                print(f"DEBUG (Compress Logic): Path found: '{abs_item_path}'. Proceeding to compress.")
                
                # Define the path component to be removed from the start of the item's path
                # Esto asegura que el ZIP contenga el nombre del archivo/carpeta seleccionado en la raíz.
                base_dir = os.path.dirname(abs_item_path) 
                
                if os.path.isdir(abs_item_path):
                    # --- Folder Compression Logic ---
                    for foldername, subfolders, filenames in os.walk(abs_item_path):
                        # 1. Add the current directory path
                        # El arcname relativo a la carpeta padre (base_dir)
                        arcname = os.path.relpath(foldername, base_dir)
                        if arcname:
                            zipf.write(foldername, arcname=arcname)
                            
                        # 2. Add files within the directory
                        for filename in filenames:
                            file_path = os.path.join(foldername, filename)
                            arcname = os.path.relpath(file_path, base_dir)
                            zipf.write(file_path, arcname=arcname)
                            total_files_added_to_zip += 1 
                else:
                    # --- Single File Compression Logic ---
                    zip_internal_path = os.path.basename(abs_item_path)
                    zipf.write(abs_item_path, arcname=zip_internal_path)
                    total_files_added_to_zip += 1 
        
        
        if total_files_added_to_zip > 0:
            # Usamos el conteo de items seleccionados (len(item_paths)) para el mensaje de éxito.
            messages.success(request, _("Successfully compressed %(count)s item(s) into '%(filename)s'.") % {'count': len(item_paths), 'filename': zip_filename})
        else:
            # Limpia el archivo zip vacío si no se escribió nada
            if os.path.exists(abs_output_path):
                os.remove(abs_output_path) 
            messages.warning(request, _("No valid files or folders were found to compress."))
            
    except Exception as e:
        # Limpia el archivo si ocurrió un error durante la escritura
        if os.path.exists(abs_output_path):
            os.remove(abs_output_path)
        messages.error(request, _("Compression failed: '%(e)s'") % {'e': str(e)})
        
    return redirect('drives:drive_content', path=quote(current_path))

@login_required
def uncompress_item_view(request):
    """
    Handles uncompressing a ZIP archive.
    """
    if request.method != 'POST':
        return redirect('drives:drive_root')

    current_path = request.POST.get('current_path', '')
    zip_path = request.POST.get('zip_path', '')

    # Check permission
    can_view, can_modify = check_access(request.user, current_path)
    if not can_modify:
        messages.error(request, _("Permission denied: You cannot modify content in this location."))
        return redirect('drives:drive_content', path=quote(current_path))

    # Absolute path setup
    abs_zip_path = os.path.join(settings.NAS_DRIVE_ROOT, zip_path)
    abs_destination_dir = os.path.join(settings.NAS_DRIVE_ROOT, current_path)
    

    # File validity and existence check
    if not os.path.exists(abs_zip_path) or not zip_path.lower().endswith('.zip'):
        messages.error(request, _("The specified file is not a valid ZIP archive or does not exist."))
        return redirect('drives:drive_content', path=quote(current_path))
    
    extracted_count = 0

    try:
        with zipfile.ZipFile(abs_zip_path, 'r') as zipf:
            file_list = zipf.namelist()
            zipf.extractall(abs_destination_dir)
            extracted_count = len(file_list)

        # Success/Warning feedback
        if extracted_count > 0:
            zip_filename = os.path.basename(zip_path)
            # --- Logic to find the extracted folder name ---
            first_item = file_list[0]
            # Get the path component before the first '/' (which is 'Folder_40')
            extracted_folder_name = first_item.split('/')[0]

            if '/' in first_item:
                 success_message = _("Successfully extracted '%(count)s' files/folders from '%(filename)s' into the folder: %(folder_name)s.")
                 messages.success(request, success_message % {
                     'count': extracted_count, 
                     'filename': zip_filename, 
                     'folder_name': extracted_folder_name
                 })

        else:
            messages.warning(request, _("The ZIP file was successfully read but contained no files."))
            
    except PermissionError as e:
        messages.error(request, _("Extraction failed due to **insufficient file system permissions**. The server cannot write to this location."))
    except zipfile.BadZipFile as e:
        messages.error(request, _("Extraction failed: The ZIP file is corrupted or not a valid archive."))
    except Exception as e:
        messages.error(request, _("An unknown error occurred during extraction: %s") % str(e))

    return redirect('drives:drive_content', path=quote(current_path))