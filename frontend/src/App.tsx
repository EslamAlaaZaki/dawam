import { Navigate, Route, Routes } from "react-router";

import { HomePage } from "./HomePage";
import { Layout } from "./Layout";
import { LoginPage } from "./auth/LoginPage";
import { RequireSignIn } from "./auth/RequireSignIn";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireSignIn />}>
          <Route index element={<HomePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
