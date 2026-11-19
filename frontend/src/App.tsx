import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";
import MyClaims from "./pages/MyClaims";
import SubmitClaim from "./pages/SubmitClaim";
import Queue from "./pages/Queue";
import ClaimDetail from "./pages/ClaimDetail";
import ClaimantClaim from "./pages/ClaimantClaim";
import Dashboard from "./pages/Dashboard";

export default function App() {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div className="grid h-screen place-items-center text-slate-500">
        <Spinner />
      </div>
    );
  if (!user)
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );

  const claimant = user.role === "claimant";
  return (
    <Layout>
      <Routes>
        <Route path="/" element={claimant ? <MyClaims /> : <Queue />} />
        <Route path="/claims/:id" element={claimant ? <ClaimantClaim /> : <ClaimDetail />} />
        {claimant && <Route path="/submit" element={<SubmitClaim />} />}
        {!claimant && <Route path="/dashboard" element={<Dashboard />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
