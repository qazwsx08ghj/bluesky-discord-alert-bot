import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Secrets the bot under test holds; they must never reach the browser. The demo server's by default
 * (config/testing/demo_server.py); config/tests_docker.py passes the Docker test install's in E2E_SECRETS. */
export const SECRETS = process.env.E2E_SECRETS
  ? process.env.E2E_SECRETS.split(",").filter(Boolean)
  : ["demo-demo-demo-demo", "wrong-wrong-wrong-wrong", "demo-token-announcements", "demo-token-art-drops"];

export async function reset(request: APIRequestContext, scenario: "empty" | "working" | "problem") {
  const res = await request.get(`/__demo__/reset/${scenario}/`);
  expect(res.ok(), "the demo server's reset address answers").toBe(true);
}

/** What the fake Discord received: the post, and the message text and allowed_mentions sent with it. */
export type DiscordMessage = {
  handle: string;
  text: string;
  channel_token: string;
  content?: string;
  allowed_mentions?: { parse: string[]; roles?: string[] };
};

export async function discordMessages(request: APIRequestContext): Promise<DiscordMessage[]> {
  return (await (await request.get("/__demo__/discord/")).json()).messages;
}

/** Collects console errors (including CSP violations) and every API answer, for checks at the end. */
export function watch(page: Page) {
  const problems: string[] = [];
  const bodies: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(String(e)));
  page.on("response", async (r) => {
    if (r.url().includes("/api/")) bodies.push(await r.text().catch(() => ""));
  });
  return {
    problems,
    async expectClean() {
      expect(problems.filter((p) => !p.includes("status of 409")), "no errors or CSP violations in the console").toEqual([]);
      const html = await page.content();
      for (const secret of SECRETS) {
        expect(html, "no secret in the page").not.toContain(secret);
        for (const body of bodies) expect(body, "no secret in any API answer").not.toContain(secret);
      }
    },
  };
}

export async function expectNoSideways(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "the page doesn't scroll sideways").toBeLessThanOrEqual(0);
}
