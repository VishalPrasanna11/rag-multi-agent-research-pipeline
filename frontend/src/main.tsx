import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Link, RouterProvider } from "react-router";
import { Toaster } from "sonner";
import "./index.css";
import { Layout } from "./components/Layout";
import { AuthProvider } from "./lib/auth";
import { HttpError } from "./lib/api";
import { CompanyPage } from "./routes/Company";
import { Home, NewRunPage } from "./routes/Home";
import { HistoryPage, SavedPage, SettingsPage } from "./routes/Lists";
import { ReportPage } from "./routes/Report";
import { RunPage } from "./routes/Run";

const qc = new QueryClient({
  defaultOptions: { queries: { retry: (n, e) => n < 2 && !(e instanceof HttpError && e.status < 500), refetchOnWindowFocus: false } },
});

function NotFound() {
  return <div className="flex flex-col gap-2"><h1 className="text-2xl font-semibold">Not found</h1><Link to="/" className="text-brand underline">Back to watchlist</Link></div>;
}

const router = createBrowserRouter([
  { path: "/share/:token", element: <main className="mx-auto max-w-5xl px-4 py-6"><ReportPage shared /></main> },
  {
    element: <Layout />,
    children: [
      { index: true, element: <Home /> },
      { path: "new", element: <NewRunPage /> },
      { path: "run/:runId", element: <RunPage /> },
      { path: "reports/:reportId", element: <ReportPage /> },
      { path: "company/:ticker", element: <CompanyPage /> },
      { path: "history", element: <HistoryPage /> },
      { path: "saved", element: <SavedPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <Tooltip.Provider>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </Tooltip.Provider>
      <Toaster position="bottom-right" richColors={false} />
    </QueryClientProvider>
  </StrictMode>,
);
