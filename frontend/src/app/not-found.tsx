import { Suspense } from "react";

import { RequireSignIn } from "../auth/RequireSignIn";
import { GoHome } from "../shell/GoHome";
import { Loading } from "../shell/Loading";

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
