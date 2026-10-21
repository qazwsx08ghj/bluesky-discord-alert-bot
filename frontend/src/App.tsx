import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { Compass } from "lucide-react";
import { retryPolicy } from "./api/queries";
import { Shell, useShell, useTitle } from "./components/Shell";
import { Empty, ToastProvider } from "./components/ui";
import { AccountDrawerProvider } from "./features/AccountDrawer";
import { AccountsPage } from "./pages/AccountsPage";
import { ActivityPage } from "./pages/ActivityPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 5_000, retry: retryPolicy, refetchOnWindowFocus: true },
      mutations: { retry: false },
    },
  });
}

/** First visit: the setup steps. Afterwards: the accounts. */
function Home() {
  const { status, statusQuery } = useShell();
  if (statusQuery.isPending) return null;
  return <Navigate to={status && !status.setup.complete && status.counts.accounts_total === 0 ? "/setup" : "/accounts"} replace />;
}

function NotFound() {
  useTitle("Not found");
  return (
    <section className="panel">
      <Empty icon={Compass} title="This page doesn't exist" action={<Link className="btn primary" to="/accounts">Go to Accounts</Link>}>
        The address may be mistyped, or the page moved.
      </Empty>
    </section>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function Providers({ client, children }: { client: QueryClient; children: React.ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AccountDrawerProvider>{children}</AccountDrawerProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <BrowserRouter>
      <Providers client={client}>
        <AppRoutes />
      </Providers>
    </BrowserRouter>
  );
}
