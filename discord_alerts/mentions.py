"""Optional Discord role mentions: checking a Role ID, and the safe message fields for it.

A notification mentions at most one role, and only one the user typed in explicitly:
- the message text is just `<@&ROLE_ID>` (the post itself stays in the embed, where
  Discord never turns anything into a ping);
- `allowed_mentions` lets Discord ping that one role and nothing else. `parse` is always
  empty, so @everyone, @here, users and other roles are never pinged, whatever the text says.

What can't be checked with only a webhook: whether the role exists in that server, whether
it can be mentioned (Discord's "Allow anyone to @mention this role"), and who is in it.
A webhook has no access to the server's roles. What can be checked: the format, and that the
ID isn't the server's own ID, which Discord also uses as the @everyone role's ID. The server
ID comes from Discord's description of the webhook (see sender.lookup_webhook).
"""
import re

ROLE_ID = re.compile(r"^[0-9]{17,20}$")
PASTED_MENTION = re.compile(r"^<@&([0-9]+)>$")
MAX_SNOWFLAKE = 2 ** 64 - 1

HOW_TO_COPY = ("In Discord, turn on Developer Mode (User Settings > Advanced), then open Server Settings > Roles, "
               "click … next to the role and choose Copy Role ID.")
NOT_A_ROLE_ID = "A Role ID is a long number, like 123456789012345678. " + HOW_TO_COPY
EVERYONE_REFUSED = ("@everyone and @here can't be mentioned. Enter the ID of a role instead, "
                    "or leave this empty to send without a mention.")
SERVER_ID_REFUSED = ("This is the Discord server's ID, which Discord also uses for @everyone. "
                     "Enter the ID of a role instead. " + HOW_TO_COPY)


class InvalidRoleId(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def clean_role_id(value) -> str:
    """The Role ID to store: "" (no mention) or the digits. Raises InvalidRoleId.

    Accepts the ID itself, or the `<@&ID>` form Discord shows for a role mention.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().lstrip("@") in ("everyone", "here"):
        raise InvalidRoleId(EVERYONE_REFUSED)
    pasted = PASTED_MENTION.match(text)
    if pasted:
        text = pasted.group(1)
    if not ROLE_ID.match(text) or int(text) > MAX_SNOWFLAKE:
        raise InvalidRoleId(NOT_A_ROLE_ID)
    return text


def is_server_id(role_id: str, guild_id: str) -> bool:
    return bool(role_id and guild_id and role_id == guild_id)


def message_fields(role_id: str) -> dict:
    """The message text and allowed_mentions for a notification (role_id "" = no mention)."""
    if not role_id:
        return {"allowed_mentions": {"parse": []}}
    if not ROLE_ID.match(role_id):  # stored values are checked; this is a last guard
        raise InvalidRoleId(NOT_A_ROLE_ID)
    return {"content": f"<@&{role_id}>", "allowed_mentions": {"parse": [], "roles": [role_id]}}
