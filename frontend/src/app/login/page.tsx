import { Suspense } from "react";

import { LoginPage } from "../../auth/LoginPage";

export default function Login() {
  // LoginPage reads `?from=`, which Next.js wants inside a Suspense boundary.
  return (
    <Suspense>
      <LoginPage />
    </Suspense>
  );
}
