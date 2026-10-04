import { Suspense, type ReactNode } from "react";

import { RequireSignIn } from "../../auth/RequireSignIn";
import { Loading } from "../../shell/Loading";

/** Every page in this group is for signed-in users only. */
export default function SignedInLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Loading />}>
      <RequireSignIn>{children}</RequireSignIn>
    </Suspense>
  );
}
