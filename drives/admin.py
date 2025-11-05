from django.contrib import admin

from .models import HardDrive

# Option 1: Simple registration (less visual control in the list view)
# admin.site.register(HardDrive)

# Option 2: Enhanced registration using ModelAdmin (Recommended)
# This allows you to customize how the model appears and behaves in the admin site.
@admin.register(HardDrive)
class HardDriveAdmin(admin.ModelAdmin):
    # Fields to display in the list view on the admin site
    list_display = ('name', 'total_size_gb', 'used_size_gb', 'is_online')

    # Fields to allow filtering on the right sidebar
    list_filter = ('is_online',)

    # Fields to allow searching
    search_fields = ('name',)

    # Fields that can be edited directly from the list view
    list_editable = ('is_online',)

    # Automatically set 'total_size_gb' to the sum of all used sizes
    # (Note: This is just an example of customization, you might not need it)
    # def save_model(self, request, obj, form, change):
    #     # Example: Add some custom logic before saving
    #     super().save_model(request, obj, form, change)
    pass

# Note: The @admin.register(HardDrive) decorator is a concise way
# to call admin.site.register(HardDrive, HardDriveAdmin)