import type { ReactNode } from "react";

/** A card for one single configuration: its title, what it does, the controls, and its save row. */
export function SettingsCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="flex max-w-xl flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
    >
      <div>
        <h3 className="text-md font-semibold tracking-[-0.005em]">{title}</h3>
        {description && <p className="mt-0.5 text-sm text-sand-11">{description}</p>}
      </div>
      {children}
      {footer && <div className="flex items-center gap-2">{footer}</div>}
    </section>
  );
}
