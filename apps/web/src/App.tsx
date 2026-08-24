import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import AppShell from "@/components/AppShell";
import AskPage from "@/pages/AskPage";
import ChargebackPage from "@/pages/ChargebackPage";
import CoveragePage from "@/pages/CoveragePage";
import ExecutivePage from "@/pages/ExecutivePage";
import HealthPage from "@/pages/HealthPage";
import StatusPage from "@/pages/StatusPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<ExecutivePage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/chargeback" element={<ChargebackPage />} />
          <Route path="/coverage" element={<CoveragePage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/status" element={<StatusPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}
