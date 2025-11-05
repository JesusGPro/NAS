from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns

# URLs that are NOT language-prefixed (system routes)
urlpatterns = [
    path('admin/', admin.site.urls),
    # This provides the necessary set_language view at /i18n/setlang/
    path('i18n/', include('django.conf.urls.i18n')),
    
    # login_nas.urls is language-independent
    path('', include('login_nas.urls')), 
]

# URLs that ARE language-prefixed (Your main application pages)
# This handles the root redirect (e.g., / -> /en/)
urlpatterns += i18n_patterns(
    # ALL main application paths MUST be inside this block.
    # The empty path ('') means /en/ or /es/ routes to the drives app.
    path('', include('drives.urls', namespace='drives')),
)