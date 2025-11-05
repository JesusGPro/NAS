from django.urls import path, re_path
from . import views

app_name = 'drives'

urlpatterns = [
    path('', views.index, name='index'),
    path('disk-status', views.disk_status, name='disk_status'),
    # File content explorer (uses a catch-all path for file browsing)
    re_path(r'^content/(?P<path>.*)$', views.drive_content, name='drive_content'),
    
    # File upload handler
    path('upload/', views.upload_file, name='upload_file'),
    path('rename/', views.rename_item, name='rename_item'),
    path('delete/', views.delete_item, name='delete_item'),
    path('root/', views.get_root_redirect, name='drive_root'),
    # New url for folder download
    re_path(r'^download/(?P<path>.*)$', views.download_folder, name='download_folder'),
    # New URL for folder creation
    path('create-folder/', views.create_folder, name='create_folder'),
    # New URLs for Cut/Paste
    # path('copy/', views.copy_item, name='copy_item'),
    # path('cut/', views.cut_item, name='cut_item'),
    path('bulk-delete/', views.bulk_delete_items, name='bulk_delete_items'),   
    path('bulk-cut/', views.bulk_cut_items, name='bulk_cut_items'),         
    path('bulk-copy/', views.bulk_copy_items, name='bulk_copy_items'),
    path('paste/', views.paste_item, name='paste_item'),
    path('upload-folder/', views.dropzone_upload, name='dropzone_upload'),
    # Managing compressed files
    path('compress/', views.compress_items_view, name='compress_items'),
    path('uncompress/', views.uncompress_item_view, name='uncompress_item'),
]