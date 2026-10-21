import { setupServer } from "msw/node";

/** One mock server for every test file; each test installs its own fake backend. */
export const server = setupServer();
