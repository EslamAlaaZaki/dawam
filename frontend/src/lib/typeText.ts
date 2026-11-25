import type { DwColumn } from "../api/dwModel";

/** A Data Warehouse column's type as text: `string(40)`, `decimal(10,2)`, `integer`. */
export function typeText(type: DwColumn["data_type"]): string {
  if (type.length != null) {
    return `${type.type}(${type.length})`;
  }
  if (type.precision != null) {
    return `${type.type}(${type.precision}${type.scale != null ? `,${type.scale}` : ""})`;
  }
  return type.type;
}
