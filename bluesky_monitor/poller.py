import logging
from django.conf import settings
from atproto import Client
from .models import MonitoredAccount, SeenPost
from discord_alerts.sender import send_discord_alert

logger = logging.getLogger(__name__)


def _get_client() -> Client:
    client = Client()
    client.login(settings.BLUESKY_HANDLE, settings.BLUESKY_PASSWORD)
    return client


def _post_url(handle: str, uri: str) -> str:
    # uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
    rkey = uri.split("/")[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def poll_account(account: MonitoredAccount) -> int:
    """Fetch recent posts for one account and announce any new ones. Returns count of new posts."""
    try:
        client = _get_client()
        feed = client.get_author_feed(actor=account.handle, limit=10)
    except Exception as exc:
        logger.error("Failed to fetch feed for %s: %s", account.handle, exc)
        return 0

    is_first_poll = not account.first_poll_done
    new_count = 0
    webhook_url = account.discord_webhook_url or settings.DISCORD_WEBHOOK_URL

    for item in reversed(feed.feed):
        post = item.post
        uri = post.uri
        cid = post.cid
        text = getattr(post.record, "text", "")
        url = _post_url(account.handle, uri)

        if SeenPost.objects.filter(post_uri=uri).exists():
            continue

        SeenPost.objects.create(
            account=account,
            post_uri=uri,
            post_cid=cid,
            text=text,
            post_url=url,
        )

        if not is_first_poll and webhook_url:
            send_discord_alert(
                webhook_url=webhook_url,
                handle=account.handle,
                display_name=account.display_name or account.handle,
                text=text,
                post_url=url,
            )
            new_count += 1
            logger.info("Announced new post from %s: %s", account.handle, uri)

    if is_first_poll:
        account.first_poll_done = True
        account.save(update_fields=["first_poll_done"])
        logger.info("Seeded existing posts for %s — no announcements on first poll", account.handle)

    return new_count


def poll_all_active_accounts():
    """Poll every active monitored account. Called by the scheduler."""
    accounts = MonitoredAccount.objects.filter(is_active=True)
    for account in accounts:
        poll_account(account)
