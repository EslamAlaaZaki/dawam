import type { Workspace } from "../api/workspaces";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";

export interface Details {
  name: string;
  description: string;
  domain: string;
}

const labelClasses = "flex flex-col gap-1.5 text-sm font-medium";

/** The name, description and business domain inputs of a Workspace form. */
export function DetailsFields({ initial }: { initial?: Workspace }) {
  return (
    <>
      <label className={labelClasses}>
        Name
        <Input name="name" required maxLength={200} defaultValue={initial?.name} />
      </label>
      <label className={labelClasses}>
        Description
        <Textarea
          name="description"
          rows={3}
          maxLength={4000}
          defaultValue={initial?.description}
        />
      </label>
      <label className={labelClasses}>
        Business domain
        <Input name="domain" maxLength={200} defaultValue={initial?.domain} />
      </label>
    </>
  );
}

export function readDetails(form: HTMLFormElement): Details {
  const data = new FormData(form);
  return {
    name: String(data.get("name") ?? ""),
    description: String(data.get("description") ?? ""),
    domain: String(data.get("domain") ?? ""),
  };
}
