import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes, Providers, makeQueryClient } from "../App";
import type { HttpHandler } from "msw";
import { TOKEN, emptyState, handlersFor, type FakeState } from "./fakeApi";
import { server } from "./server";

/** Render the whole control panel at `path`, backed by a fresh fake API. `before`: handlers that
 *  answer first (e.g. to slow a response down), falling through to the fake API if they return nothing. */
export function renderApp(
  path: string,
  state: FakeState = emptyState(),
  { withCookie = true, before = [] as HttpHandler[] } = {},
) {
  if (withCookie) document.cookie = `csrftoken=${TOKEN}; path=/`;
  server.use(...before, ...handlersFor(state));
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false, staleTime: 0 } });
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Providers client={client}>
        <AppRoutes />
      </Providers>
    </MemoryRouter>,
  );
  return { ...view, user, state, client };
}

/** The changes the panel sent (method and path), in order. */
export function changes(state: FakeState) {
  return state.requests.map((r) => `${r.method} ${r.path}`);
}
