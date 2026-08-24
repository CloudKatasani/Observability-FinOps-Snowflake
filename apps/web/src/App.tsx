import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import StatusPage from "@/pages/StatusPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route path="/status" element={<StatusPage />} />
        <Route path="*" element={<Navigate to="/status" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}
