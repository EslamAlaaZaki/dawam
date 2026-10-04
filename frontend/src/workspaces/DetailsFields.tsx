import type { Workspace } from "../api/workspaces";

export interface Details {
  name: string;
  description: string;
  domain: string;
}

/** The name, description and business domain inputs of a Workspace form. */
export function DetailsFields({ initial }: { initial?: Workspace }) {
  return (
    <>
      <label>
        Name
        <input name="name" required maxLength={200} defaultValue={initial?.name} />
      </label>
      <label>
        Description
        <textarea name="description" rows={3} maxLength={4000} defaultValue={initial?.description} />
      </label>
      <label>
        Business domain
        <input name="domain" maxLength={200} defaultValue={initial?.domain} />
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
