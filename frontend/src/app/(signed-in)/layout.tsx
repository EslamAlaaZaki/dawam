import { Suspense, type ReactNode } from "react";

import { Loading, RequireSignIn } from "../../auth/RequireSignIn";

/** Every page in this group is for signed-in users only. */
export default function SignedInLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Loading />}>
      <RequireSignIn>{children}</RequireSignIn>
    </Suspense>
  );
}
