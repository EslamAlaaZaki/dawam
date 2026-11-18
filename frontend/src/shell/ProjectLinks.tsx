import type { ReactNode } from "react";
import { Globe } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";

export const GITHUB_URL = "https://github.com/EslamAlaaZaki/dawam";
export const WEBSITE_URL = "https://eslamalaazaki.github.io/dawam/";

/** GitHub's mark; lucide no longer ships brand icons. */
function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
      className={className}
    >
      <path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.05-.71.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z" />
    </svg>
  );
}

const iconLinkClasses =
  "grid size-6 place-items-center rounded-md text-subtle-foreground transition-colors duration-120 outline-primary hover:bg-muted hover:text-foreground focus-visible:outline-2 [&_svg]:size-3.5";

function IconLink({
  href,
  label,
  children,
}: {
  href: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={label}
          className={iconLinkClasses}
        >
          {children}
        </a>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** The project's GitHub repo and website as icons, for the sidebar's footer. */
export function ProjectIconLinks() {
  return (
    <span className="flex shrink-0 gap-0.5">
      <IconLink href={GITHUB_URL} label="Source on GitHub">
        <GithubMark />
      </IconLink>
      <IconLink href={WEBSITE_URL} label="DAWAM website">
        <Globe />
      </IconLink>
    </span>
  );
}

const textLinkClasses =
  "underline decoration-border-strong underline-offset-2 transition-colors duration-120 hover:text-foreground hover:decoration-current";

/** The same two links as words, for the sign-in pages' footer. */
export function ProjectTextLinks() {
  return (
    <>
      <a
        href={GITHUB_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={textLinkClasses}
      >
        GitHub
      </a>
      <span aria-hidden className="mx-1.5 text-border-strong">
        ·
      </span>
      <a
        href={WEBSITE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={textLinkClasses}
      >
        Website
      </a>
    </>
  );
}
