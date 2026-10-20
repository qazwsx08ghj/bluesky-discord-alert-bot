from django.contrib import admin
from .models import MonitoredAccount, SeenPost


@admin.register(MonitoredAccount)
class MonitoredAccountAdmin(admin.ModelAdmin):
    list_display = ["handle", "display_name", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["handle", "display_name"]


@admin.register(SeenPost)
class SeenPostAdmin(admin.ModelAdmin):
    list_display = ["account", "post_url", "seen_at"]
    list_filter = ["account"]
    readonly_fields = ["post_uri", "post_cid", "text", "post_url", "seen_at"]
