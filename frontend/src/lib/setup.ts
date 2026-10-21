import type { BotStatus, Channel } from "../api/types";

export interface SetupProgress {
  login: boolean;
  channel: boolean;
  tested: boolean;
  account: boolean;
  done: number;
  complete: boolean;
  /** 1-4: the first step that isn't done, or 5 when all are. */
  current: number;
}

/** The four first-run steps. The server decides three; the test message counts as done once any
 * channel has had a successful delivery (a test or a real post), or when the owner skipped it. */
export function setupProgress(status: BotStatus | undefined, channels: Channel[] | undefined, testSkipped = false): SetupProgress {
  const login = Boolean(status?.setup.bluesky_login);
  const channel = Boolean(status?.setup.discord_channel);
  const account = Boolean(status?.setup.accounts);
  const tested =
    Boolean(channels?.some((c) => c.last_delivery?.ok)) || testSkipped || (login && channel && account);
  const steps = [login, channel, tested, account];
  const done = steps.filter(Boolean).length;
  const firstOpen = steps.findIndex((s) => !s);
  return { login, channel, tested, account, done, complete: Boolean(status?.setup.complete), current: firstOpen === -1 ? 5 : firstOpen + 1 };
}
