// The optional Discord role mention on an account's channel. The bot checks the same rules
// (discord_alerts/mentions.py); this copy only lets the form say what's wrong while typing.

export const HOW_TO_COPY_ROLE_ID = [
  "In Discord, open User Settings > Advanced and turn on Developer Mode.",
  "Open your server's Server Settings > Roles.",
  "Click … next to the role and choose Copy Role ID.",
  "Paste it here. Leave the field empty to send without mentioning anyone.",
];

const NOT_A_ROLE_ID = "A Role ID is a long number, like 123456789012345678. See “How do I find a Role ID?” below.";
const EVERYONE = "@everyone and @here can't be mentioned. Enter the ID of a role instead, or leave this empty.";

/** The Role ID to save ("" = no mention), or the problem with what was typed. */
export function checkRoleId(typed: string): { value: string; error?: string } {
  const text = typed.trim();
  if (!text) return { value: "" };
  if (["everyone", "here"].includes(text.toLowerCase().replace(/^@/, ""))) return { value: text, error: EVERYONE };
  const pasted = /^<@&([0-9]+)>$/.exec(text);
  const id = pasted ? pasted[1] : text;
  if (!/^[0-9]{17,20}$/.test(id) || BigInt(id) > 2n ** 64n - 1n) return { value: text, error: NOT_A_ROLE_ID };
  return { value: id };
}
