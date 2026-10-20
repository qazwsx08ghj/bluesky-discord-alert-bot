from django.contrib import admin
from .models import DiscordWebhook


@admin.register(DiscordWebhook)
class DiscordWebhookAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "is_default", "created_at"]
