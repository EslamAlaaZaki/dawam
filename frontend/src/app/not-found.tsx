import { Suspense } from "react";

import { Loading, RequireSignIn } from "../auth/RequireSignIn";
import { GoHome } from "../shell/GoHome";

/** Any path without a page (a 404): signed-in users go home, anyone else signs in first. */
export default function NotFound() {
  return (
    <Suspense fallback={<Loading />}>
      <RequireSignIn>
        <GoHome />
      </RequireSignIn>
    </Suspense>
  );
}
